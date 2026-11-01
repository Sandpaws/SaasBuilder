"""Hacker News collector using the public Algolia HN Search API (https://hn.algolia.com/api).

It searches comments and "Ask HN" posts for complaint phrases ("wish there was", "alternative to", ...). No key is
needed. Be polite: requests are sequential with a delay, the User-Agent names this project, and nothing is stored
that identifies the author (the `author` field is never read). Check the API's current terms before relying on it.
Comment text arrives as HTML and is converted to plain text.

Note: written from the API's documented response shape; it has not been run against the live service yet.
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


def collect_hackernews(
    cfg: dict,
    fetch: Callable[[str], dict] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> tuple[list[Post], list[str]]:
    """Run every (tag, query) search. Returns (posts, error messages); one failing query does not stop the rest."""
    hn = cfg["hackernews"]
    since = int(now() - hn["since_days"] * 86400)
    posts: list[Post] = []
    errors: list[str] = []
    seen: set[str] = set()
    for tag in hn["tags"]:
        for query in hn["queries"]:
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
    return posts, errors
