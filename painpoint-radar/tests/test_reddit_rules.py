"""Behaviour that follows from Reddit's API guide: identification, deletion sync, search collection, discovery."""

from types import SimpleNamespace as NS

import pytest

from painpoint import db
from painpoint.cli import main
from painpoint.collect import reddit_api
from painpoint.collect.common import Post
from painpoint.prompts import EXTRACT_SYSTEM

NOW = 1_800_000_000


# ---------------------------------------------------------------- User-Agent format


@pytest.mark.parametrize("ua,ok", [
    ("python:com.example.painpoint-radar:v0.1.0 (by /u/some_user)", True),
    ("android:com.example.myredditapp:v1.2.3 (by /u/kemitche)", True),  # Reddit's own example
    ("painpoint-radar by u/yourname", False),
    ("Python/urllib", False),
    ("python:app:v1", False),
    ("", False),
])
def test_user_agent_format_matches_reddits_requirement(ua, ok):
    assert (reddit_api.check_user_agent(ua) is None) is ok
    if not ok:
        assert "(by /u/<username>)" in reddit_api.check_user_agent(ua)


# ---------------------------------------------------------------- deletion sync


def thing(name, **kw):
    return NS(fullname=name, **kw)


def test_find_gone_flags_deleted_removed_and_missing_items():
    items = {
        "t3_ok": thing("t3_ok", selftext="Still here", removed_by_category=None),
        "t3_del": thing("t3_del", selftext="[deleted]", removed_by_category="deleted"),
        "t3_link_removed": thing("t3_link_removed", selftext="", removed_by_category="moderator"),
        "t1_ok": thing("t1_ok", body="A real comment"),
        "t1_del": thing("t1_del", body="[deleted]"),
        "t1_rem": thing("t1_rem", body="[removed]"),
    }  # t3_vanished is not returned at all

    def info(fullnames):
        return (items[n] for n in fullnames if n in items)

    ids = ["reddit:" + n for n in [*items, "t3_vanished"]]
    gone = reddit_api.find_gone(NS(info=info), ids)
    assert sorted(gone) == sorted(["reddit:t3_del", "reddit:t3_link_removed", "reddit:t1_del", "reddit:t1_rem",
                                   "reddit:t3_vanished"])


def test_find_gone_asks_reddit_in_chunks_of_100():
    asked = []

    def info(fullnames):
        asked.append(len(fullnames))
        return (thing(n, selftext="fine", removed_by_category=None) for n in fullnames)

    ids = [f"reddit:t3_{i}" for i in range(230)]
    assert reddit_api.find_gone(NS(info=info), ids) == []
    assert asked == [100, 100, 30]


def test_purge_removes_derived_data_and_the_bytes_themselves(cfg):
    marker = "ZEBRA-MARKER-7731-should-not-survive"
    path = cfg["db_path"]
    conn = db.connect(path)
    db.init(conn)
    keep = Post("reddit:t3_keep", "reddit", "r/x", "post", "reddit:t3_keep", "u", "Keeper title", "Keeper body text.", NOW)
    gone = Post("reddit:t3_gone", "reddit", "r/x", "post", "reddit:t3_gone", "u", "Doomed title", marker, NOW)
    gone2 = Post("reddit:t3_gone2", "reddit", "r/x", "post", "reddit:t3_gone2", "u", "Also doomed", "Second doomed body.", NOW)
    db.insert_posts(conn, [keep, gone, gone2])
    for pid in ("reddit:t3_keep", "reddit:t3_gone", "reddit:t3_gone2"):
        db.save_extraction(conn, pid, "ok", "m", {
            "is_pain_point": True, "problem": f"problem of {pid}", "summary": "s", "category": "other",
            "persona": "x", "urgency": 3, "budget_signal": "none", "competitors_named": [],
            "evidence_quote": marker if pid.endswith("_gone") else "Keeper body"})
    conn.commit()
    db.replace_clusters(conn, [
        {"label": "problem of reddit:t3_keep", "category": "other", "members": ["reddit:t3_keep", "reddit:t3_gone"]},
        {"label": "only the doomed", "category": "other", "members": ["reddit:t3_gone2"]},
    ])

    assert db.purge_posts(conn, ["reddit:t3_gone", "reddit:t3_gone2", "reddit:t3_does_not_exist"]) == 2

    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 1  # cascaded
    sizes = {r["label"]: r["size"] for r in conn.execute("SELECT label, size FROM clusters")}
    assert sizes == {"problem of reddit:t3_keep": 1}  # empty cluster deleted, size recomputed
    conn.close()
    import glob
    for f in glob.glob(path + "*"):
        assert marker.encode() not in open(f, "rb").read(), f"deleted text survives in {f}"


# ---------------------------------------------------------------- search-based collection


def sub(i, title="A long enough submission title here", score=10):
    s = NS(id=i, title=title, selftext="Body text that is long enough.", stickied=False,
           permalink=f"/r/x/comments/{i}/", created_utc=NOW, score=score, num_comments=0, comment_sort=None)
    s.comments = NS(replace_more=lambda limit=0: None, list=lambda: [])
    return s


CFG = {"listing": "top", "time_filter": "month", "limit": 100, "min_score": 2, "include_comments": False,
       "comments_per_post": 5, "comment_min_chars": 40, "comment_min_score": 1, "comment_prefilter": False,
       "search_queries": ['"wish there was"', '"alternative to"'], "search_sort": "top", "search_time": "year",
       "search_limit": 25}


def test_collect_runs_a_search_per_query_and_deduplicates():
    calls = []

    def search(query, **kwargs):
        calls.append((query, kwargs))
        return iter([sub("shared"), sub("only_" + query[1:5])])

    reddit = NS(subreddit=lambda name: NS(top=lambda **kw: iter([sub("shared"), sub("low", score=1)]), search=search))
    posts = list(reddit_api.collect_subreddit(reddit, "SaaS", CFG))
    assert [c[0] for c in calls] == ['"wish there was"', '"alternative to"']
    assert calls[0][1] == {"sort": "top", "time_filter": "year", "limit": 25}
    ids = [p.id for p in posts]
    assert ids.count("reddit:t3_shared") == 1  # seen in the listing and in both searches, kept once
    assert "reddit:t3_low" not in ids          # below min_score
    assert len(ids) == 3


def test_search_is_optional():
    reddit = NS(subreddit=lambda name: NS(top=lambda **kw: iter([sub("a")]),
                                          search=lambda *a, **k: pytest.fail("no search expected")))
    assert len(list(reddit_api.collect_subreddit(reddit, "x", {**CFG, "search_queries": []}))) == 1


# ---------------------------------------------------------------- subreddit discovery


def fake_sr(**kw):
    base = dict(subscribers=12345, accounts_active=99, subreddit_type="public", over18=False,
                public_description="A place for tools.", display_name="tools")
    base.update(kw)
    return NS(**base)


class RaisingSub:
    @property
    def subscribers(self):
        from prawcore.exceptions import NotFound
        raise NotFound(NS(status_code=404))


def test_describe_subreddit_reports_missing_private_and_nsfw():
    # prawcore ships with the optional `reddit` extra; skip rather than break the suite without it.
    pytest.importorskip("prawcore")
    reddit = NS(subreddit=lambda n: {"good": fake_sr(), "priv": fake_sr(subreddit_type="private"),
                                     "nsfw": fake_sr(over18=True), "ghost": RaisingSub()}[n])
    good = reddit_api.describe_subreddit(reddit, "good")
    assert good["exists"] and good["usable"] and good["subscribers"] == 12345
    assert reddit_api.describe_subreddit(reddit, "priv")["usable"] is False
    assert reddit_api.describe_subreddit(reddit, "nsfw")["usable"] is False
    ghost = reddit_api.describe_subreddit(reddit, "ghost")
    assert ghost["exists"] is False and "NotFound" in ghost["error"]


def test_search_subreddits_skips_nsfw_and_private():
    found = [fake_sr(display_name="a"), fake_sr(display_name="b", over18=True),
             fake_sr(display_name="c", subreddit_type="private"), fake_sr(display_name="d")]
    reddit = NS(subreddits=NS(search=lambda q, limit: iter(found)))
    assert [r["name"] for r in reddit_api.search_subreddits(reddit, "saas", limit=10)] == ["a", "d"]


# ---------------------------------------------------------------- CLI wiring + extraction prompt


@pytest.fixture
def cli_cfg(tmp_path):
    import yaml
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"db_path": str(tmp_path / "t.db")}))
    return str(path)


def test_subreddits_check_command(cli_cfg, monkeypatch, capsys):
    pytest.importorskip("prawcore")
    reddit = NS(subreddit=lambda n: fake_sr() if n == "tools" else RaisingSub())
    monkeypatch.setattr(reddit_api, "make_client", lambda: reddit)
    main(["--config", cli_cfg, "subreddits", "check", "r/tools", "saasuser"])
    out = capsys.readouterr().out
    assert "r/tools: ok | 12,345 subscribers" in out and "r/saasuser: NOT USABLE" in out


def test_collect_reddit_first_removes_items_deleted_on_reddit(cli_cfg, monkeypatch, capsys):
    import sqlite3
    main(["--config", cli_cfg, "init"])
    from painpoint.config import load_config
    path = load_config(cli_cfg)["db_path"]
    conn = db.connect(path)
    db.insert_posts(conn, [
        Post("reddit:t3_alive", "reddit", "r/x", "post", "reddit:t3_alive", "u", "Alive post title", "Alive body text.", NOW),
        Post("reddit:t3_dead", "reddit", "r/x", "post", "reddit:t3_dead", "u", "Dead post title", "Dead body text.", NOW)])
    conn.close()
    reddit = NS(
        info=lambda fullnames: iter([thing("t3_alive", title="Alive post title", selftext="Alive body text.",
                                           removed_by_category=None),
                                     thing("t3_dead", title="Dead post title", selftext="[deleted]",
                                           removed_by_category="deleted")]),
        subreddit=lambda n: NS(top=lambda **kw: iter([]), search=lambda *a, **k: iter([])))
    monkeypatch.setattr(reddit_api, "make_client", lambda: reddit)
    main(["--config", cli_cfg, "collect", "reddit", "--subreddit", "SaaS"])
    out = capsys.readouterr().out
    assert "deletion sync: checked 2 stored Reddit items, removed 1 deleted/removed and 0 edited" in out
    left = [r[0] for r in sqlite3.connect(path).execute("SELECT id FROM posts")]
    assert left == ["reddit:t3_alive"]


def test_extraction_prompt_forbids_inferring_sensitive_traits():
    assert "sensitive characteristics" in EXTRACT_SYSTEM and "health, politics" in EXTRACT_SYSTEM


# ---------------------------------------------------------------- edited content (Developer Terms 3.3: "modified")


def stored(kind, title, body, pid):
    return {"id": f"reddit:{pid}", "kind": kind, "title": title, "body": body}


def test_find_outdated_separates_unchanged_edited_and_gone():
    items = {
        "t3_same": thing("t3_same", title="Same title", selftext="Same body", removed_by_category=None),
        "t3_body": thing("t3_body", title="Edited body", selftext="The author rewrote this paragraph", removed_by_category=None),
        "t3_title": thing("t3_title", title="A brand new title", selftext="Body", removed_by_category=None),
        "t1_same": thing("t1_same", body="Same comment"),
        "t1_edit": thing("t1_edit", body="Same comment, plus an edit"),
        "t3_del": thing("t3_del", title="x", selftext="[deleted]", removed_by_category="deleted"),
    }
    rows = [
        stored("post", "Same title", "Same body", "t3_same"),
        stored("post", "Edited body", "The original paragraph", "t3_body"),
        stored("post", "Old title", "Body", "t3_title"),
        stored("comment", "Re: whatever the parent was", "Same comment", "t1_same"),
        stored("comment", "Re: parent", "Same comment", "t1_edit"),
        stored("post", "x", "x", "t3_del"),
        stored("post", "x", "x", "t3_missing"),
    ]
    gone, modified = reddit_api.find_outdated(NS(info=lambda fullnames: (items[n] for n in fullnames if n in items)), rows)
    assert sorted(gone) == ["reddit:t3_del", "reddit:t3_missing"]
    assert sorted(modified) == ["reddit:t1_edit", "reddit:t3_body", "reddit:t3_title"]


def test_an_item_stored_by_our_own_converter_is_not_flagged_as_edited():
    s = sub("rt", title="Struggling with invoices every week", score=9)
    s.selftext = "It takes hours. Mail bob@example.com or call me."  # includes PII that scrub() rewrites on storage
    post = reddit_api.submission_to_post(s, "r/x")
    row = {"id": post.id, "kind": post.kind, "title": post.title, "body": post.body}
    s.fullname = "t3_rt"
    s.removed_by_category = None
    assert reddit_api.find_outdated(NS(info=lambda fullnames: iter([s])), [row]) == ([], [])


def test_collect_reddit_also_purges_edited_items(cli_cfg, monkeypatch, capsys):
    main(["--config", cli_cfg, "init"])
    from painpoint.config import load_config
    conn = db.connect(load_config(cli_cfg)["db_path"])
    db.insert_posts(conn, [Post("reddit:t3_ed", "reddit", "r/x", "post", "reddit:t3_ed", "u", "Edited title here", "Original body text here.", NOW)])
    conn.close()
    reddit = NS(info=lambda fullnames: iter([thing("t3_ed", title="Edited title here", selftext="Now totally different text.",
                                                   removed_by_category=None)]),
                subreddit=lambda n: NS(top=lambda **kw: iter([]), search=lambda *a, **k: iter([])))
    monkeypatch.setattr(reddit_api, "make_client", lambda: reddit)
    main(["--config", cli_cfg, "collect", "reddit", "--subreddit", "SaaS"])
    assert "removed 0 deleted/removed and 1 edited" in capsys.readouterr().out


# ---------------------------------------------------------------- wipe (Data API Terms 6: delete derived data on termination)


def test_wipe_requires_confirmation_then_erases_one_source_only(cli_cfg, capsys):
    main(["--config", cli_cfg, "init"])
    from painpoint.config import load_config
    path = load_config(cli_cfg)["db_path"]
    conn = db.connect(path)
    db.insert_posts(conn, [
        Post("reddit:t3_a", "reddit", "r/x", "post", "reddit:t3_a", "u", "Reddit post title", "Reddit body text here.", NOW),
        Post("hn:1", "hackernews", "Hacker News", "post", "hn:1", "u", "HN post title here", "HN body text here ok.", NOW)])
    db.save_extraction(conn, "reddit:t3_a", "ok", "m", {"is_pain_point": True, "problem": "p", "summary": "s",
                       "category": "other", "persona": "x", "urgency": 2, "budget_signal": "none",
                       "competitors_named": [], "evidence_quote": ""})
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit) as e:
        main(["--config", cli_cfg, "wipe"])
    assert "1 'reddit' items would be erased" in str(e.value)
    main(["--config", cli_cfg, "wipe", "--yes"])
    assert "erased 1 'reddit' items" in capsys.readouterr().out
    conn = db.connect(path)
    assert [r[0] for r in conn.execute("SELECT id FROM posts")] == ["hn:1"]
    assert conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 0
