"""Drive the real CLI end to end (everything that does not need the live API)."""

import json

import pytest
import yaml

from painpoint import db
from painpoint.cli import main
from data import make_posts


@pytest.fixture
def workdir(tmp_path, monkeypatch, now):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"db_path": str(tmp_path / "t.db"), "backend": "api"}))
    data = tmp_path / "posts.jsonl"
    data.write_text("\n".join(
        json.dumps({"id": p.id, "title": p.title, "body": p.body, "community": p.community, "url": p.url,
                    "created_utc": p.created_utc, "score": p.upvotes, "kind": p.kind, "thread_id": p.thread_id})
        for p in make_posts(now)))
    return tmp_path, str(cfg), str(data)


def test_init_import_estimate_stats_and_empty_report(workdir, capsys):
    tmp, cfg, data = workdir
    main(["--config", cfg, "init"])
    main(["--config", cfg, "collect", "import", data, "--source-name", "demo"])
    assert "imported: 22 new, 0 duplicate" in capsys.readouterr().out
    main(["--config", cfg, "collect", "import", data, "--source-name", "demo"])
    assert "0 new, 22 duplicate" in capsys.readouterr().out  # re-import is idempotent

    main(["--config", cfg, "extract", "--dry-run"])
    out = capsys.readouterr().out
    assert "22 posts, model claude-haiku-5-5, rough estimate ~$" in out

    with pytest.raises(SystemExit) as e:  # spend cap refuses before any API call is made
        main(["--config", cfg, "extract", "--max-usd", "0.001"])
    assert "exceeds --max-usd" in str(e.value)

    main(["--config", cfg, "stats"])
    assert "posts" in capsys.readouterr().out

    main(["--config", cfg, "cluster", "--no-merge"])
    assert "0 pain-point mentions" in capsys.readouterr().out
    main(["--config", cfg, "report", "--out", str(tmp / "r.md"), "--csv", str(tmp / "r.csv")])
    assert (tmp / "r.md").exists() and (tmp / "r.csv").exists()


def test_web_collect_requires_matching_source(workdir):
    _, cfg, _ = workdir
    with pytest.raises(SystemExit) as e:
        main(["--config", cfg, "collect", "web"])
    assert "no matching web_sources" in str(e.value)


def test_missing_config_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        main(["--config", str(tmp_path / "nope.yaml"), "stats"])


def test_claude_code_backend_dry_run_says_no_api_charges(workdir, capsys):
    tmp, cfg, data = workdir
    yaml_cfg = yaml.safe_load(open(cfg)); yaml_cfg["backend"] = "claude_code"
    open(cfg, "w").write(yaml.safe_dump(yaml_cfg))
    main(["--config", cfg, "init"])
    main(["--config", cfg, "collect", "import", data])
    capsys.readouterr()
    main(["--config", cfg, "extract", "--dry-run"])
    out = capsys.readouterr().out
    assert "22 posts in ~4 `claude -p` calls" in out and "No API charges" in out


def test_default_config_targets_the_two_given_subreddits_and_haiku():
    from painpoint.config import load_config
    c = load_config()
    assert c["reddit"]["subreddits"] == ["SaaS", "SideProject"]
    assert c["backend"] == "claude_code" and c["cc"]["models"]["extract"] == "haiku" and c["cc"]["batch_size"] == 6
    assert c["models"]["extract"] == "claude-haiku-5-5" and c["llm"]["use_fallbacks"] == "auto"
