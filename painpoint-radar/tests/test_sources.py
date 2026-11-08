"""Stack Exchange, Lemmy and Discourse collectors against responses shaped like their documented APIs (no network)."""

import gzip
import io
import json
import urllib.error
import urllib.parse

import pytest
import yaml

from painpoint.cli import main
from painpoint.collect import common, discourse, lemmy, stackexchange
from painpoint.config import load_config

NOW = 1_800_000_000
DAY = 86400


def qs(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def no_sleep(_):
    pass


# ---------------------------------------------------------------- shared helpers

def test_get_json_reads_gzip_bodies(monkeypatch):
    payload = gzip.compress(json.dumps({"items": [1]}).encode())

    class Resp(io.BytesIO):
        headers = {"Content-Encoding": "gzip"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(common.urllib.request, "urlopen", lambda req, timeout: Resp(payload))
    assert common.get_json("https://api.example/x") == {"items": [1]}


def test_iso_dates_with_and_without_zone():
    assert common.iso_to_ts("2027-01-15T08:00:00Z") == common.iso_to_ts("2027-01-15T08:00:00.000+00:00")
    assert common.iso_to_ts("2027-01-15T08:00:00") == common.iso_to_ts("2027-01-15T08:00:00Z")  # Lemmy omits the zone
    assert common.iso_to_ts("not a date") is None and common.iso_to_ts(None) is None


# ---------------------------------------------------------------- Stack Exchange

SE_ITEM = {
    "question_id": 9001, "title": "Tool to reconcile bank exports with invoices?", "score": 7, "answer_count": 2,
    "creation_date": NOW - 10 * DAY, "link": "https://softwarerecs.stackexchange.com/questions/9001/x",
    "owner": {"display_name": "Secret Name"},
    "body": "<p>Every month I match bank CSV exports against invoices <b>by hand</b>. Mail jane@example.com.</p>",
}


def se_cfg(**kw):
    cfg = load_config()
    cfg["stackexchange"].update({"delay_s": 0, **kw})
    return cfg


def test_se_urls_list_top_questions_or_search():
    top = stackexchange.build_url("softwarerecs", None, NOW - 100, 1, 50, None)
    found = stackexchange.build_url("superuser", "alternative to", NOW - 100, 2, 50, "k123")
    assert "/2.3/questions?" in top and "q" not in qs(top) and qs(top)["filter"] == "withbody"
    assert "/2.3/search/advanced?" in found and qs(found)["q"] == "alternative to" and qs(found)["key"] == "k123"
    assert qs(found)["fromdate"] == str(NOW - 100) and qs(found)["page"] == "2"


def test_se_question_becomes_a_post_without_author_or_email():
    post = stackexchange.item_to_post(SE_ITEM, "softwarerecs")
    assert post.id == post.thread_id == "se:softwarerecs:9001" and post.kind == "post"
    assert post.upvotes == 7 and post.num_comments == 2 and "by hand" in post.body and "<b>" not in post.body
    assert "Secret Name" not in json.dumps(post.__dict__) and "jane@example.com" not in post.body


def test_se_pages_honour_backoff_dedupe_and_report_api_errors():
    sleeps = []

    def fetch(url):
        q = qs(url)
        if q["site"] == "webapps":
            return {"error_id": 502, "error_name": "throttle_violation", "error_message": "too many requests"}
        if q["page"] == "1":
            return {"items": [SE_ITEM], "has_more": True, "backoff": 10, "quota_remaining": 50}
        return {"items": [SE_ITEM, dict(SE_ITEM, question_id=9002)], "has_more": False, "quota_remaining": 49}

    cfg = se_cfg(sites=[{"site": "softwarerecs", "queries": []}, {"site": "webapps", "queries": ["x"]}])
    posts, errors = stackexchange.collect_stackexchange(cfg, fetch, sleeps.append, lambda: NOW)
    assert [p.id for p in posts] == ["se:softwarerecs:9001", "se:softwarerecs:9002"]
    assert 10 in sleeps  # backoff honoured
    assert len(errors) == 1 and "throttle_violation" in errors[0]


def test_se_stops_when_the_daily_quota_is_gone():
    calls = []

    def fetch(url):
        calls.append(url)
        return {"items": [SE_ITEM], "has_more": True, "quota_remaining": 0}

    posts, errors = stackexchange.collect_stackexchange(se_cfg(), fetch, no_sleep, lambda: NOW)
    assert len(calls) == 1 and len(posts) == 1 and "quota" in errors[0]


# ---------------------------------------------------------------- Lemmy

L_POST = {
    "post": {"id": 1, "name": "Is there a tool that syncs dotfiles without git?", "body": "I keep breaking my setup "
             "when I copy configs between machines by hand, there has to be a better way to do this.",
             "ap_id": "https://programming.dev/post/1", "published": "2027-01-10T10:00:00Z"},
    "community": {"name": "linux", "actor_id": "https://programming.dev/c/linux"},
    "creator": {"name": "secret_user"}, "counts": {"score": 12, "comments": 4},
}
L_COMMENT = {
    "comment": {"id": 2, "content": "I wish there was a self-hosted invoicing app that handles VAT for EU clients "
                "properly, every option I tried gets it wrong.", "ap_id": "https://lemmy.world/comment/2",
                "published": "2027-01-11T10:00:00", "deleted": False, "removed": False},
    "post": {"name": "Ask: tools you miss", "ap_id": "https://lemmy.world/post/7"},
    "community": {"name": "selfhosted", "actor_id": "https://lemmy.world/c/selfhosted"},
    "creator": {"name": "secret_user"}, "counts": {"score": 5, "child_count": 1},
}


def lemmy_cfg(**kw):
    cfg = load_config()
    cfg["lemmy"].update({"delay_s": 0, "since_days": 100000, **kw})
    return cfg


def test_lemmy_url_and_conversion():
    q = qs(lemmy.build_url("programming.dev", "alternative to", "Comments", 2, 50, "TopAll", "All"))
    assert q == {"q": "alternative to", "type_": "Comments", "sort": "TopAll", "listing_type": "All",
                 "limit": "50", "page": "2"}
    p = lemmy.post_view_to_post(L_POST, "programming.dev", 80)
    c = lemmy.comment_view_to_post(L_COMMENT, "programming.dev", 80)
    assert p.id == p.thread_id == "lemmy:https://programming.dev/post/1" and p.community == "linux@programming.dev"
    assert c.kind == "comment" and c.thread_id == "lemmy:https://lemmy.world/post/7" and c.title == "Re: Ask: tools you miss"
    assert c.community == "selfhosted@lemmy.world" and c.upvotes == 5
    assert "secret_user" not in json.dumps([p.__dict__, c.__dict__])
    assert lemmy.comment_view_to_post({**L_COMMENT, "comment": {**L_COMMENT["comment"], "removed": True}}, "x", 80) is None


def test_lemmy_dedupes_federated_copies_across_instances_and_reports_failures():
    def fetch(url):
        host = urllib.parse.urlparse(url).netloc
        if host == "down.example":
            raise urllib.error.URLError("down")
        return {"posts": [L_POST], "comments": [L_COMMENT]} if "type_=Posts" in url else {"comments": [L_COMMENT]}

    cfg = lemmy_cfg(instances=["programming.dev", "lemmy.world", "down.example"], queries=["wish there was"])
    posts, errors = lemmy.collect_lemmy(cfg, fetch, no_sleep, lambda: NOW)
    assert sorted(p.id for p in posts) == ["lemmy:https://lemmy.world/comment/2", "lemmy:https://programming.dev/post/1"]
    assert len(errors) == 2 and all("down.example" in e for e in errors)  # posts and comments both failed


def test_lemmy_drops_items_older_than_since_days():
    cfg = lemmy_cfg(queries=["x"], instances=["programming.dev"], since_days=1)
    posts, _ = lemmy.collect_lemmy(cfg, lambda u: {"posts": [L_POST], "comments": []}, no_sleep,
                                   lambda: common.iso_to_ts("2027-03-01T00:00:00Z"))
    assert posts == []


# ---------------------------------------------------------------- Discourse

CATEGORIES = {"category_list": {"categories": [
    {"id": 1, "name": "General", "slug": "general"},
    {"id": 2, "name": "Feature requests", "slug": "feature-requests", "subcategory_list": [
        {"id": 5, "name": "Mobile", "slug": "mobile"}]},
    {"id": 3, "name": "Plugins", "slug": "plugins", "subcategory_list": [
        {"id": 6, "name": "Plugin ideas", "slug": "plugin-ideas"}]},
]}}
TOPIC_LIST = {"topic_list": {"topics": [
    {"id": 10, "created_at": "2026-12-01T00:00:00Z", "like_count": 40},
    {"id": 11, "created_at": "2026-12-02T00:00:00Z", "pinned": True},           # "About this category"
    {"id": 12, "created_at": "2010-01-01T00:00:00Z"},                          # too old
], "more_topics_url": None}}
TOPIC_10 = {"id": 10, "title": "Offline sync for mobile", "slug": "offline-sync", "like_count": 40, "posts_count": 9,
            "post_stream": {"posts": [
                {"post_number": 1, "username": "secret_user", "created_at": "2026-12-01T00:00:00Z",
                 "cooked": "<p>When I lose signal on the train my notes do not sync and I lose edits. "
                           "Please add a proper offline mode.</p>"},
                {"post_number": 2, "username": "other", "cooked": "<p>+1</p>"}]}}


def dc_cfg(**kw):
    cfg = load_config()
    cfg["discourse"].update({"delay_s": 0, "forums": ["forum.example"], **kw})
    return cfg


def test_discourse_finds_request_categories_by_name_including_subcategories():
    cats = discourse.find_categories("forum.example", CATEGORIES, load_config()["discourse"]["category_pattern"])
    assert [c["slug"] for c in cats] == ["feature-requests", "plugin-ideas"]


def test_discourse_collects_opening_posts_of_liked_recent_topics():
    urls = []

    def fetch(url):
        urls.append(url)
        if url.endswith("/categories.json?include_subcategories=true"):
            return CATEGORIES
        if "/c/" in url:
            return TOPIC_LIST
        if url.endswith("/t/10.json"):
            return TOPIC_10
        raise AssertionError(url)

    posts, errors = discourse.collect_discourse(dc_cfg(), fetch, no_sleep, lambda: NOW)
    assert errors == [] and len(posts) == 1  # pinned and old topics skipped; topic 10 listed in both categories once
    p = posts[0]
    assert p.id == p.thread_id == "discourse:forum.example:t10" and p.url == "https://forum.example/t/offline-sync/10"
    assert p.upvotes == 40 and p.num_comments == 8 and "offline mode" in p.body and "<p>" not in p.body
    assert "secret_user" not in json.dumps(p.__dict__)
    assert not any("/search" in u for u in urls)  # Discourse's robots.txt disallows /search


def test_discourse_reports_forums_without_matching_categories_and_failures():
    def fetch(url):
        if "broken" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return {"category_list": {"categories": [{"id": 1, "name": "General", "slug": "general"}]}}

    posts, errors = discourse.collect_discourse(dc_cfg(forums=["forum.example", "broken.example"]), fetch,
                                                no_sleep, lambda: NOW)
    assert posts == [] and len(errors) == 2
    assert "no category matches" in errors[0] and "403" in errors[1]


# ---------------------------------------------------------------- CLI


@pytest.fixture
def cli_cfg(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"db_path": str(tmp_path / "t.db")}))
    return str(path)


def test_collect_commands_store_posts(cli_cfg, monkeypatch, capsys):
    seen = {}

    def fake(name, post):
        def run(cfg, progress=None):
            seen[name] = cfg[name]
            return [post], ["one warning"]
        return run

    monkeypatch.setattr(stackexchange, "collect_stackexchange",
                        fake("stackexchange", stackexchange.item_to_post(SE_ITEM, "softwarerecs")))
    monkeypatch.setattr(lemmy, "collect_lemmy", fake("lemmy", lemmy.post_view_to_post(L_POST, "programming.dev", 80)))
    main(["--config", cli_cfg, "collect", "stackexchange", "--site", "softwarerecs", "--limit", "5"])
    main(["--config", cli_cfg, "collect", "lemmy", "--instance", "programming.dev"])
    out = capsys.readouterr().out
    assert "stackexchange: 1 new" in out and "lemmy: 1 new" in out and "WARNING one warning" in out
    assert seen["stackexchange"]["max_per_query"] == 5
    assert [s["site"] for s in seen["stackexchange"]["sites"]] == ["softwarerecs"]
    assert seen["lemmy"]["instances"] == ["programming.dev"]
