"""Configuration: built-in defaults, overridden by an optional YAML file."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "db_path": "painpoint.db",
    # Models. Extraction runs once per post, so it dominates cost -- see README "Cost".
    "models": {
        "extract": "claude-opus-5-5",
        "solutions": "claude-opus-5-5",
        "merge": "claude-opus-5-5",    # one call per ~300 cluster labels, so cheap
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
        # Server-side refusal fallback (recommended for claude-opus-5-5 on the Claude API).
        # Set false for Bedrock/Vertex/Foundry or if `painpoint doctor` reports a 400 about it.
        "use_fallbacks": True,
    },
    # $ per million tokens [input, output]; used ONLY for the --dry-run cost estimate.
    "prices_per_mtok": {
        "claude-opus-5-5": [4.0, 20.0],
        "claude-sonnet-5-5": [2.0, 10.0],
        "claude-haiku-5-5": [0.10, 0.50],
    },
    "reddit": {
        "subreddits": ["SaaS", "SideProject", "microsaas", "startups"],
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
    },
    # Scrapling-based sources (reviews, forums, ...). See config.example.yaml.
    "web_sources": [],
    "cluster": {
        "distance_threshold": 0.8,     # TF-IDF cosine distance for the first, high-precision pass
        "max_global_items": 6000,      # above this, cluster per category to bound memory
        "merge_batch": 300,            # cluster labels per LLM merge call (largest clusters first)
    },
    "scoring": {
        "half_life_days": 180,         # 0 disables recency weighting
        "repetition_ref_mentions": 100,  # effective mentions that map to a 10/10 repetition score
        "extra_comment_weight": 0.25,  # extra mentions inside one thread count this much each
        "extra_comment_cap": 8,
        "wtp_full_share": 0.35,        # share of mentions with a budget signal that = 10/10
        "min_threads_for_solution_check": 2,
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
