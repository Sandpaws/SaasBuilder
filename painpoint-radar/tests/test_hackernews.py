"""Hacker News collector against responses shaped like the documented Algolia HN Search API (no network)."""

import json
import urllib.error
import urllib.parse
from types import SimpleNamespace as NS

import pytest
import yaml

from painpoint import db
from painpoint.cli import main
from painpoint.collect import hackernews as hn
from painpoint.collect import reddit_api
from painpoint.config import load_config

NOW = 1_800_000_000

COMMENT = {
    "objectID": "111", "_tags": ["comment", "author_secret_name", "story_999"], "author": "secret_name",
    "comment_text": "I really wish there was a tool for this.<p>Right now I paste invoices into a spreadsheet "
                    "&#x27;by hand&#x27; and it costs me <i>hours</i> every week. See <a href=\"https://x.test\">https://x.test</a>",
    "story_id": 999, "story_title": "Ask HN: How do you handle invoicing?", "created_at_i": NOW - 86400, "points": None,
}
STORY = {
    "objectID": "222", "_tags": ["story", "ask_hn", "author_other"], "author": "other",
    "title": "Ask HN: What is a tool you wish existed?", "story_text": "I keep hitting the same <b>annoying</b> workflow gap.",
    "created_at_i": NOW - 2 * 86400, "points": 42, "num_comments": 17,
}


def test_html_is_converted_to_plain_text():
    text = hn.html_to_text(COMMENT["comment_text"])
    assert "<" not in text and "&#x27;" not in text
    assert "'by hand'" in text and "\n\n" in text and "hours every week" in text


def test_comment_becomes_a_post_without_the_author():
    post = hn.hit_to_post(COMMENT)
    assert post.id == "hn:111" and post.kind == "comment" and post.thread_id == "hn:999"
    assert post.title == "Re: Ask HN: How do you handle invoicing?"
    assert post.url == "https://news.ycombinator.com/item?id=111" and post.created_utc == NOW - 86400
    assert "secret_name" not in json.dumps(post.__dict__)


def test_story_becomes_a_post_with_points_and_comment_count():
    post = hn.hit_to_post(STORY)
    assert post.kind == "post" and post.thread_id == post.id == "hn:222"
    assert post.upvotes == 42 and post.num_comments == 17 and "annoying workflow gap" in post.body


@pytest.mark.parametrize("hit", [
    {"objectID": "1", "_tags": ["comment"], "comment_text": "too short"},
    {"objectID": "2", "_tags": ["comment"], "comment_text": "[dead]"},
    {"objectID": "3", "_tags": ["comment"], "comment_text": None},
    {"objectID": "", "_tags": ["comment"], "comment_text": "x" * 200},
    {"objectID": "4", "_tags": ["story"], "title": "tiny", "story_text": None},
])
def test_unusable_hits_are_skipped(hit):
    assert hn.hit_to_post(hit) is None


def test_personal_identifiers_in_text_are_scrubbed():
    hit = dict(COMMENT, comment_text="Mail me at jane@example.com about how I wish there was an invoicing tool for this, "
                                    "because the manual spreadsheet routine costs me hours every single week.")
    post = hn.hit_to_post(hit)
    assert post is not None and "jane@example.com" not in post.body and "[email]" in post.body


def test_url_carries_query_tag_date_filter_and_paging():
    url = hn.build_url('"wish there was"', "comment", NOW - 100, 2, 50, "search")
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.path == "/api/v1/search" and parsed.netloc == "hn.algolia.com"
    assert q["query"] == ['"wish there was"'] and q["tags"] == ["comment"]
    assert q["numericFilters"] == [f"created_at_i>{NOW - 100}"] and q["page"] == ["2"] and q["hitsPerPage"] == ["50"]


def cfg_with(**overrides):
    """The search pass on its own: no Ask HN extras, no thread mining (those have their own tests)."""
    cfg = load_config()
    base = dict(queries=['"wish there was"', '"alternative to"'], tags=["comment"], delay_s=0, ask_queries=[])
    cfg["hackernews"].update({**base, **overrides})
    cfg["hackernews"]["threads"]["enabled"] = False
    return cfg


def test_collect_pages_dedupes_and_stops_at_the_cap():
    pages = {0: {"hits": [COMMENT, dict(COMMENT, objectID="112", comment_text=COMMENT["comment_text"] + " more")], "nbPages": 3},
             1: {"hits": [dict(COMMENT, objectID="113", comment_text="Another long enough comment about a missing tool " * 3)], "nbPages": 3}}
    seen_urls = []

    def fetch(url):
        seen_urls.append(url)
        page = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["page"][0])
        return pages.get(page, {"hits": [], "nbPages": 3})

    posts, errors = hn.collect_hackernews(cfg_with(max_per_query=3), fetch, sleep=lambda s: None, now=lambda: NOW)
    assert errors == []
    # query 1 stops after 3 hits (pages 0 and 1); query 2 returns the same ids, which are deduplicated
    assert sorted(p.id for p in posts) == ["hn:111", "hn:112", "hn:113"]
    assert len(seen_urls) == 4 and "page=2" not in " ".join(seen_urls)


def test_one_failing_query_is_reported_and_the_rest_continue():
    def fetch(url):
        if "alternative" in urllib.parse.unquote_plus(url):
            raise urllib.error.URLError("boom")
        return {"hits": [COMMENT], "nbPages": 1}

    posts, errors = hn.collect_hackernews(cfg_with(max_per_query=10), fetch, sleep=lambda s: None, now=lambda: NOW)
    assert [p.id for p in posts] == ["hn:111"]
    assert len(errors) == 1 and "alternative to" in errors[0] and "URLError" in errors[0]


def test_since_days_sets_the_date_filter():
    urls = []
    hn.collect_hackernews(cfg_with(max_per_query=1, since_days=30), lambda u: urls.append(u) or {"hits": []},
                          sleep=lambda s: None, now=lambda: NOW)
    assert f"created_at_i%3E{NOW - 30 * 86400}" in urls[0]


def test_ask_queries_run_on_ask_hn_only():
    urls = []
    cfg = cfg_with(max_per_query=1, ask_queries=['"too expensive"'])
    hn.collect_hackernews(cfg, lambda u: urls.append(urllib.parse.unquote_plus(u)) or {"hits": []},
                          sleep=lambda s: None, now=lambda: NOW)
    expensive = [u for u in urls if "too expensive" in u]
    assert len(expensive) == 1 and "tags=ask_hn" in expensive[0]


# ---------------------------------------------------------------- thread mining

ASK_THREAD = {"objectID": "500", "title": "Ask HN: What tool do you wish existed?", "num_comments": 80}
OFF_TOPIC = {"objectID": "501", "title": "Ask HN: What apps do you use daily?", "num_comments": 300}
EXCLUDED = {"objectID": "502", "title": "Ask HN: What problem would you solve with unlimited money? Annoying ones",
            "num_comments": 90}
ITEM = {
    "id": 500, "type": "story", "title": "Ask HN: What tool do you wish existed?", "created_at_i": NOW - 86400,
    "children": [
        {"id": 601, "type": "comment", "author": "secret_name", "created_at_i": NOW - 3600,
         "text": "A tool that reconciles my bank exports with invoices. I do it by hand every month and it takes hours.",
         "children": [{"id": 701, "type": "comment", "text": "Same here, I would pay for this.", "children": []}]},
        {"id": 602, "type": "comment", "created_at_i": NOW - 7200,
         "text": "Something that keeps my dotfiles in sync across machines without me thinking about it at all.",
         "children": [{"id": 702, "type": "comment", "text": "+1"}, {"id": 703, "type": "comment", "text": "+1"}]},
        {"id": 603, "type": "comment", "text": "too short", "children": []},
        {"id": 604, "type": "comment", "text": None, "children": []},  # deleted
    ],
}


def test_thread_search_url_targets_ask_hn_titles_with_enough_comments():
    q = urllib.parse.parse_qs(urllib.parse.urlparse(hn.build_thread_search_url("wish existed", NOW - 100, 25)).query)
    assert q["tags"] == ["ask_hn"] and q["restrictSearchableAttributes"] == ["title"]
    assert q["numericFilters"] == [f"created_at_i>{NOW - 100},num_comments>=25"]


STATEMENT = {"objectID": "503", "title": "Tell HN: Take-home tests without feedback are frustrating", "num_comments": 400}
YES_NO = {"objectID": "504", "title": "Ask HN: Would you pay for a verified-only social network?", "num_comments": 200}
OPEN_PAY = {"objectID": "505", "title": "Ask HN: Dev productivity tools you would pay for", "num_comments": 60}  # no "?"


def test_find_threads_keeps_open_asks_biggest_first():
    tcfg = dict(load_config()["hackernews"]["threads"], title_queries=["wish existed"], max_threads=10)
    hits = [OFF_TOPIC, OPEN_PAY, ASK_THREAD, EXCLUDED, STATEMENT, YES_NO]
    threads, errors = hn.find_threads(tcfg, lambda u: {"hits": hits}, lambda s: None, NOW - 100, 0)
    # off-topic, "unlimited money", a statement and a yes/no question are all dropped
    assert errors == [] and [t["objectID"] for t in threads] == ["500", "505"]


def test_each_top_level_answer_is_its_own_voice_without_the_author():
    posts = hn.thread_answers(ITEM, min_chars=80, limit=10)
    assert [p.id for p in posts] == ["hn:602", "hn:601"]  # most-replied first; short and deleted answers skipped
    assert all(p.thread_id == p.id for p in posts)
    assert posts[0].num_comments == 2 and posts[0].title == "Re: Ask HN: What tool do you wish existed?"
    assert "secret_name" not in json.dumps([p.__dict__ for p in posts])
    assert len(hn.thread_answers(ITEM, min_chars=80, limit=1)) == 1


def test_collect_mines_threads_after_searches_and_dedupes():
    cfg = cfg_with(max_per_query=5)
    cfg["hackernews"]["threads"].update(enabled=True, title_queries=["wish existed"], max_threads=5)

    def fetch(url):
        if "/items/500" in url:
            return ITEM
        if "restrictSearchableAttributes" in url:
            return {"hits": [ASK_THREAD]}
        # the search pass already found answer 601 as an ordinary comment hit
        return {"hits": [dict(COMMENT, objectID="601", story_id=500)], "nbPages": 1}

    posts, errors = hn.collect_hackernews(cfg, fetch, sleep=lambda s: None, now=lambda: NOW)
    assert errors == [] and sorted(p.id for p in posts) == ["hn:601", "hn:602"]


def test_a_failing_thread_is_reported_and_the_rest_continue():
    cfg = cfg_with(max_per_query=5, queries=[])
    cfg["hackernews"]["threads"].update(enabled=True, title_queries=["wish existed"], max_threads=5)

    def fetch(url):
        if "/items/" in url:
            raise urllib.error.URLError("down")
        return {"hits": [ASK_THREAD]}

    posts, errors = hn.collect_hackernews(cfg, fetch, sleep=lambda s: None, now=lambda: NOW)
    assert posts == [] and len(errors) == 1 and "thread 500" in errors[0]


# ---------------------------------------------------------------- CLI


@pytest.fixture
def cli_cfg(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"db_path": str(tmp_path / "t.db")}))
    return str(path)


def test_collect_hn_command_stores_posts_and_reports_errors(cli_cfg, monkeypatch, capsys):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        if len(calls) == 2:
            raise urllib.error.URLError("network down")
        return {"hits": [COMMENT, STORY], "nbPages": 1}

    monkeypatch.setattr(hn, "fetch_json", fake_fetch)
    monkeypatch.setattr(hn.time, "sleep", lambda s: None)
    main(["--config", cli_cfg, "collect", "hn", "--query", '"wish there was"', "--query", '"alternative to"'])
    out = capsys.readouterr().out
    assert "hackernews: 2 new, 0 already stored or duplicate" in out and "WARNING" in out
    # --query is a targeted search: one request per phrase per tag, no Ask HN extras or thread mining
    assert len(calls) == 4 and not any("restrictSearchableAttributes" in u or "/items/" in u for u in calls)
    main(["--config", cli_cfg, "stats"])
    assert "posts" in capsys.readouterr().out


def test_reddit_limit_flag_shrinks_a_test_run(cli_cfg, monkeypatch, capsys):
    seen = {}

    def top(**kw):
        seen["top"] = kw
        return iter([])

    def search(query, **kw):
        seen["search"] = kw
        return iter([])

    reddit = NS(info=lambda fullnames: iter([]), subreddit=lambda n: NS(top=top, search=search))
    monkeypatch.setattr(reddit_api, "make_client", lambda: reddit)
    main(["--config", cli_cfg, "collect", "reddit", "--subreddit", "SaaS", "--limit", "5"])
    assert seen["top"]["limit"] == 5 and seen["search"]["limit"] == 5
