"""Lemmy collector (the open, federated Reddit-like network) using each instance's public API (/api/v3).

For each instance and complaint phrase, it searches posts and comments. With listing_type "All" an instance also
returns content federated from other instances, so the same post can come back from two instances; items are keyed
by their ActivityPub id (`ap_id`, the canonical URL), which is the same everywhere. Author names are never read.
Deleted and removed items are skipped. Each instance sets its own rules: read them before running this.

Written from the documented v3 API shape and tested against fixtures; not run against the live service yet.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Callable

from .common import FETCH_ERRORS, Post, get_json, iso_to_ts, scrub


def build_url(instance: str, query: str, kind: str, page: int, limit: int, sort: str, listing: str) -> str:
    params = {"q": query, "type_": kind, "sort": sort, "listing_type": listing, "limit": limit, "page": page}
    return f"https://{instance}/api/v3/search?{urllib.parse.urlencode(params)}"


def _community(view: dict, instance: str) -> str:
    c = view.get("community") or {}
    actor = c.get("actor_id") or ""
    host = urllib.parse.urlparse(actor).netloc or instance
    return f"{c.get('name') or 'unknown'}@{host}"


def post_view_to_post(view: dict, instance: str, min_chars: int) -> Post | None:
    p = view.get("post") or {}
    if not p.get("ap_id") or p.get("deleted") or p.get("removed"):
        return None
    title = scrub(p.get("name") or "")
    body = scrub(p.get("body") or "")
    if len(title) + len(body) < min_chars:
        return None
    counts = view.get("counts") or {}
    pid = f"lemmy:{p['ap_id']}"
    return Post(
        id=pid, source="lemmy", community=_community(view, instance), kind="post", thread_id=pid, url=p["ap_id"],
        title=title[:300], body=body, created_utc=iso_to_ts(p.get("published")) or int(time.time()),
        upvotes=int(counts.get("score") or 0), num_comments=int(counts.get("comments") or 0),
    )


def comment_view_to_post(view: dict, instance: str, min_chars: int) -> Post | None:
    c = view.get("comment") or {}
    if not c.get("ap_id") or c.get("deleted") or c.get("removed"):
        return None
    body = scrub(c.get("content") or "")
    if len(body) < min_chars:
        return None
    post = view.get("post") or {}
    counts = view.get("counts") or {}
    return Post(
        id=f"lemmy:{c['ap_id']}", source="lemmy", community=_community(view, instance), kind="comment",
        thread_id=f"lemmy:{post.get('ap_id') or c['ap_id']}", url=c["ap_id"],
        title=scrub(f"Re: {post.get('name') or ''}")[:200], body=body,
        created_utc=iso_to_ts(c.get("published")) or int(time.time()),
        upvotes=int(counts.get("score") or 0), num_comments=int(counts.get("child_count") or 0),
    )


def collect_lemmy(
    cfg: dict,
    fetch: Callable[[str], dict] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[Post], list[str]]:
    """Search every instance x phrase x (posts, comments). Returns (posts, errors)."""
    fetch = fetch or get_json
    sleep = sleep or time.sleep
    now = now or time.time
    lc = cfg["lemmy"]
    since = int(now() - lc["since_days"] * 86400)
    posts: list[Post] = []
    errors: list[str] = []
    seen: set[str] = set()
    for instance in lc["instances"]:
        before = len(posts)
        for query in lc["queries"]:
            for kind, key, convert in (("Posts", "posts", post_view_to_post), ("Comments", "comments", comment_view_to_post)):
                fetched, page = 0, 1
                while fetched < lc["max_per_query"]:
                    limit = min(50, lc["max_per_query"] - fetched)  # the API caps limit at 50
                    try:
                        data = fetch(build_url(instance, query, kind, page, limit, lc["sort"], lc["listing_type"]))
                    except FETCH_ERRORS as exc:
                        errors.append(f"{instance} / {query} / {kind}: {type(exc).__name__}: {exc}")
                        break
                    views = data.get(key) or []
                    for view in views:
                        post = convert(view, instance, lc["min_chars"])
                        if post and post.id not in seen and post.created_utc >= since:
                            seen.add(post.id)
                            posts.append(post)
                    fetched += len(views)
                    page += 1
                    sleep(lc["delay_s"])
                    if len(views) < limit:
                        break
        if progress:
            progress(f"  {instance}: +{len(posts) - before}")
    return posts, errors
