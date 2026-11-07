"""Hacker News collector using the public Algolia HN Search API (https://hn.algolia.com/api).

It searches comments and "Ask HN" posts for complaint phrases ("wish there was", "alternative to", ...). No key is
needed. Be polite: requests are sequential with a delay, the User-Agent names this project, and nothing is stored
that identifies the author (the `author` field is never read). Check the API's current terms before relying on it.
Comment text arrives as HTML and is converted to plain text.

First run against the live service on 2026-11-07: 210 items from 16 searches, text clean (no HTML left), and 209 of
210 contain their search phrase verbatim, so quoted phrases are matched as phrases.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .common import Post, scrub

BASE = "https://hn.algolia.com/api/v1"
USER_AGENT = "painpoint-radar/0.1 (market research; https://github.com/Sandpaws/SaasBuilder)"
_BREAKS = re.compile(r"<\s*(?:p|br)\b[^>]*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_BLANK_RUNS = re.compile(r"\n{3,}")
_DEAD = {"[dead]", "[deleted]", "[flagged]"}


def html_to_text(raw: str) -> str:
    """HN comment HTML -> plain text (paragraph breaks kept, tags dropped, entities decoded)."""
    text = _BREAKS.sub("\n\n", raw or "")
    text = html.unescape(_TAGS.sub("", text))
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_RUNS.sub("\n\n", text).strip()


def fetch_json(url: str, timeout: int = 30, attempts: int = 3) -> dict:
    """GET and parse JSON, retrying rate limits (429) and server errors with backoff."""
    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last = exc
        except urllib.error.URLError as exc:
            last = exc
        time.sleep(2 * 2**attempt)
    assert last is not None
    raise last


def build_url(query: str, tag: str, since_ts: int, page: int, per_page: int, sort: str) -> str:
    params = {
        "query": query,
        "tags": tag,
        "numericFilters": f"created_at_i>{since_ts}",
        "hitsPerPage": per_page,
        "page": page,
    }
    return f"{BASE}/{sort}?{urllib.parse.urlencode(params)}"


def hit_to_post(hit: dict, min_chars: int = 80) -> Post | None:
    """Convert one search hit to a Post; None for dead/deleted/too-short items."""
    object_id = str(hit.get("objectID") or "")
    if not object_id:
        return None
    if "comment" in (hit.get("_tags") or []):
        body = scrub(html_to_text(hit.get("comment_text") or ""))
        if body in _DEAD or len(body) < min_chars:
            return None
        title = scrub(f"Re: {hit.get('story_title') or ''}".strip())[:200]
        thread, kind = f"hn:{hit.get('story_id') or object_id}", "comment"
    else:
        title = scrub(hit.get("title") or "")
        body = scrub(html_to_text(hit.get("story_text") or ""))
        if body in _DEAD or len(title) + len(body) < 20:
            return None
        thread, kind = f"hn:{object_id}", "post"
    return Post(
        id=f"hn:{object_id}",
        source="hackernews",
        community="Hacker News",
        kind=kind,
        thread_id=thread,
        url=f"https://news.ycombinator.com/item?id={object_id}",
        title=title,
        body=body,
        created_utc=int(hit.get("created_at_i") or time.time()),
        upvotes=int(hit.get("points") or 0),
        num_comments=int(hit.get("num_comments") or 0),
    )


def build_thread_search_url(query: str, since_ts: int, min_comments: int, per_page: int = 50) -> str:
    """Ask HN posts whose *title* matches `query` and that drew at least `min_comments` comments."""
    params = {
        "query": query,
        "tags": "ask_hn",
        "restrictSearchableAttributes": "title",
        "numericFilters": f"created_at_i>{since_ts},num_comments>={min_comments}",
        "hitsPerPage": per_page,
    }
    return f"{BASE}/search?{urllib.parse.urlencode(params)}"


def find_threads(
    tcfg: dict, fetch: Callable[[str], dict], sleep: Callable[[float], None], since: int, delay: float
) -> tuple[list[dict], list[str]]:
    """Ask HN threads that ask people for their problems, biggest first. Title search is loose, so every hit's title
    must also match `title_pattern` and not `exclude_pattern`."""
    keep = re.compile(tcfg["title_pattern"], re.IGNORECASE)
    drop = re.compile(tcfg["exclude_pattern"], re.IGNORECASE) if tcfg.get("exclude_pattern") else None
    found: dict[str, dict] = {}
    errors: list[str] = []
    for query in tcfg["title_queries"]:
        try:
            data = fetch(build_thread_search_url(query, since, tcfg["min_comments"]))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"threads / {query}: {type(exc).__name__}: {exc}")
            continue
        for hit in data.get("hits") or []:
            title = hit.get("title") or ""
            oid = str(hit.get("objectID") or "")
            if oid and keep.search(title) and not (drop and drop.search(title)):
                found[oid] = hit
        sleep(delay)
    ranked = sorted(found.values(), key=lambda h: -int(h.get("num_comments") or 0))
    return ranked[: tcfg["max_threads"]], errors


def thread_answers(item: dict, min_chars: int, limit: int) -> list[Post]:
    """Top-level answers of one thread (from GET /items/{id}), most-replied first.

    Each answer gets its own thread_id: in a thread that asks "what tool do you wish existed?", every top-level
    answer is a different person's need, so ten people wanting the same thing should count as ten voices, not as
    one thread with extra comments. Replies under an answer are not collected; their count is kept as num_comments.
    """
    story_title = scrub(f"Re: {item.get('title') or ''}".strip())[:200]
    answers = []
    for child in item.get("children") or []:
        if child.get("type") != "comment" or not child.get("id"):
            continue
        body = scrub(html_to_text(child.get("text") or ""))
        if body in _DEAD or len(body) < min_chars:
            continue
        cid = str(child["id"])
        answers.append(Post(
            id=f"hn:{cid}",
            source="hackernews",
            community="Hacker News",
            kind="comment",
            thread_id=f"hn:{cid}",
            url=f"https://news.ycombinator.com/item?id={cid}",
            title=story_title,
            body=body,
            created_utc=int(child.get("created_at_i") or item.get("created_at_i") or time.time()),
            upvotes=0,  # the API does not expose comment scores
            num_comments=len(child.get("children") or []),
        ))
    answers.sort(key=lambda p: -p.num_comments)
    return answers[:limit]


def _search(
    query: str, tag: str, since: int, hn: dict, fetch: Callable[[str], dict], sleep: Callable[[float], None],
    seen: set[str], posts: list[Post], errors: list[str],
) -> None:
    fetched, page = 0, 0
    while fetched < hn["max_per_query"]:
        per_page = min(100, hn["max_per_query"] - fetched)
        try:
            data = fetch(build_url(query, tag, since, page, per_page, hn["sort"]))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"{tag} / {query}: {type(exc).__name__}: {exc}")
            break
        hits = data.get("hits") or []
        for hit in hits:
            oid = str(hit.get("objectID") or "")
            if oid in seen:
                continue
            seen.add(oid)
            post = hit_to_post(hit, hn["min_chars"])
            if post is not None:
                posts.append(post)
        fetched += len(hits)
        page += 1
        if not hits or page >= int(data.get("nbPages") or 0):
            break
        sleep(hn["delay_s"])
    sleep(hn["delay_s"])


def collect_hackernews(
    cfg: dict,
    fetch: Callable[[str], dict] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[Post], list[str]]:
    """Run every search, then mine the matching Ask HN threads. Returns (posts, error messages); one failing request
    does not stop the rest. fetch/sleep/now default to the real ones, looked up at call time so tests can patch them."""
    fetch = fetch or fetch_json
    sleep = sleep or time.sleep
    now = now or time.time
    hn = cfg["hackernews"]
    since = int(now() - hn["since_days"] * 86400)
    posts: list[Post] = []
    errors: list[str] = []
    seen: set[str] = set()
    searches = [(tag, q) for tag in hn["tags"] for q in hn["queries"]]
    searches += [("ask_hn", q) for q in hn.get("ask_queries") or []]
    for i, (tag, query) in enumerate(searches, 1):
        _search(query, tag, since, hn, fetch, sleep, seen, posts, errors)
        if progress and i % 10 == 0:
            progress(f"  searches {i}/{len(searches)}: {len(posts)} items so far")

    tcfg = hn.get("threads") or {}
    if tcfg.get("enabled") and tcfg.get("max_threads", 0) > 0:
        thread_since = int(now() - tcfg["since_days"] * 86400)
        threads, thread_errors = find_threads(tcfg, fetch, sleep, thread_since, hn["delay_s"])
        errors += thread_errors
        for i, hit in enumerate(threads, 1):
            try:
                item = fetch(f"{BASE}/items/{hit['objectID']}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                errors.append(f"thread {hit['objectID']}: {type(exc).__name__}: {exc}")
                continue
            added = 0
            for post in thread_answers(item, hn["min_chars"], tcfg["max_comments_per_thread"]):
                oid = post.id.removeprefix("hn:")
                if oid not in seen:
                    seen.add(oid)
                    posts.append(post)
                    added += 1
            if progress:
                progress(f"  thread {i}/{len(threads)}: +{added}  {(hit.get('title') or '')[:70]}")
            sleep(hn["delay_s"])
    return posts, errors
