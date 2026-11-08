"""Stack Exchange collector using the official API (https://api.stackexchange.com/docs, version 2.3).

Software Recommendations (softwarerecs) is the best fit: every question there is someone asking for software that
does X, so it is collected without a search phrase. Other sites (Super User, Web Applications) are searched for
complaint phrases. Questions only, with their score and answer count; answers and author names are not stored.

Without a key the API allows about 300 requests a day per IP; with a free app key (register at stackapps.com, put
it in STACKEXCHANGE_KEY or stackexchange.key) about 10,000. The API asks clients to honour its `backoff` field.
Content is CC BY-SA: any quote you show someone needs a link and the author's name, and we do not store names, so
keep reports private. Read the API terms before running it.

Written from the documented API shape and tested against fixtures; not run against the live service yet.
"""

from __future__ import annotations

import os
import time
import urllib.parse
from typing import Callable

from .common import FETCH_ERRORS, Post, get_json, html_to_text, scrub

BASE = "https://api.stackexchange.com/2.3"


def build_url(site: str, query: str | None, since_ts: int, page: int, per_page: int, key: str | None) -> str:
    params = {
        "site": site,
        "order": "desc",
        "sort": "votes",
        "fromdate": since_ts,
        "page": page,
        "pagesize": per_page,
        "filter": "withbody",  # include the question body (HTML)
    }
    if query:
        params["q"] = query
    if key:
        params["key"] = key
    path = "search/advanced" if query else "questions"
    return f"{BASE}/{path}?{urllib.parse.urlencode(params)}"


def item_to_post(item: dict, site: str, min_chars: int = 40) -> Post | None:
    qid = item.get("question_id")
    if not qid:
        return None
    title = scrub(html_to_text(item.get("title") or ""))
    body = scrub(html_to_text(item.get("body") or ""))
    if len(title) + len(body) < min_chars:
        return None
    pid = f"se:{site}:{qid}"
    return Post(
        id=pid,
        source="stackexchange",
        community=site,
        kind="post",
        thread_id=pid,
        url=item.get("link") or f"https://{site}.stackexchange.com/q/{qid}",
        title=title[:300],
        body=body,
        created_utc=int(item.get("creation_date") or time.time()),
        upvotes=int(item.get("score") or 0),
        num_comments=int(item.get("answer_count") or 0),
    )


def collect_stackexchange(
    cfg: dict,
    fetch: Callable[[str], dict] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[Post], list[str]]:
    """Every (site, query) search; a site with no queries lists its top questions. Returns (posts, errors)."""
    fetch = fetch or get_json
    sleep = sleep or time.sleep
    now = now or time.time
    se = cfg["stackexchange"]
    key = se.get("key") or os.environ.get("STACKEXCHANGE_KEY")
    since = int(now() - se["since_days"] * 86400)
    posts: list[Post] = []
    errors: list[str] = []
    seen: set[str] = set()
    for entry in se["sites"]:
        site = entry["site"]
        before = len(posts)
        for query in entry.get("queries") or [None]:
            fetched, page = 0, 1
            while fetched < se["max_per_query"]:
                per_page = min(100, se["max_per_query"] - fetched)
                try:
                    data = fetch(build_url(site, query, since, page, per_page, key))
                except FETCH_ERRORS as exc:
                    errors.append(f"{site} / {query or 'top questions'}: {type(exc).__name__}: {exc}")
                    break
                if data.get("error_id"):
                    errors.append(f"{site} / {query or 'top questions'}: {data.get('error_name')}: "
                                  f"{data.get('error_message')}")
                    break
                items = data.get("items") or []
                for item in items:
                    post = item_to_post(item, site, se["min_chars"])
                    if post is not None and post.id not in seen and post.upvotes >= se["min_score"]:
                        seen.add(post.id)
                        posts.append(post)
                fetched += len(items)
                page += 1
                # The API asks clients to wait `backoff` seconds before the next call to the same method.
                sleep(max(se["delay_s"], float(data.get("backoff") or 0)))
                if not items or not data.get("has_more"):
                    break
                if data.get("quota_remaining", 1) <= 0:
                    errors.append("daily API quota used up; add a key (STACKEXCHANGE_KEY) or run again tomorrow")
                    return posts, errors
        if progress:
            progress(f"  {site}: +{len(posts) - before}")
    return posts, errors
