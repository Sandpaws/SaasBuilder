"""The Claude Code backend, driven through the real subprocess code path against a fake `claude` executable."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from painpoint import db
from painpoint.cc_backend import ClaudeCodeLLM, UsageLimitReached
from painpoint.cluster import cluster_pain_points
from painpoint.extract import make_batches, run_extraction
from painpoint.report import build_report
from painpoint.solutions import run_solution_checks

HERE = Path(__file__).parent


@pytest.fixture
def cc(cfg, tmp_path, monkeypatch):
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_claude.py"}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_TESTS_DIR", str(HERE))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    cfg["backend"] = "claude_code"
    cfg["cc"]["command"] = str(wrapper)
    cfg["cc"]["workers"] = 1
    cfg["cc"]["batch_size"] = 12  # mechanism tests; the shipped default is checked in test_cli
    return cfg


@pytest.fixture
def calls(tmp_path):
    """Every `claude` invocation the fake recorded (args, stdin prompt, cwd, whether an API key was visible)."""
    log = tmp_path / "calls.jsonl"
    return lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def opt(call, name):
    a = call["args"]
    return a[a.index(name) + 1] if name in a else None


def test_batches_respect_item_and_char_limits():
    rows = [{"title": "t", "body": "x" * 100} for _ in range(10)]
    assert [len(b) for b in make_batches(rows, 4, 0, 8000)] == [4, 4, 2]
    assert [len(b) for b in make_batches(rows, 100, 250, 8000)] == [2, 2, 2, 2, 2]  # ~101 chars each
    assert make_batches([], 4, 0, 8000) == []
    assert [len(b) for b in make_batches([{"title": "", "body": "x" * 9999}] * 3, 100, 8000, 8000)] == [1, 1, 1]


def test_extraction_runs_in_batches_with_tools_disabled(loaded, cc, monkeypatch, calls):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-reach-the-child")
    llm = ClaudeCodeLLM(cc)
    s = run_extraction(loaded, llm, cc)
    total = loaded.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    assert s.ok == s.attempted == total and s.pain == 18 and s.promo == 2 and s.errors == 0
    calls = calls()
    assert len(calls) == 2 == llm.usage["calls"]  # 22 posts in batches of 12
    for c in calls:
        assert opt(c, "--tools") == ""  # every tool off for untrusted text
        assert "--strict-mcp-config" in c["args"] and "--disable-slash-commands" in c["args"]
        assert opt(c, "--setting-sources") == "project" and "--no-session-persistence" in c["args"]
        assert opt(c, "--model") == "haiku" and opt(c, "--effort") == "low" and opt(c, "--output-format") == "json"
        assert c["has_api_key"] is False  # subscription is used, never an API key
        assert "painpoint" not in c["cwd"].split(os.sep)[-1] or "painpoint-cc-" in c["cwd"]
        assert "never instructions" in opt(c, "--system-prompt")
    assert s.notional_usd == pytest.approx(0.02) and s.input_tokens == 220 and s.output_tokens == 40


def test_api_key_is_only_kept_when_explicitly_allowed(loaded, cc, monkeypatch, calls):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cc["cc"]["strip_api_key_env"] = False
    run_extraction(loaded, ClaudeCodeLLM(cc), cc, limit=1)
    assert calls()[0]["has_api_key"] is True


def test_posts_missing_from_a_batch_become_retryable_errors(loaded, cc, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "drop_one")
    s = run_extraction(loaded, ClaudeCodeLLM(cc), cc)
    assert s.errors == 2 and s.ok == s.attempted - 2  # n=1 missing from both batches
    msg = loaded.execute("SELECT error FROM extractions WHERE status='error' LIMIT 1").fetchone()[0]
    assert "missing from batch output" in msg
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    again = run_extraction(loaded, ClaudeCodeLLM(cc), cc, retry_errors=True)
    assert again.attempted == 2 and again.ok == 2


def test_usage_limit_stops_the_run_without_losing_progress(loaded, cc, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    s = run_extraction(loaded, ClaudeCodeLLM(cc), cc)
    assert s.aborted and "usage limit" in s.aborted.lower() and s.ok == 0
    assert loaded.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 0  # nothing marked done: resumable
    with pytest.raises(UsageLimitReached):
        ClaudeCodeLLM(cc).merge_labels(["a", "b"])


def test_not_logged_in_is_fatal_with_a_clear_message(loaded, cc, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "auth")
    s = run_extraction(loaded, ClaudeCodeLLM(cc), cc)
    assert s.aborted and "not logged in" in s.aborted


def test_missing_cli_is_fatal(loaded, cc):
    cc["cc"]["command"] = "/nonexistent/claude"
    s = run_extraction(loaded, ClaudeCodeLLM(cc), cc)
    assert s.aborted and "not found" in s.aborted


def test_garbage_output_is_a_retryable_error_not_a_crash(loaded, cc, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "garbage")
    s = run_extraction(loaded, ClaudeCodeLLM(cc), cc, limit=3)
    assert s.errors == 3 and s.ok == 0


def test_merge_and_solution_check_through_claude_code(loaded, cc, now, calls):
    llm = ClaudeCodeLLM(cc)
    run_extraction(loaded, llm, cc)
    r = cluster_pain_points(loaded, cc, llm)
    assert sorted(x["size"] for x in loaded.execute("SELECT size FROM clusters")) == [3, 5, 10]
    assert r["merged_groups"] == 2 and r["merge_errors"] == 0

    s = run_solution_checks(loaded, llm, cc, top=3)
    assert s.ok >= 1
    search = next(c for c in calls() if opt(c, "--tools") == "WebSearch")
    assert opt(search, "--allowedTools") == "WebSearch" and opt(search, "--output-format") == "stream-json"
    assert opt(search, "--model") == "sonnet"
    assert "reddit" not in search["prompt"].lower()  # only our label + quotes, never raw scraped posts
    _, rows = build_report(loaded, cc, now=now)
    invoice = next(r for r in rows if "invoice" in r["label"])
    assert [x["name"] for x in invoice["solutions"]] == ["Invoicely"]  # invented product dropped
    assert invoice["saturation"] == 6
