"""Generic Scrapling collector for review sites, forums and other HTML pages, driven by YAML.

Example source (see config.example.yaml):

    web_sources:
      - name: acme_reviews
        community: "Reviews: Acme"
        start_urls: ["https://example.com/reviews?page=1"]
        item_selector: ".review"
        fields: {title: ".title::text", body: ".body ::text", rating: ".stars::attr(data-rating)",
                 url: "a.permalink::attr(href)", date: "time::attr(datetime)"}
        next_page: "a.next::attr(href)"
        max_rating: 3

robots.txt is obeyed by default: Scrapling silently drops disallowed requests, so this module reports the
count. Only point it at sites whose terms allow it, and never at personal/sensitive data.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

from .common import Post, scrub
from .importer import to_epoch


def _extract(el, selector: str) -> str:
    if "::attr(" in selector:
        return (el.css(selector).get() or "").strip()
    return " ".join(v.strip() for v in el.css(selector).getall() if v and v.strip())


def _number(value: str) -> float | None:
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return None


def build_spider(src: dict[str, Any]):
    """Create a Scrapling Spider subclass from one `web_sources` entry."""
    from scrapling.spiders import Spider  # imported lazily so the rest of the tool works without it

    item_selector = src["item_selector"]
    fields: dict[str, str] = src["fields"]
    next_selector = src.get("next_page")
    max_pages = int(src.get("max_pages", 10))
    max_rating = src.get("max_rating")
    start_urls = list(src["start_urls"])
    # Scrapling's offsite filter compares host[:port], so allow both spellings (matters for non-default ports).
    hosts = {h for u in start_urls for h in (urlparse(u).hostname, urlparse(u).netloc) if h}

    async def parse(self, response):
        page = int(response.meta.get("page", 1))
        for el in response.css(item_selector):
            item = {key: _extract(el, sel) for key, sel in fields.items()}
            if max_rating is not None and "rating" in item:
                rating = _number(item["rating"])
                if rating is not None and rating > float(max_rating):
                    continue
            item["_page_url"] = response.url
            yield item
        if next_selector and page < max_pages:
            nxt = response.css(next_selector).get()
            if nxt:
                yield response.follow(nxt, meta={"page": page + 1})

    attrs = {
        "name": f"painpoint_{src['name']}",
        "start_urls": start_urls,
        "allowed_domains": hosts,
        "robots_txt_obey": bool(src.get("obey_robots_txt", True)),
        "concurrent_requests": int(src.get("concurrent", 2)),
        "concurrent_requests_per_domain": int(src.get("concurrent", 2)),
        "download_delay": float(src.get("delay", 2.0)),
        "autothrottle_enabled": bool(src.get("autothrottle", True)),
        "development_mode": bool(src.get("dev_cache", False)),
        "logging_level": logging.WARNING,
        "parse": parse,
    }
    return type(f"WebSpider_{src['name']}", (Spider,), attrs)


def item_to_post(src: dict[str, Any], item: dict[str, str]) -> Post | None:
    title = scrub(item.get("title", ""))
    body = scrub(item.get("body", ""))
    if len(title) + len(body) < 20:
        return None
    url = item.get("url") or item.get("_page_url", "")
    pid = f"web:{src['name']}:" + hashlib.sha1(f"{url}{title}{body}".encode("utf-8")).hexdigest()[:16]
    return Post(
        id=pid,
        source=f"web:{src['name']}",
        community=src.get("community", src["name"]),
        kind=src.get("kind", "review"),
        thread_id=pid,
        url=url,
        title=title,
        body=body,
        created_utc=to_epoch(item.get("date")),
        upvotes=int(_number(item.get("upvotes", "0")) or 0),
        num_comments=0,
    )


def collect_web_source(src: dict[str, Any]) -> tuple[list[Post], dict[str, int]]:
    spider = build_spider(src)()
    result = spider.start()
    stats = {
        "items": len(result.items),
        "requests": int(getattr(result.stats, "requests_count", 0)),
        "robots_disallowed": int(getattr(result.stats, "robots_disallowed_count", 0)),
    }
    posts = [p for p in (item_to_post(src, i) for i in result.items) if p is not None]
    return posts, stats
