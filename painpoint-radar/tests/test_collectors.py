import csv
import json
from types import SimpleNamespace as NS

import pytest

from painpoint import db
from painpoint.collect import importer, reddit_api
from painpoint.collect.common import Post, looks_like_pain, scrub

NOW = 1_800_000_000


def test_scrub_removes_identifiers():
    out = scrub("Mail me at jane.doe@example.com or call +1 (415) 555-0100. Thanks u/some_user and /u/other-guy!")
    assert "jane.doe" not in out and "415" not in out and "some_user" not in out and "other-guy" not in out
    assert "[email]" in out and "[phone]" in out and "u/[user]" in out


def test_pain_prefilter():
    assert looks_like_pain("I'm so tired of exporting this by hand, wish there was a tool")
    assert not looks_like_pain("Congrats on the launch, looks great!")


def sub(i="abc", title="Struggling with invoices every week", selftext="It takes hours.", score=10, comments=None, **kw):
    s = NS(id=i, title=title, selftext=selftext, stickied=False, permalink=f"/r/SaaS/comments/{i}/x/",
           created_utc=NOW, score=score, num_comments=len(comments or []), comment_sort=None)
    s.comments = NS(replace_more=lambda limit=0: None, list=lambda: comments or [])
    s.__dict__.update(kw)
    return s


def comment(i, body, score=5, **kw):
    return NS(id=i, body=body, score=score, stickied=False, distinguished=None, permalink=f"/r/SaaS/c/{i}/",
              created_utc=NOW, **kw)


def test_submission_to_post_has_no_author_and_scrubs():
    s = sub(selftext="Email me bob@example.com, I hate manual work", author=NS(name="secret_name"))
    p = reddit_api.submission_to_post(s, "r/SaaS")
    assert p.id == "reddit:t3_abc" and p.thread_id == p.id and p.url.startswith("https://www.reddit.com/r/SaaS")
    assert "bob@example.com" not in p.body
    assert "secret_name" not in json.dumps(p.__dict__)


def test_submission_filters():
    assert reddit_api.submission_to_post(sub(stickied=True), "r/x") is None
    assert reddit_api.submission_to_post(sub(title="hi", selftext="[removed]"), "r/x") is None


CFG = {"listing": "top", "time_filter": "month", "limit": 10, "min_score": 2, "include_comments": True,
       "comments_per_post": 2, "comment_min_chars": 40, "comment_min_score": 1, "comment_prefilter": True}


def test_comment_filters():
    parent = reddit_api.submission_to_post(sub(), "r/SaaS")
    ok = comment("c1", "This drives me crazy, I waste hours every week on manual exports.")
    assert reddit_api.comment_to_post(ok, parent, CFG).thread_id == parent.thread_id
    assert reddit_api.comment_to_post(comment("c2", "Great post, thanks for sharing this with everyone here!"), parent, CFG) is None
    assert reddit_api.comment_to_post(comment("c3", "short but hate"), parent, CFG) is None
    mod = comment("c4", "This is frustrating and a waste of hours every single week.")
    mod.distinguished = "moderator"
    assert reddit_api.comment_to_post(mod, parent, CFG) is None
    assert reddit_api.comment_to_post(comment("c5", "I hate manual work and waste hours daily on it", score=0), parent, CFG) is None


def test_collect_subreddit_applies_listing_score_and_comment_cap():
    seen = {}
    comments = [comment(f"c{i}", f"Frustrating: I waste hours every week on manual task number {i}.") for i in range(5)]
    subs = [sub("a", comments=comments), sub("b", score=1), sub("c", title="Another long enough painful title here")]

    def top(**kwargs):
        seen.update(kwargs)
        return iter(subs)
    reddit = NS(subreddit=lambda name: NS(top=top))
    posts = list(reddit_api.collect_subreddit(reddit, "SaaS", CFG))
    assert seen == {"limit": 10, "time_filter": "month"}
    kinds = [p.kind for p in posts]
    assert kinds.count("post") == 2 and kinds.count("comment") == 2  # 'b' dropped by min_score; comments capped at 2
    assert all(p.community == "r/SaaS" for p in posts)

    hot_seen = {}
    reddit2 = NS(subreddit=lambda name: NS(hot=lambda **kw: hot_seen.update(kw) or iter([])))
    list(reddit_api.collect_subreddit(reddit2, "SaaS", {**CFG, "listing": "hot"}))
    assert hot_seen == {"limit": 10}  # hot() takes no time_filter


def test_importer_jsonl_and_csv(tmp_path):
    rows = [
        {"id": 1, "title": "Exports are painfully slow", "text": "Every export takes ten minutes and then fails.",
         "date": "2026-10-01T10:00:00Z", "score": "7", "author": "should_be_ignored", "community": "r/Tool"},
        {"title": "x", "body": ""},  # too short -> skipped
        {"title": "No id", "body": "Body long enough to keep this row around.", "url": "https://e.test/1"},
    ]
    j = tmp_path / "d.jsonl"
    j.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    posts = list(importer.load_posts(j, "mydata"))
    assert len(posts) == 2
    assert posts[0].id == "mydata:1" and posts[0].upvotes == 7 and posts[0].community == "r/Tool"
    assert posts[0].created_utc == importer.to_epoch("2026-10-01T10:00:00Z")
    assert posts[1].id.startswith("mydata:") and "should_be_ignored" not in json.dumps([p.__dict__ for p in posts])

    c = tmp_path / "d.csv"
    with c.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["title", "body", "created_utc"])
        w.writeheader()
        w.writerow({"title": "CSV title long enough", "body": "CSV body long enough", "created_utc": "1700000000"})
    assert list(importer.load_posts(c))[0].created_utc == 1700000000


def test_to_epoch_variants():
    assert importer.to_epoch(1700000000.5) == 1700000000
    assert importer.to_epoch("2026-01-01") == importer.to_epoch("2026-01-01T00:00:00+00:00")
    assert importer.to_epoch("garbage", default=5) == 5


def test_duplicate_text_and_ids_are_ignored(conn):
    a = Post("p1", "s", "c", "post", "p1", "u", "Same title here", "Same body text here.", NOW)
    b = Post("p2", "s", "c", "post", "p2", "u", "same  TITLE here", "Same body text   here.", NOW)  # same text
    c = Post("p1", "s", "c", "post", "p1", "u", "Different", "Different body entirely.", NOW)       # same id
    assert db.insert_posts(conn, [a, b, c]) == (1, 2)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(posts)")]
    assert not any(x in cols for x in ("author", "username", "user"))


def test_a_missing_subreddit_is_reported_and_partial_results_are_kept():
    NotFound = pytest.importorskip("prawcore.exceptions").NotFound  # optional `reddit` extra

    def broken_listing(**kwargs):
        yield sub("ok1", title="A perfectly fine long enough post title")
        raise NotFound(NS(status_code=404))

    reddit = NS(subreddit=lambda name: NS(top=broken_listing))
    posts, error = reddit_api.collect_safely(reddit, "saasuser", {**CFG, "include_comments": False})
    assert [p.id for p in posts] == ["reddit:t3_ok1"]  # what was gathered before the error is kept
    assert "NotFound" in error and "may not exist" in error

    good = NS(subreddit=lambda name: NS(top=lambda **kw: iter([sub("ok2", title="Another long enough post title")])))
    posts, error = reddit_api.collect_safely(good, "SaaS", {**CFG, "include_comments": False})
    assert error is None and len(posts) == 1
