"""Import posts you already have (JSONL or CSV): exports, datasets, or output from any other scraper.

Recognised columns (all optional except some text): id, source, community, kind, thread_id, url, title,
body|text|content, created_utc|date|created_at, upvotes|score, num_comments. Author columns are ignored.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .common import Post, scrub


def to_epoch(value: Any, default: int | None = None) -> int:
    """Accept unix seconds (int/float/str) or ISO-8601; fall back to `default` (now)."""
    fallback = int(time.time()) if default is None else default
    if value in (None, ""):
        return fallback
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.replace(".", "", 1).isdigit():
        return int(float(s))
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _first(row: dict, *names: str, default: Any = "") -> Any:
    for n in names:
        if row.get(n) not in (None, ""):
            return row[n]
    return default


def _rows(path: Path) -> Iterator[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            yield from csv.DictReader(fh)
    else:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def row_to_post(row: dict, default_source: str = "import") -> Post | None:
    title = scrub(str(_first(row, "title")))
    body = scrub(str(_first(row, "body", "text", "content")))
    if len(title) + len(body) < 20:
        return None
    source = str(_first(row, "source", default=default_source))
    url = str(_first(row, "url"))
    if row.get("id"):
        pid = f"{source}:{row['id']}"
    else:
        pid = f"{source}:" + hashlib.sha1((url or title + body).encode("utf-8")).hexdigest()[:16]
    return Post(
        id=pid,
        source=source,
        community=str(_first(row, "community", default=source)),
        kind=str(_first(row, "kind", default="post")),
        thread_id=str(_first(row, "thread_id", default=pid)),
        url=url,
        title=title,
        body=body,
        created_utc=to_epoch(_first(row, "created_utc", "date", "created_at", default=None)),
        upvotes=int(float(_first(row, "upvotes", "score", default=0) or 0)),
        num_comments=int(float(_first(row, "num_comments", default=0) or 0)),
    )


def load_posts(path: str | Path, default_source: str = "import") -> Iterator[Post]:
    for row in _rows(Path(path)):
        post = row_to_post(row, default_source)
        if post is not None:
            yield post
