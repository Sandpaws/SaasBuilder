"""Reddit collector using the OFFICIAL API through PRAW (read-only app credentials).

Credentials come from the environment (never from the config file):
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT

Reddit's terms govern what you may do with this data, especially commercially. Read Reddit's current
Data API terms (start at redditinc.com/policies) and check your access before building a product on it.
Usernames are never read or stored.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Iterable, Iterator

from .common import Post, looks_like_pain, scrub

REQUIRED_ENV = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
_REMOVED = {"", "[deleted]", "[removed]"}
# Reddit asks for: <platform>:<app ID>:<version string> (by /u/<reddit username>)
USER_AGENT_RE = re.compile(r"^[^:\s]+:[^:\s]+:\S+ \(by /u/[A-Za-z0-9_-]+\)$")
USER_AGENT_EXAMPLE = "python:com.yourname.painpoint-radar:v0.1.0 (by /u/yourname)"
# Ids we can look up with GET /api/info. Comments collected through Agent Reach have none (see agent_reach.py).
_FULLNAME_ID = re.compile(r"^reddit:t[13]_[a-z0-9]+$")


def has_credentials() -> bool:
    return all(os.environ.get(k) for k in REQUIRED_ENV)


def has_fullname(post_id: str) -> bool:
    """True for a stored id Reddit's API can check ('reddit:t3_abc', 'reddit:t1_xyz')."""
    return bool(_FULLNAME_ID.match(post_id))


def check_user_agent(user_agent: str) -> str | None:
    """Return a warning if the User-Agent is not in the format Reddit's API guide requires, else None."""
    if USER_AGENT_RE.match(user_agent.strip()):
        return None
    return (f"REDDIT_USER_AGENT {user_agent!r} does not match Reddit's required format "
            f"'<platform>:<app ID>:<version> (by /u/<username>)', e.g. {USER_AGENT_EXAMPLE!r}. "
            "Reddit throttles or blocks unidentified clients.")


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
    warning = check_user_agent(os.environ["REDDIT_USER_AGENT"])
    if warning:
        print("WARNING: " + warning, file=sys.stderr)
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
    """Listing (top/hot/new/...) plus one search per configured complaint-phrase query. Each post once."""
    sr = reddit.subreddit(name)
    seen: set[str] = set()

    def emit(sub) -> Iterator[Post]:
        if sub.id in seen or int(getattr(sub, "score", 0)) < cfg["min_score"]:
            return
        seen.add(sub.id)
        post = submission_to_post(sub, f"r/{name}")
        if post is None:
            return
        yield post
        if not cfg["include_comments"] or int(getattr(sub, "num_comments", 0)) == 0:
            return
        sub.comment_sort = "top"
        sub.comments.replace_more(limit=0)  # drops "load more" stubs without extra requests
        kept = 0
        for c in sub.comments.list():
            cp = comment_to_post(c, post, cfg)
            if cp is None:
                continue
            yield cp
            kept += 1
            if kept >= cfg["comments_per_post"]:
                break

    lister = getattr(sr, cfg["listing"])
    kwargs = {"limit": cfg["limit"]}
    if cfg["listing"] in ("top", "controversial"):
        kwargs["time_filter"] = cfg["time_filter"]
    for sub in lister(**kwargs):
        yield from emit(sub)
    for query in cfg.get("search_queries") or []:
        for sub in sr.search(query, sort=cfg["search_sort"], time_filter=cfg["search_time"],
                             limit=cfg["search_limit"]):
            yield from emit(sub)


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


# ---------------------------------------------------------------- deletion sync


def _is_gone(item) -> bool:
    """True if Reddit now shows this post/comment as deleted or removed."""
    if getattr(item, "removed_by_category", None):  # posts removed by the author, a mod or Reddit
        return True
    text = (getattr(item, "body", None) or getattr(item, "selftext", None) or "").strip().lower()
    return text in ("[deleted]", "[removed]")


def find_gone(reddit, post_ids: Iterable[str]) -> list[str]:
    """Which of our stored reddit ids (e.g. 'reddit:t3_abc') are deleted/removed or no longer returned by Reddit.

    Reddit's rules require removing content that was deleted on Reddit. Items missing from the response are treated
    as gone too. Looks up 100 fullnames per request.
    """
    ids = list(post_ids)
    gone: list[str] = []
    for start in range(0, len(ids), 100):
        chunk = ids[start : start + 100]
        names = [i.split(":", 1)[1] for i in chunk]
        alive = {item.fullname: item for item in reddit.info(fullnames=names)}
        for pid, name in zip(chunk, names):
            item = alive.get(name)
            if item is None or _is_gone(item):
                gone.append(pid)
    return gone


# ---------------------------------------------------------------- subreddit discovery


def describe_subreddit(reddit, name: str) -> dict:
    """Does r/name exist and is it usable? Reads GET /r/{name}/about (subscribers, type, NSFW, description)."""
    try:
        from prawcore.exceptions import PrawcoreException
    except ImportError:  # pragma: no cover
        PrawcoreException = ()  # type: ignore[assignment,misc]
    try:
        sr = reddit.subreddit(name)
        info = {
            "name": name,
            "exists": True,
            "subscribers": getattr(sr, "subscribers", None),
            "active_users": getattr(sr, "accounts_active", None),
            "type": getattr(sr, "subreddit_type", None),
            "nsfw": bool(getattr(sr, "over18", False)),
            "description": (getattr(sr, "public_description", "") or "").strip(),
        }
    except PrawcoreException as exc:
        return {"name": name, "exists": False, "error": f"{type(exc).__name__}: missing, private, banned or no access"}
    info["usable"] = info["type"] == "public" and not info["nsfw"]
    return info


def search_subreddits(reddit, query: str, limit: int = 10) -> list[dict]:
    """Find subreddits by title/description (GET /subreddits/search). NSFW and non-public ones are left out."""
    out = []
    for sr in reddit.subreddits.search(query, limit=limit * 2):
        if bool(getattr(sr, "over18", False)) or getattr(sr, "subreddit_type", "public") != "public":
            continue
        out.append({
            "name": sr.display_name,
            "subscribers": getattr(sr, "subscribers", None),
            "description": (getattr(sr, "public_description", "") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


def _current_text(item) -> tuple[str | None, str]:
    """(title, body) exactly as we would store the item today; title is None for comments (we store a derived one)."""
    if item.fullname.startswith("t1_"):
        return None, scrub(getattr(item, "body", "") or "")
    body = scrub(getattr(item, "selftext", "") or "")
    if body.strip().lower() in _REMOVED:
        body = ""
    return scrub(getattr(item, "title", "") or ""), body


def find_outdated(reddit, rows: Iterable[dict]) -> tuple[list[str], list[str]]:
    """Compare stored Reddit items with Reddit now. Returns (gone_ids, modified_ids).

    Reddit's Developer Terms (3.3) require deleting or modifying content that was deleted, removed *or modified* on
    Reddit. Rows need id, kind, title and body. Items edited since we stored them are returned so they can be purged
    and re-collected in their current form.
    """
    rows = list(rows)
    gone: list[str] = []
    modified: list[str] = []
    for start in range(0, len(rows), 100):
        chunk = rows[start : start + 100]
        names = [r["id"].split(":", 1)[1] for r in chunk]
        alive = {item.fullname: item for item in reddit.info(fullnames=names)}
        for row, name in zip(chunk, names):
            item = alive.get(name)
            if item is None or _is_gone(item):
                gone.append(row["id"])
                continue
            title, body = _current_text(item)
            if body != row["body"] or (title is not None and row["kind"] == "post" and title != row["title"]):
                modified.append(row["id"])
    return gone, modified
