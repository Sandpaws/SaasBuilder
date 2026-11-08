"""Discourse forum collector: feature-request and idea categories of product forums, via Discourse's public JSON.

Many software products run a Discourse forum with a category where users ask for what is missing ("Feature
requests", "Ideas", "Wishlist"). For each forum this collector:
  1. reads /categories.json and keeps categories whose name or slug matches `category_pattern`, so no slugs have to
     be known in advance;
  2. lists each category's topics, most liked first (/c/{slug}/{id}.json?order=op_likes);
  3. fetches each topic (/t/{id}.json) and keeps the opening post: the request itself, with its like count.
It avoids /search, which Discourse's default robots.txt disallows, and never reads author names. Each forum has its
own terms (usually at /tos): read them before running this.

Written from Discourse's documented JSON shape and tested against fixtures; not run against a live forum yet.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from .common import FETCH_ERRORS, Post, get_json, html_to_text, iso_to_ts, scrub


def find_categories(forum: str, data: dict, pattern: str) -> list[dict]:
    """Categories (and subcategories) whose name or slug matches `pattern`."""
    keep = re.compile(pattern, re.IGNORECASE)
    out = []
    stack = list((data.get("category_list") or {}).get("categories") or [])
    while stack:
        cat = stack.pop(0)
        stack.extend(cat.get("subcategory_list") or [])
        if cat.get("id") and cat.get("slug") and (keep.search(cat.get("name") or "") or keep.search(cat["slug"])):
            out.append({"id": cat["id"], "slug": cat["slug"], "name": cat.get("name") or cat["slug"]})
    return out


def topic_to_post(forum: str, topic: dict, min_chars: int) -> Post | None:
    """The opening post of a /t/{id}.json response."""
    posts = (topic.get("post_stream") or {}).get("posts") or []
    first = next((p for p in posts if p.get("post_number") == 1), None)
    if not first or not topic.get("id") or first.get("hidden") or first.get("deleted_at"):
        return None
    title = scrub(topic.get("title") or "")
    body = scrub(html_to_text(first.get("cooked") or ""))
    if len(title) + len(body) < min_chars:
        return None
    tid = f"discourse:{forum}:t{topic['id']}"
    return Post(
        id=tid, source="discourse", community=forum, kind="post", thread_id=tid,
        url=f"https://{forum}/t/{topic.get('slug') or 'topic'}/{topic['id']}",
        title=title[:300], body=body,
        created_utc=iso_to_ts(first.get("created_at") or topic.get("created_at")) or int(time.time()),
        upvotes=int(topic.get("like_count") or first.get("like_count") or 0),
        num_comments=max(int(topic.get("posts_count") or 1) - 1, 0),
    )


def collect_discourse(
    cfg: dict,
    fetch: Callable[[str], dict] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[Post], list[str]]:
    """Returns (posts, errors); one failing forum, category or topic does not stop the rest."""
    fetch = fetch or get_json
    sleep = sleep or time.sleep
    now = now or time.time
    dc = cfg["discourse"]
    since = int(now() - dc["since_days"] * 86400)
    posts: list[Post] = []
    errors: list[str] = []
    for forum in dc["forums"]:
        try:
            cats = find_categories(forum, fetch(f"https://{forum}/categories.json?include_subcategories=true"),
                                   dc["category_pattern"])
        except FETCH_ERRORS as exc:
            errors.append(f"{forum}: {type(exc).__name__}: {exc}")
            continue
        sleep(dc["delay_s"])
        if not cats:
            errors.append(f"{forum}: no category matches category_pattern; set one in discourse.category_pattern")
            continue
        before = len(posts)
        done: set[int] = set()  # a topic can show up in a category and in its parent's listing
        for cat in cats:
            topic_ids: list[int] = []
            page = 0
            while len(topic_ids) < dc["max_topics_per_category"]:
                try:
                    data = fetch(f"https://{forum}/c/{cat['slug']}/{cat['id']}.json?order=op_likes&page={page}")
                except FETCH_ERRORS as exc:
                    errors.append(f"{forum} / {cat['name']}: {type(exc).__name__}: {exc}")
                    break
                sleep(dc["delay_s"])
                listed = (data.get("topic_list") or {}).get("topics") or []
                for t in listed:
                    created = iso_to_ts(t.get("created_at")) or 0
                    tid = t.get("id")
                    if tid and not t.get("pinned") and created >= since and tid not in topic_ids and tid not in done:
                        topic_ids.append(tid)
                if not listed or not (data.get("topic_list") or {}).get("more_topics_url"):
                    break
                page += 1
            for tid in topic_ids[: dc["max_topics_per_category"]]:
                done.add(tid)
                try:
                    post = topic_to_post(forum, fetch(f"https://{forum}/t/{tid}.json"), dc["min_chars"])
                except FETCH_ERRORS as exc:
                    errors.append(f"{forum} / topic {tid}: {type(exc).__name__}: {exc}")
                    continue
                finally:
                    sleep(dc["delay_s"])
                if post is not None:
                    posts.append(post)
        if progress:
            progress(f"  {forum}: +{len(posts) - before} from {len(cats)} categor{'y' if len(cats) == 1 else 'ies'}")
    return posts, errors
