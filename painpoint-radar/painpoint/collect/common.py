"""Shared collector pieces: the Post record, PII scrubbing, and the pain-signal prefilter."""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

USER_AGENT = "painpoint-radar/0.1 (market research; https://github.com/Sandpaws/SaasBuilder)"
_BREAKS = re.compile(r"<\s*(?:p|br|li|h\d)\b[^>]*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_BLANK_RUNS = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """HTML -> plain text (paragraph breaks kept, tags dropped, entities decoded)."""
    text = _BREAKS.sub("\n\n", raw or "")
    text = html.unescape(_TAGS.sub("", text))
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_RUNS.sub("\n\n", text).strip()


def get_json(url: str, timeout: int = 30, attempts: int = 3, user_agent: str = USER_AGENT) -> dict:
    """GET and parse JSON (gzip-aware), retrying rate limits (429) and server errors with backoff."""
    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url, headers={"User-Agent": user_agent, "Accept": "application/json", "Accept-Encoding": "gzip"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                if response.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return json.loads(data)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last = exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 120))
                continue
        except urllib.error.URLError as exc:
            last = exc
        time.sleep(2 * 2**attempt)
    assert last is not None
    raise last


def iso_to_ts(value: str | None) -> int | None:
    """'2024-05-01T12:34:56.789Z' (or without zone, read as UTC) -> unix seconds."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# Errors a collector reports and moves past (one failing request must not stop a run).
FETCH_ERRORS = (urllib.error.URLError, OSError, ValueError)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
_REDDIT_USER = re.compile(r"(?<![\w/])/?u/[A-Za-z0-9_-]{3,}")
_WS = re.compile(r"\s+")


def scrub(text: str) -> str:
    """Strip obvious personal identifiers before anything is stored or sent to a model."""
    text = _EMAIL.sub("[email]", text or "")
    text = _REDDIT_USER.sub("u/[user]", text)
    text = _PHONE.sub("[phone]", text)
    return text.strip()


# Cheap filter used on high-volume comments to skip chatter before paying for extraction.
PAIN_SIGNAL = re.compile(
    r"frustrat|annoy|hate|wish (there|i|we)|struggl|painful|nightmare|waste|manual(ly)?|"
    r"hours (a|per|every|each) (week|day|month)|too expensive|overpriced|can'?t find|"
    r"looking for (a|an|any) |alternative to|anyone (know|recommend|found)|workaround|clunky|"
    r"broken|doesn'?t (work|support|integrate)|no way to|tedious|spreadsheet|churn|"
    r"drives me|sick of|tired of|would (pay|love)|worth paying|switch(ed|ing) (from|away)",
    re.IGNORECASE,
)


def looks_like_pain(text: str) -> bool:
    return bool(PAIN_SIGNAL.search(text or ""))


@dataclass
class Post:
    id: str
    source: str
    community: str
    kind: str  # post | comment | review
    thread_id: str
    url: str
    title: str
    body: str
    created_utc: int
    upvotes: int = 0
    num_comments: int = 0

    def text_hash(self) -> str:
        norm = _WS.sub(" ", f"{self.title} {self.body}".strip().lower())
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()
