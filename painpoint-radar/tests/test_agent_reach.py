"""The Agent Reach Reddit collector, driven through the real subprocess path against a fake `agent-reach`.

Envelopes are shaped like Agent Reach 1.6 output over OpenCLI 2.0 rows (search/subreddit rows carry id, subreddit,
score or upvotes, comments, url, created_utc, selftext; `read` carries {"post": ..., "comments": [...]} in `raw`).
"""

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from painpoint import db
from painpoint.cli import main
from painpoint.collect import agent_reach, reddit_api
from painpoint.collect.agent_reach import AgentReach, AgentReachError
from painpoint.collect.common import Post
from painpoint.config import load_config

HERE = Path(__file__).parent
NOW = 1_800_000_000
DAY = 86400
PAIN_A = "I hate doing our invoicing manually every single week, there has to be a better tool for this by now."
PAIN_B = "Same here, I waste hours on it every month and would pay for something that actually works.\nSecond line."
AUTHORS = ("real_op_name", "alice_real", "bob_real")


def row(sid, title, selftext="", sub="r/SaaS", score=10, comments=3, created=NOW - DAY, author="real_op_name"):
    url = f"https://www.reddit.com/{sub}/comments/{sid}/some_slug/"
    raw = {"id": sid, "title": title, "subreddit": sub, "author": author, "score": score, "comments": comments,
           "url": url, "created_utc": created, "selftext": selftext, "post_hint": ""}
    return {"title": title, "url": url, "author": author, "published_at": "2027-01-14T08:00:00Z", "text": None,
            "raw": raw}


def comment(text, depth=0, score=5, author="alice_real"):
    prefix = "  " * depth + "> " if depth else ""
    return {"type": f"L{depth}", "author": author, "score": score,
            "text": "\n".join(prefix + line for line in text.split("\n"))}


MORE = {"type": "L1", "author": "", "score": "", "text": "  [+4 more replies]"}


def read_item(comments):
    post = {"type": "POST", "author": "real_op_name", "score": 10, "text": "Title\n\nBody"}
    merged = "Title\n\nBody" + "".join(f"\n@{c['author']}: {c['text']}" for c in comments)
    return {"title": None, "url": None, "author": "real_op_name", "published_at": None, "text": merged,
            "raw": {"post": post, "comments": comments}}


def ok(items):
    return {"schema_version": 1, "ok": True, "channel": "reddit", "action": "x", "backend": "opencli",
            "items": items, "meta": {"elapsed_ms": 5, "attempts": [{"backend": "opencli", "result": "ok"}]},
            "error": None}


def fail(code, message, hint=""):
    return {"schema_version": 1, "ok": False, "channel": "reddit", "action": "x", "backend": None, "items": [],
            "meta": {"elapsed_ms": 5, "attempts": []}, "error": {"code": code, "message": message, "hint": hint}}


@pytest.fixture
def fake_ar(tmp_path, monkeypatch):
    exe = tmp_path / "agent-reach"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_agent_reach.py"}" "$@"\n')
    exe.chmod(0o755)
    script, log = tmp_path / "ar_script.json", tmp_path / "ar_calls.jsonl"
    monkeypatch.setenv("FAKE_AR_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_AR_LOG", str(log))

    class Fake:
        command = str(exe)

        def respond(self, rules, noise=False):
            script.write_text(json.dumps({"rules": [{"match": m, **r} for m, r in rules], "noise": noise}))

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    fake = Fake()
    fake.respond([])
    return fake


@pytest.fixture
def rcfg():
    c = load_config()["reddit"]
    c["search_queries"] = ['"wish there was"', '"alternative to"']
    return c


# ---------------------------------------------------------------- converters


def test_items_become_the_same_posts_the_praw_collector_stores_without_authors():
    post = agent_reach.item_to_post(row("abc12", "Struggling with invoices every week",
                                        "It takes hours. Mail bob@example.com"), "SaaS")
    assert post.id == post.thread_id == "reddit:t3_abc12" and post.community == "r/SaaS" and post.kind == "post"
    assert post.url == "https://www.reddit.com/r/SaaS/comments/abc12/some_slug/"
    assert (post.upvotes, post.num_comments, post.created_utc) == (10, 3, NOW - DAY)
    assert "[email]" in post.body and "real_op_name" not in repr(post)
    # subreddit listings call the score "upvotes"; subreddit names compare case-insensitively
    listing = row("def34", "A hot post about painful invoicing", sub="r/saas")
    listing["raw"]["upvotes"] = listing["raw"].pop("score")
    assert agent_reach.item_to_post(listing, "SaaS").upvotes == 10
    # site search can stray into other subreddits; rows without an id cannot be synced; empty posts are skipped
    assert agent_reach.item_to_post(row("x1", "A long enough title here", sub="r/Other"), "SaaS") is None
    assert agent_reach.item_to_post(dict(row("x2", "A long enough title here"), raw={}), "SaaS") is None
    assert agent_reach.item_to_post(row("x3", "Short"), "SaaS") is None


def test_an_unchanged_agent_reach_post_is_not_flagged_as_edited_by_the_api_sync():
    post = agent_reach.item_to_post(row("rt", "Struggling with invoices every week",
                                        "It takes hours. Mail bob@example.com or call me."), "SaaS")
    live = NS(fullname="t3_rt", title="Struggling with invoices every week",
              selftext="It takes hours. Mail bob@example.com or call me.", removed_by_category=None)
    stored = {"id": post.id, "kind": post.kind, "title": post.title, "body": post.body}
    assert reddit_api.find_outdated(NS(info=lambda fullnames: iter([live])), [stored]) == ([], [])


def test_comments_are_unindented_filtered_and_never_keep_the_author(rcfg):
    post = agent_reach.item_to_post(row("p1", "Invoicing is painful for us"), "SaaS")
    item = read_item([
        comment(PAIN_A, 0, author="alice_real"),
        comment(PAIN_B, 1, author="bob_real"),
        MORE,                                                              # "[+4 more replies]" stub
        comment("[deleted]", 0, author="[deleted]"),
        comment("lol same", 0),                                            # too short
        comment("This is a long comment with nothing in it but friendly chatter about the weather today.", 0),
        comment(PAIN_A.replace("invoicing", "payroll"), 0, score=0),       # below comment_min_score
    ])
    out = agent_reach.thread_comments(item, post, rcfg)
    assert [c.body for c in out] == [PAIN_A, PAIN_B]                       # "> " reply prefix removed per line
    assert all(c.kind == "comment" and c.thread_id == post.id and c.id.startswith(post.id + ":c") for c in out)
    assert not any(reddit_api.has_fullname(c.id) for c in out)            # the API sync cannot check these
    assert not any(a in repr(out) for a in AUTHORS)
    assert [c.id for c in agent_reach.thread_comments(item, post, rcfg)] == [c.id for c in out]  # stable ids
    assert len(agent_reach.thread_comments(item, post, dict(rcfg, comments_per_post=1))) == 1
    assert agent_reach.thread_comments({"raw": {"type": "POST", "text": "no comments"}}, post, rcfg) == []


# ---------------------------------------------------------------- the subprocess call


def test_call_uses_the_opencli_backend_and_reads_the_envelope_after_warnings(fake_ar):
    fake_ar.respond([(["reddit search"], {"envelope": ok([row("p1", "I wish there was a tool")])})], noise=True)
    items = AgentReach(fake_ar.command, timeout_s=45).call("search", 'subreddit:SaaS "wish there was"', "--limit", 5)
    assert items[0]["raw"]["id"] == "p1"
    call = fake_ar.calls()[0]
    assert call[:5] == ["reddit", "search", 'subreddit:SaaS "wish there was"', "--limit", "5"]
    assert call[call.index("--backend") + 1] == "opencli" and call[call.index("--timeout") + 1] == "45"


def test_empty_results_are_not_errors_but_login_and_blocks_stop_the_run(fake_ar):
    fake_ar.respond([
        (["reddit read"], {"envelope": fail("NOT_FOUND", "nothing found")}),
        (["reddit subreddit"], {"envelope": fail("NEED_LOGIN", "Not logged in", "Run `agent-reach login reddit`")}),
        (["reddit search", "garbage"], {"stdout": "Traceback (most recent call last): boom", "exit": 2}),
    ])
    ar = AgentReach(fake_ar.command)
    assert ar.call("search", "nothing matches") == []                    # Agent Reach's "returned no items"
    assert ar.call("read", "abc") == []
    with pytest.raises(AgentReachError) as e:
        ar.call("subreddit", "SaaS")
    assert e.value.code == "NEED_LOGIN" and e.value.stops_run and "agent-reach login reddit" in e.value.hint
    with pytest.raises(AgentReachError) as e:
        ar.call("search", "garbage")
    assert e.value.code == "UPSTREAM_BROKEN" and not e.value.stops_run and "boom" in e.value.message


def test_a_missing_agent_reach_points_to_the_github_install_not_pypi(tmp_path):
    with pytest.raises(AgentReachError) as e:
        AgentReach(str(tmp_path / "no-such-agent-reach")).call("search", "x")
    assert e.value.code == "NEED_SETUP" and e.value.stops_run
    assert "github.com/Panniantong/Agent-Reach" in e.value.hint and "PyPI" in e.value.hint


# ---------------------------------------------------------------- one subreddit


def test_collect_lists_searches_each_phrase_inside_the_subreddit_and_reads_top_threads(fake_ar, rcfg):
    fake_ar.respond([
        (["reddit subreddit SaaS"], {"envelope": ok([
            row("p1", "Hot post about painful invoicing", score=50, comments=4),
            row("p2", "Another hot post title here", score=5, comments=2),
            row("p0", "A post nobody upvoted at all", score=1)])}),        # below min_score
        (["reddit search", "wish there was"], {"envelope": ok([
            row("p1", "Hot post about painful invoicing", score=50, comments=4),                 # duplicate
            row("old1", "An old post wishing for a tool", created=NOW - 400 * DAY),             # older than a year
            row("p3", "I wish there was a tool for this", score=3, comments=0),
            row("o1", "A post from another subreddit", sub="r/Other")])}),
        (["reddit read p1"], {"envelope": ok([read_item([comment(PAIN_A)])])}),
    ])
    res = agent_reach.collect_subreddit(AgentReach(fake_ar.command), "SaaS", rcfg, {"comment_threads": 1}, now=NOW)
    assert [p.id for p in res.posts if p.kind == "post"] == ["reddit:t3_p1", "reddit:t3_p2", "reddit:t3_p3"]
    [c] = [p for p in res.posts if p.kind == "comment"]
    assert c.thread_id == "reddit:t3_p1" and res.threads_read == {"reddit:t3_p1": {c.id}}
    calls = fake_ar.calls()
    assert [a[1] for a in calls] == ["subreddit", "search", "search", "read"]  # one read: comment_threads=1
    assert [a[2] for a in calls[1:3]] == ['subreddit:SaaS "wish there was"', 'subreddit:SaaS "alternative to"']
    assert calls[3][2] == "p1"                                                # the highest-scoring thread
    assert res.warnings == [] and res.stop is None


def test_a_stop_code_ends_the_run_but_keeps_what_was_gathered(fake_ar, rcfg):
    fake_ar.respond([
        (["reddit subreddit"], {"envelope": ok([row("p1", "Hot post about painful invoicing")])}),
        (["reddit search"], {"envelope": fail("RATE_LIMITED", "HTTP 429")}),
    ])
    res = agent_reach.collect_subreddit(AgentReach(fake_ar.command), "SaaS", rcfg, {"comment_threads": 5}, now=NOW)
    assert res.stop.code == "RATE_LIMITED" and [p.id for p in res.posts] == ["reddit:t3_p1"]
    assert len(fake_ar.calls()) == 2                                          # no further searches, no reads


def test_three_failures_in_a_row_stop_instead_of_hammering_a_broken_channel(fake_ar, rcfg):
    rcfg["search_queries"] = ['"a"', '"b"', '"c"', '"d"']
    fake_ar.respond([
        (["reddit subreddit"], {"envelope": fail("TIMEOUT", "upstream did not answer in time")}),
        (["reddit search"], {"envelope": fail("UPSTREAM_BROKEN", "HTTP 500")}),
    ])
    res = agent_reach.collect_subreddit(AgentReach(fake_ar.command), "SaaS", rcfg, {"comment_threads": 5}, now=NOW)
    assert res.stop is not None and "3 Agent Reach calls failed in a row" in res.stop.message
    assert len(fake_ar.calls()) == 3 and len(res.warnings) == 2


# ---------------------------------------------------------------- CLI


@pytest.fixture
def ar_cli(tmp_path, fake_ar, monkeypatch):
    for k in reddit_api.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({
        "db_path": str(tmp_path / "t.db"),
        "agent_reach": {"command": fake_ar.command},
        "reddit": {"search_queries": ['"wish there was"']},
    }))
    return str(path), str(tmp_path / "t.db")


def stored(path):
    return sqlite3.connect(path).execute("SELECT * FROM posts ORDER BY id").fetchall()


def test_default_config_collects_reddit_through_agent_reach():
    assert load_config()["reddit"]["via"] == "agent_reach"
    assert load_config()["agent_reach"]["command"] == "agent-reach"


def test_collect_reddit_uses_agent_reach_and_warns_that_deletion_sync_needs_api_credentials(ar_cli, fake_ar, capsys):
    cfg, path = ar_cli
    fake_ar.respond([
        (["reddit subreddit SaaS"], {"envelope": ok([row("p1", "Hot post about painful invoicing")])}),
        (["reddit read p1"], {"envelope": ok([read_item([comment(PAIN_A)])])}),
    ])
    main(["--config", cfg, "collect", "reddit", "--subreddit", "SaaS"])
    out = capsys.readouterr().out
    assert "through Agent Reach" in out and "not Reddit's official API" in out
    assert "deletion sync skipped" in out and "r/SaaS: 2 new, 0 already stored" in out
    rows = stored(path)
    assert [r[0] for r in rows][0] == "reddit:t3_p1" and len(rows) == 2
    assert not any(a in repr(rows) for a in AUTHORS)                       # no usernames anywhere in the database


def test_limit_also_caps_the_threads_whose_comments_are_read(ar_cli, fake_ar):
    cfg, _ = ar_cli
    fake_ar.respond([(["reddit subreddit SaaS"], {"envelope": ok(
        [row(f"p{i}", f"Hot post number {i} about invoicing", score=50 - i) for i in range(4)])})])
    main(["--config", cfg, "collect", "reddit", "--subreddit", "SaaS", "--limit", "2"])
    calls = fake_ar.calls()
    assert calls[0][calls[0].index("--limit") + 1] == "2"
    assert [a[2] for a in calls if a[1] == "read"] == ["p0", "p1"]


def test_rereading_a_thread_replaces_comments_deleted_or_edited_on_reddit(ar_cli, fake_ar, capsys):
    cfg, path = ar_cli
    listing = (["reddit subreddit SaaS"], {"envelope": ok([row("p1", "Hot post about painful invoicing")])})
    fake_ar.respond([listing, (["reddit read p1"], {"envelope": ok([read_item([comment(PAIN_A), comment(PAIN_B)])])})])
    main(["--config", cfg, "collect", "reddit", "--subreddit", "SaaS"])
    fake_ar.respond([listing, (["reddit read p1"], {"envelope": ok([read_item([comment(PAIN_A)])])})])
    main(["--config", cfg, "collect", "reddit", "--subreddit", "SaaS"])
    assert "r/SaaS: 0 new, 2 already stored or duplicate, 1 outdated comments removed" in capsys.readouterr().out
    assert sorted(r[7] for r in stored(path) if r[3] == "comment") == [PAIN_A]


def test_deletion_sync_checks_posts_by_id_and_drops_agent_reach_comments_with_their_thread(ar_cli, monkeypatch, capsys):
    cfg, path = ar_cli
    main(["--config", cfg, "init"])
    conn = db.connect(path)
    db.insert_posts(conn, [
        Post("reddit:t3_alive", "reddit", "r/x", "post", "reddit:t3_alive", "u", "Alive post title", "Alive body.", NOW),
        Post("reddit:t3_alive:c111", "reddit", "r/x", "comment", "reddit:t3_alive", "u", "Re: Alive", PAIN_A, NOW),
        Post("reddit:t3_dead", "reddit", "r/x", "post", "reddit:t3_dead", "u", "Dead post title", "Dead body.", NOW),
        Post("reddit:t3_dead:c222", "reddit", "r/x", "comment", "reddit:t3_dead", "u", "Re: Dead", PAIN_B, NOW)])
    conn.close()
    asked = []

    def info(fullnames):
        asked.extend(fullnames)
        return iter([NS(fullname="t3_alive", title="Alive post title", selftext="Alive body.", removed_by_category=None),
                     NS(fullname="t3_dead", title="Dead post title", selftext="[deleted]", removed_by_category="deleted")])

    monkeypatch.setattr(reddit_api, "make_client", lambda: NS(info=info))
    main(["--config", cfg, "purge-deleted"])
    out = capsys.readouterr().out
    assert sorted(asked) == ["t3_alive", "t3_dead"]                        # comment ids are never sent to /api/info
    assert "checked 2 stored Reddit items, removed 1 deleted/removed and 0 edited" in out
    assert "Also removed 1 Agent Reach comments" in out
    assert [r[0] for r in stored(path)] == ["reddit:t3_alive", "reddit:t3_alive:c111"]


def test_collect_runs_the_api_deletion_sync_first_when_credentials_are_set(ar_cli, fake_ar, monkeypatch, capsys):
    cfg, _ = ar_cli
    for k in reddit_api.REQUIRED_ENV:
        monkeypatch.setenv(k, "set")
    monkeypatch.setattr(reddit_api, "make_client", lambda: NS(info=lambda fullnames: iter([])))
    main(["--config", cfg, "collect", "reddit", "--subreddit", "SaaS"])
    out = capsys.readouterr().out
    assert "deletion sync: checked 0 stored Reddit items" in out and "deletion sync skipped" not in out


def test_a_stop_saves_what_was_collected_then_exits_with_the_fix(ar_cli, fake_ar):
    cfg, path = ar_cli
    fake_ar.respond([
        (["reddit subreddit SaaS"], {"envelope": ok([row("p1", "Hot post about painful invoicing", comments=0)])}),
        (["reddit search"], {"envelope": fail("NEED_LOGIN", "Not logged in", "Run `agent-reach login reddit`")}),
    ])
    with pytest.raises(SystemExit) as e:
        main(["--config", cfg, "collect", "reddit", "--subreddit", "SaaS", "--subreddit", "SideProject"])
    assert "NEED_LOGIN" in str(e.value) and "agent-reach login reddit" in str(e.value)
    assert [r[0] for r in stored(path)] == ["reddit:t3_p1"]
    assert not any("SideProject" in " ".join(a) for a in fake_ar.calls())   # the next subreddit was not started


def test_a_broken_channel_points_the_person_to_agent_reachs_checks_not_its_agent_hint(ar_cli, fake_ar):
    cfg, _ = ar_cli
    fake_ar.respond([(["reddit"], {"envelope": fail("UPSTREAM_BROKEN", "could not find the OpenCLI extension release",
                                                    "Tell the user in plain words which platform has a problem")})])
    with pytest.raises(SystemExit) as e:   # 2 calls per subreddit here; the count carries over to the next one
        main(["--config", cfg, "collect", "reddit", "--subreddit", "SaaS", "--subreddit", "SideProject",
              "--subreddit", "software"])
    assert "3 Agent Reach calls failed in a row" in str(e.value) and "agent-reach check reddit --text" in str(e.value)
    assert "Tell the user" not in str(e.value)
    assert [a[2] for a in fake_ar.calls()] == ["SaaS", 'subreddit:SaaS "wish there was"', "SideProject"]
