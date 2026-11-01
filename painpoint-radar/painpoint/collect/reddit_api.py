"""Reddit collector using the OFFICIAL API through PRAW (read-only app credentials).

Credentials come from the environment (never from the config file):
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT

Reddit's terms govern what you may do with this data, especially commercially. Read Reddit's current
Data API terms (start at redditinc.com/policies) and check your access before building a product on it.
Usernames are never read or stored.
"""

from __future__ import annotations

import os
from typing import Iterator

from .common import Post, looks_like_pain, scrub

REQUIRED_ENV = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
_REMOVED = {"", "[deleted]", "[removed]"}


def make_client():
    try:
        import praw
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit("PRAW is not installed. Run: pip install -e '.[reddit]'") from exc
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            "Missing Reddit credentials in the environment: " + ", ".join(missing)
            + "\nCreate a 'script' app at https://www.reddit.com/prefs/apps (if Reddit still allows it for your account)."
        )
    return praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ["REDDIT_USER_AGENT"],
        check_for_async=False,
    )


def submission_to_post(sub, community: str) -> Post | None:
    if getattr(sub, "stickied", False):
        return None
    title = scrub(getattr(sub, "title", "") or "")
    body = scrub(getattr(sub, "selftext", "") or "")
    if body.strip().lower() in _REMOVED:
        body = ""
    if len(title) + len(body) < 20:
        return None
    pid = f"reddit:t3_{sub.id}"
    return Post(
        id=pid,
        source="reddit",
        community=community,
        kind="post",
        thread_id=pid,
        url="https://www.reddit.com" + (getattr(sub, "permalink", "") or ""),
        title=title,
        body=body,
        created_utc=int(getattr(sub, "created_utc", 0)),
        upvotes=int(getattr(sub, "score", 0)),
        num_comments=int(getattr(sub, "num_comments", 0)),
    )


def comment_to_post(c, parent: Post, cfg: dict) -> Post | None:
    body = scrub(getattr(c, "body", "") or "")
    if body.strip().lower() in _REMOVED or getattr(c, "stickied", False) or getattr(c, "distinguished", None):
        return None
    if len(body) < cfg["comment_min_chars"] or int(getattr(c, "score", 0)) < cfg["comment_min_score"]:
        return None
    if cfg["comment_prefilter"] and not looks_like_pain(body):
        return None
    return Post(
        id=f"reddit:t1_{c.id}",
        source="reddit",
        community=parent.community,
        kind="comment",
        thread_id=parent.thread_id,
        url="https://www.reddit.com" + (getattr(c, "permalink", "") or ""),
        title=f"Re: {parent.title}"[:200],
        body=body,
        created_utc=int(getattr(c, "created_utc", parent.created_utc)),
        upvotes=int(getattr(c, "score", 0)),
        num_comments=0,
    )


def collect_subreddit(reddit, name: str, cfg: dict) -> Iterator[Post]:
    sr = reddit.subreddit(name)
    lister = getattr(sr, cfg["listing"])
    kwargs = {"limit": cfg["limit"]}
    if cfg["listing"] in ("top", "controversial"):
        kwargs["time_filter"] = cfg["time_filter"]
    for sub in lister(**kwargs):
        if int(getattr(sub, "score", 0)) < cfg["min_score"]:
            continue
        post = submission_to_post(sub, f"r/{name}")
        if post is None:
            continue
        yield post
        if not cfg["include_comments"] or int(getattr(sub, "num_comments", 0)) == 0:
            continue
        sub.comment_sort = "top"
        sub.comments.replace_more(limit=0)
        kept = 0
        for c in sub.comments.list():
            cp = comment_to_post(c, post, cfg)
            if cp is None:
                continue
            yield cp
            kept += 1
            if kept >= cfg["comments_per_post"]:
                break


def collect_safely(reddit, name: str, cfg: dict) -> tuple[list[Post], str | None]:
    """Collect one subreddit. A missing/private/banned subreddit or an access error becomes a message, not a crash;
    whatever was gathered before the error is still returned so it can be saved."""
    try:
        from prawcore.exceptions import PrawcoreException
    except ImportError:  # pragma: no cover - PRAW not installed
        PrawcoreException = ()  # type: ignore[assignment,misc]
    posts: list[Post] = []
    try:
        for post in collect_subreddit(reddit, name, cfg):
            posts.append(post)
    except PrawcoreException as exc:
        return posts, (f"{type(exc).__name__}: the subreddit may not exist, be private or banned, "
                       "or your credentials lack access")
    return posts, None
