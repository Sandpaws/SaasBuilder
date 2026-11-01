"""Shared collector pieces: the Post record, PII scrubbing, and the pain-signal prefilter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

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
