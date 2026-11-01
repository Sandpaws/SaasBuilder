"""Runs the real Scrapling spider against a local mock review site (including robots.txt behaviour)."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("scrapling")

from painpoint.collect.web_spider import collect_web_source  # noqa: E402

REVIEW = (
    '<div class="review"><h3 class="title">{title}</h3><p class="body">{body}</p>'
    '<span class="stars" data-rating="{rating}"></span><a class="permalink" href="/reviews/{n}">link</a>'
    '<time datetime="2026-10-0{n}T10:00:00Z"></time></div>'
)
PAGES = {
    "/reviews?page=1": "".join([
        REVIEW.format(n=1, rating=5, title="Love it", body="Great product, no complaints at all here."),
        REVIEW.format(n=2, rating=2, title="Exports are slow", body="Exporting reports takes forever and it crashes."),
        REVIEW.format(n=3, rating=1, title="Pricing is a joke", body="Way too expensive for what you get, switching away."),
    ]) + '<a class="next" href="/reviews?page=2">next</a>',
    "/reviews?page=2": REVIEW.format(n=4, rating=3, title="No API access", body="There is no way to automate anything with an API."),
    "/private/reviews": REVIEW.format(n=5, rating=1, title="Secret", body="This page is disallowed by robots.txt entirely."),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body, ctype = "User-agent: *\nDisallow: /private/\n", "text/plain"
        elif self.path in PAGES:
            body, ctype = f"<html><body>{PAGES[self.path]}</body></html>", "text/html"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def source(base, path, **kw):
    return {
        "name": "mock", "community": "Mock reviews", "start_urls": [base + path], "item_selector": ".review",
        "fields": {"title": ".title::text", "body": ".body ::text", "rating": ".stars::attr(data-rating)",
                   "url": "a.permalink::attr(href)", "date": "time::attr(datetime)"},
        "next_page": "a.next::attr(href)", "max_rating": 3, "delay": 0, "autothrottle": False, **kw,
    }


def test_follows_pagination_and_filters_by_rating(site):
    posts, stats = collect_web_source(source(site, "/reviews?page=1"))
    titles = sorted(p.title for p in posts)
    assert titles == ["Exports are slow", "No API access", "Pricing is a joke"]  # the 5-star review is dropped
    assert stats["robots_disallowed"] == 0
    p = next(p for p in posts if p.title == "Exports are slow")
    assert p.community == "Mock reviews" and p.kind == "review" and p.source == "web:mock"
    assert p.created_utc > 1_700_000_000  # parsed from the <time datetime=...>


def test_max_pages_limits_the_crawl(site):
    posts, _ = collect_web_source(source(site, "/reviews?page=1", max_pages=1))
    assert len(posts) == 2  # page 2 never fetched


def test_robots_txt_is_obeyed_and_reported(site):
    posts, stats = collect_web_source(source(site, "/private/reviews"))
    assert posts == [] and stats["robots_disallowed"] >= 1
