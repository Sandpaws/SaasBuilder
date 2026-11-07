"""Configuration: built-in defaults, overridden by an optional YAML file."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "db_path": "painpoint.db",
    # claude_code: run the Claude steps through your logged-in Claude Code (`claude -p`), no per-token billing.
    # api:         call the Anthropic API directly (needs ANTHROPIC_API_KEY, billed per token).
    "backend": "claude_code",
    # Models for the `api` backend. Extraction runs once per post, so a small model keeps it cheap.
    "models": {
        "extract": "claude-haiku-5-5",
        "solutions": "claude-opus-5-5",
        "merge": "claude-opus-5-5",    # one call per ~300 cluster labels, so cheap
    },
    # Settings for the `claude_code` backend.
    "cc": {
        "command": "claude",
        "models": {"extract": "haiku", "merge": "sonnet", "solutions": "sonnet"},  # aliases = latest of each
        "effort": "low",               # extraction
        "effort_hard": "medium",       # merge + solution check
        "batch_size": 6,               # posts per `claude -p` call. Measured: 12 skipped a clear pain post, 6 and 3 did not
        "batch_chars": 40000,          # also cap a batch by total characters
        "workers": 2,                  # parallel `claude -p` processes; keep low to respect subscription limits
        "timeout_s": 600,
        "strip_api_key_env": True,     # remove ANTHROPIC_API_KEY from the child so your subscription is used
    },
    "llm": {
        "extract_effort": "low",       # low | medium | high | xhigh | max
        "solutions_effort": "medium",
        "merge_effort": "medium",
        "extract_max_tokens": 8000,    # thinking counts toward max_tokens
        "solutions_max_tokens": 16000,
        "workers": 4,                  # parallel extraction requests
        "max_post_chars": 8000,        # longer posts are cut (and counted in the run summary)
        "max_search_turns": 6,         # pause_turn continuations per solution check
        "max_web_searches": 6,         # web_search max_uses per solution check
        # Server-side refusal fallback (api backend only). "auto" = on for models that support it (not Haiku 5.5).
        # Set false for Bedrock/Vertex/Foundry or if `painpoint doctor` reports a 400 about it.
        "use_fallbacks": "auto",
    },
    # $ per million tokens [input, output]; used ONLY for the --dry-run cost estimate.
    "prices_per_mtok": {
        "claude-opus-5-5": [4.0, 20.0],
        "claude-sonnet-5-5": [2.0, 10.0],
        "claude-haiku-5-5": [0.10, 0.50],
    },
    "reddit": {
        "subreddits": [
            "SaaS", "SideProject",                          # builders and founders
            "SomebodyMakeThis", "AppIdeas", "software",     # people asking for tools to exist
            "smallbusiness", "Entrepreneur", "freelance",   # operators and buyers across industries
            "sysadmin", "selfhosted",                       # IT staff and people leaving paid tools
        ],
        "listing": "top",              # top | hot | new | controversial
        "time_filter": "month",        # hour|day|week|month|year|all (top/controversial only)
        "limit": 100,                  # posts per subreddit
        "min_score": 2,
        "include_comments": True,
        "comments_per_post": 25,
        "comment_min_chars": 80,
        "comment_min_score": 1,
        # Comments are high volume; only keep ones that look like complaints/needs.
        "comment_prefilter": True,
        # Also search each subreddit for complaint phrases (GET /r/{sub}/search); far more on-topic than top posts.
        "search_queries": [
            '"is there a tool"', '"is there an app"', '"wish there was"', '"looking for software"',
            '"alternative to"', '"frustrated with"', '"too expensive"', '"waste hours"',
        ],
        "search_sort": "top",          # relevance | hot | top | new | comments
        "search_time": "year",         # hour|day|week|month|year|all
        "search_limit": 25,            # posts per query per subreddit
    },
    # Hacker News via the public Algolia search API. Three passes:
    #  1. complaint phrases in comments and "Ask HN" posts (`queries` x `tags`);
    #  2. extra phrases searched in Ask HN posts only (`ask_queries`): too noisy in comments, useful as questions;
    #  3. `threads`: Ask HN threads that ask people for their problems; every top-level answer is collected.
    # Phrases were picked from hit counts on the live API (2026-11-07); first-run yield was 35% for comments and
    # 62% for Ask HN posts.
    "hackernews": {
        "queries": [
            '"wish there was"', '"is there a tool"', '"is there an app"', '"looking for software"',
            '"frustrated with"', '"waste hours"', '"hours every week"', '"manual process"',
            '"I would pay for"', '"I\'d pay for"', '"would happily pay"', '"take my money"',
            '"why is there no"', '"there is no good"', '"there\'s no good"', '"nothing out there"',
            '"does anyone know of a"', '"we built our own"', '"ended up building"', '"ended up writing"',
            '"wrote a script"', '"so frustrating"', '"drives me crazy"', '"biggest pain"',
            '"I hate how"', '"hard to find a"', '"switched away from"',
        ],
        "tags": ["comment", "ask_hn"],  # Algolia tags; each query runs once per tag
        # "too expensive" and "alternative to" are mostly chatter in comments (and "too expensive" inflates budget
        # scores), but "Ask HN: alternative to X?" posts are exactly what we want.
        "ask_queries": ['"alternative to"', '"too expensive"', '"is there a"'],
        "sort": "search",               # search = relevance, search_by_date = newest first
        "since_days": 730,
        "max_per_query": 100,           # hits per (tag, query)
        "min_chars": 80,                # shortest comment worth analysing
        "delay_s": 1.0,                 # pause between requests
        "threads": {
            "enabled": True,
            # Searched in Ask HN titles (unquoted, so loose); `title_pattern` then keeps only real asks.
            "title_queries": [
                "wish existed", "would pay for", "pay to solve", "biggest pain", "biggest problem", "pain points",
                "should be automated", "still do manually", "tools missing", "problem worth solving",
                "worst saas", "worst software", "dislike about", "frustrating", "annoying", "tedious",
                "waste time", "what tool", "what software", "what saas", "need a tool",
            ],
            "title_pattern": (
                r"wish(ed)? .{0,20}(existed|there (was|were))|would (you )?(happily )?pay|pay to solve"
                r"|pain ?points?|biggest (pain|problem|frustration|challenge|headache)|frustrat|annoy|tedious"
                r"|manually|automated|missing|worth solving|dislike|worst (saas|software|tool|app|part)"
                r"|waste (of )?time|need(ed)? an? (tool|app)|sucks"
            ),
            # Only open questions: each answer is then a different person's problem. A statement ("Tell HN: X is
            # frustrating") or a yes/no question ("Would you pay for X?") gets answers that all discuss one topic,
            # and counting each as a separate voice made one such thread the #1 theme in the first full run.
            "require_question": True,  # a "?" in the title, or an "Ask HN:" prefix
            "exclude_pattern": (
                r"^(ask hn:\s*)?(would|is|are|do|does|did|should|can|could|will|has|have)\b"
                r"|unlimited (time|money|resources)|who is hiring|who wants to be hired|freelancer\?"
            ),
            "since_days": 1460,
            "min_comments": 25,
            "max_threads": 40,          # biggest threads first
            "max_comments_per_thread": 150,
        },
    },
    # Scrapling-based sources (reviews, forums, ...). See config.example.yaml.
    "web_sources": [],
    "cluster": {
        "distance_threshold": 0.8,     # TF-IDF cosine distance for the first, high-precision pass
        "max_global_items": 6000,      # above this, cluster per category to bound memory
        "merge_batch": 300,            # cluster labels per LLM merge call (largest clusters first)
        # Passes over all labels: the 1st batches the largest clusters together, later ones batch similar labels
        # together, so paraphrases split across batches still meet. Stops early once a pass merges nothing.
        "merge_passes": 3,
    },
    "scoring": {
        "half_life_days": 180,         # 0 disables recency weighting
        "repetition_ref_mentions": 100,  # effective mentions that map to a 10/10 repetition score
        "extra_comment_weight": 0.25,  # extra mentions inside one thread count this much each
        "extra_comment_cap": 8,
        "wtp_full_share": 0.35,        # share of mentions with a budget signal that = 10/10
        "min_threads_for_solution_check": 2,
        # Pull each theme's intensity and WTP toward the sample average as if it had this many extra typical
        # threads, so a one-off angry post can't outrank a problem seen in many threads. 0 turns it off.
        "prior_threads": 3,
        "weights": {"repetition": 0.40, "intensity": 0.20, "wtp": 0.15, "competition_gap": 0.25},
    },
    "report": {
        "top_n": 25,
        "min_threads": 1,
        "sort": "opportunity",         # opportunity | repetition
    },
}


def _merge(base: dict, over: dict) -> dict:
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Defaults, overlaid with the YAML at `path` if given. A missing explicit path is an error."""
    cfg = copy.deepcopy(DEFAULTS)
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"config file not found: {p}")
        with p.open(encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
        _merge(cfg, user)
    return cfg
