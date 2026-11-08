"""Reddit collector that goes through Agent Reach instead of Reddit's official Data API.

Agent Reach (github.com/Panniantong/Agent-Reach) runs OpenCLI in its own browser, logged in to your Reddit account,
and returns one JSON envelope per command:

    agent-reach reddit subreddit <name> --limit N     hot posts of a subreddit
    agent-reach reddit search <query> --limit N       site search (we add `subreddit:<name>` to the query)
    agent-reach reddit read <post id>                 the post and its top comments

This is NOT the access Reddit approved for this project (that is the PRAW collector, reddit_api.py). Read "Reddit's
rules" in the README before using it.

Install Agent Reach from GitHub, never from PyPI: the PyPI package called `agent-reach` is a different project.

Usernames are never stored. Agent Reach returns them; this module drops them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from .common import Post, looks_like_pain, scrub
from .reddit_api import _REMOVED, submission_to_post

INSTALL_HINT = (
    "Install Agent Reach from GitHub (the PyPI package with the same name is a different project): "
    "uv tool install https://github.com/Panniantong/Agent-Reach/releases/latest/download/agent-reach.zip ; "
    "then: agent-reach setup && agent-reach add reddit && agent-reach login reddit"
)

# After these, Agent Reach says to stop: no retry, no other backend or account. The run ends and keeps what it has.
STOP_CODES = frozenset({"NEED_SETUP", "NEED_LOGIN", "PLATFORM_BLOCKED", "RATE_LIMITED", "BUSY"})
# Other failures are skipped, but this many in a row ends the run instead of hammering a broken channel.
MAX_FAILURES_IN_A_ROW = 3
# Agent Reach reports an empty result as UPSTREAM_BROKEN with this message.
_EMPTY_RESULT = "returned no items"
_WINDOW_S = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 31 * 86400, "year": 366 * 86400}


class AgentReachError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.hint = hint

    @property
    def stops_run(self) -> bool:
        return self.code in STOP_CODES


def parse_envelope(stdout: str) -> dict | None:
    """The one JSON object Agent Reach prints (skipping any warning lines before it), or None."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and "ok" in doc:
            return doc
    return None


class AgentReach:
    """Runs `agent-reach reddit <action> ...` through the OpenCLI backend and returns the envelope's items."""

    def __init__(self, command: str = "agent-reach", timeout_s: float = 90.0):
        self.command = command
        self.timeout_s = float(timeout_s)
        self.failures_in_a_row = 0  # across the whole run, so a broken channel stops it whatever the subreddit

    def call(self, action: str, *args: Any) -> list[dict]:
        try:
            items = self._call(action, *args)
        except AgentReachError:
            self.failures_in_a_row += 1
            raise
        self.failures_in_a_row = 0
        return items

    def _call(self, action: str, *args: Any) -> list[dict]:
        # --backend opencli: structured rows (ids, scores, dates, body text) instead of anonymous web-search snippets.
        argv = [self.command, "reddit", action, *map(str, args), "--backend", "opencli", "--timeout", f"{self.timeout_s:g}"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_s + 30)
        except FileNotFoundError:
            raise AgentReachError("NEED_SETUP", f"`{self.command}` was not found", INSTALL_HINT) from None
        except subprocess.TimeoutExpired:
            raise AgentReachError("TIMEOUT", f"agent-reach did not answer within {self.timeout_s + 30:.0f}s") from None
        envelope = parse_envelope(proc.stdout)
        if envelope is None:
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            raise AgentReachError("UPSTREAM_BROKEN", f"no JSON result from agent-reach (exit {proc.returncode}): {detail}")
        if envelope.get("ok"):
            return [i for i in envelope.get("items") or [] if isinstance(i, dict)]
        err = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
        code = str(err.get("code") or "UPSTREAM_BROKEN")
        message = str(err.get("message") or "")
        if code == "NOT_FOUND" or (code == "UPSTREAM_BROKEN" and _EMPTY_RESULT in message):
            return []
        raise AgentReachError(code, message, str(err.get("hint") or ""))


# ---------------------------------------------------------------- converters


def _raw(item: dict) -> dict:
    raw = item.get("raw")
    return raw if isinstance(raw, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def item_to_post(item: dict, subreddit: str) -> Post | None:
    """A search/listing item -> Post. Same id scheme and text handling as the PRAW collector, so items collected
    either way never duplicate and the API deletion sync can check them. Returns None for rows from another
    subreddit (site search can stray), stickied/empty posts and rows without a Reddit id."""
    raw = _raw(item)
    sid = str(raw.get("id") or "").strip().lower()
    if not sid.isalnum():
        return None
    community = str(raw.get("subreddit") or "").removeprefix("r/")
    if community and community.lower() != subreddit.lower():
        return None
    url = str(raw.get("url") or item.get("url") or "")
    permalink = url.removeprefix("https://www.reddit.com") if url.startswith("https://www.reddit.com/") else ""
    sub = SimpleNamespace(
        id=sid,
        title=str(raw.get("title") or item.get("title") or ""),
        selftext=str(raw.get("selftext") or ""),
        permalink=permalink or f"/r/{subreddit}/comments/{sid}/",
        created_utc=_int(raw.get("created_utc")),
        score=_int(raw.get("score", raw.get("upvotes"))),
        num_comments=_int(raw.get("comments")),
        stickied=False,
    )
    return submission_to_post(sub, f"r/{subreddit}")


def _unindent(text: str, depth: int) -> str:
    """OpenCLI prefixes every line of a depth-N reply with N*2 spaces and '> '. Remove exactly that."""
    if depth <= 0:
        return text
    prefix = "  " * depth + "> "
    return "\n".join(line[len(prefix):] if line.startswith(prefix) else line for line in text.split("\n"))


def comment_id(thread_id: str, body: str) -> str:
    """Agent Reach gives comments no Reddit id, so ours comes from the text: an edited comment gets a new id."""
    return f"{thread_id}:c{hashlib.sha1(body.encode('utf-8')).hexdigest()[:12]}"


def thread_comments(item: dict, post: Post, cfg: dict) -> list[Post]:
    """Comments from a `read` item, filtered like the PRAW collector's. Authors are read only to skip the
    '[+N more replies]' stubs (they have none) and are never stored."""
    rows = _raw(item).get("comments")
    out: list[Post] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("author"):
            continue
        body = scrub(_unindent(str(row.get("text") or ""), _int(str(row.get("type") or "L0")[1:])))
        score = _int(row.get("score"))
        if body.strip().lower() in _REMOVED:
            continue
        if len(body) < cfg["comment_min_chars"] or score < cfg["comment_min_score"]:
            continue
        if cfg["comment_prefilter"] and not looks_like_pain(body):
            continue
        out.append(Post(
            id=comment_id(post.thread_id, body),
            source="reddit",
            community=post.community,
            kind="comment",
            thread_id=post.thread_id,
            url=post.url,                      # no comment permalink without an id; link to the thread
            title=f"Re: {post.title}"[:200],
            body=body,
            created_utc=post.created_utc,      # comment dates are not returned; use the thread's
            upvotes=score,
            num_comments=0,
        ))
        if len(out) >= cfg["comments_per_post"]:
            break
    return out


# ---------------------------------------------------------------- one subreddit


@dataclass
class SubredditResult:
    posts: list[Post] = field(default_factory=list)
    threads_read: dict[str, set[str]] = field(default_factory=dict)  # thread id -> comment ids it has now
    warnings: list[str] = field(default_factory=list)
    stop: AgentReachError | None = None


def collect_subreddit(ar: AgentReach, name: str, cfg: dict, ar_cfg: dict, now: float | None = None) -> SubredditResult:
    """Hot listing, one search per complaint phrase, then the comments of the highest-scoring threads.

    Agent Reach's search cannot sort by top or limit the time range, so search results older than
    `search_time` are dropped here. Errors become warnings; a stop code (or too many failures in a row) ends the
    run, keeping whatever was gathered."""
    res = SubredditResult()
    now = time.time() if now is None else now
    window = _WINDOW_S.get(cfg.get("search_time", "all"))
    seen: set[str] = set()

    def run(action: str, *args: Any) -> list[dict] | None:
        try:
            return ar.call(action, *args)
        except AgentReachError as exc:
            if exc.stops_run:
                res.stop = exc
            elif ar.failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                res.stop = AgentReachError(
                    exc.code, f"{ar.failures_in_a_row} Agent Reach calls failed in a row, last: {exc.message}", exc.hint)
            else:
                res.warnings.append(f"{action} {' '.join(map(str, args))}: {exc.code} {exc.message}")
            return None

    def take(items: list[dict], from_search: bool) -> None:
        for item in items:
            post = item_to_post(item, name)
            if post is None or post.id in seen or post.upvotes < cfg["min_score"]:
                continue
            if from_search and window and post.created_utc and post.created_utc < now - window:
                continue
            seen.add(post.id)
            res.posts.append(post)

    calls = [("subreddit", False, (name, "--limit", cfg["limit"]))]
    calls += [("search", True, (f"subreddit:{name} {q}", "--limit", cfg["search_limit"]))
              for q in cfg.get("search_queries") or []]
    for action, from_search, args in calls:
        items = run(action, *args)
        if res.stop:
            return res
        take(items or [], from_search)

    if not cfg["include_comments"]:
        return res
    threads = sorted((p for p in res.posts if p.num_comments > 0), key=lambda p: -p.upvotes)
    for post in threads[: ar_cfg["comment_threads"]]:
        items = run("read", post.id.removeprefix("reddit:t3_"))
        if res.stop:
            return res
        if items is None:
            continue
        comments = thread_comments(items[0], post, cfg) if items else []
        res.threads_read[post.thread_id] = {c.id for c in comments}
        res.posts.extend(comments)
    return res
