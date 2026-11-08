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
    cfg = load_config()
    cfg["hackernews"].update(queries=['"wish there was"', '"alternative to"'], tags=["comment"], delay_s=0, **overrides)
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
    # collect_hackernews binds fetch_json as a default argument, so patch the default as well
    monkeypatch.setattr(hn.collect_hackernews, "__defaults__", (fake_fetch, lambda s: None, hn.time.time))
    main(["--config", cli_cfg, "collect", "hn", "--query", '"wish there was"', "--query", '"alternative to"'])
    out = capsys.readouterr().out
    assert "hackernews: 2 new, 0 already stored or duplicate" in out and "WARNING" in out
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
    main(["--config", cli_cfg, "collect", "reddit", "--via", "api", "--subreddit", "SaaS", "--limit", "5"])
    assert seen["top"]["limit"] == 5 and seen["search"]["limit"] == 5
