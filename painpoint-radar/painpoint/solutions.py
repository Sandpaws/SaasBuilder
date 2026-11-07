"""Check whether the top pain points already have solutions out there (web search + verification)."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from . import db
from .llm import LLM
from .scoring import score_all


@dataclass
class SolutionSummary:
    checked: int = 0
    ok: int = 0
    no_results: int = 0
    errors: int = 0
    skipped_existing: int = 0


def _context(cluster: dict) -> dict:
    members = cluster["members"]
    competitors = Counter(c.strip() for m in members for c in m["competitors"] if c.strip())
    personas = Counter(m["persona"].strip() for m in members if m.get("persona") and m["persona"] != "unknown")
    ranked = sorted(members, key=lambda m: (-m["urgency"], -m["upvotes"]))
    quotes = []
    for m in ranked:
        q = (m.get("quote") or "").strip()
        if q and q not in quotes:
            quotes.append(q)
        if len(quotes) == 3:
            break
    return {
        "personas": [p for p, _ in personas.most_common(3)],
        "competitors": [c for c, _ in competitors.most_common(5)],
        "quotes": quotes,
    }


def run_solution_checks(
    conn, llm: LLM, cfg: dict, top: int = 15, force: bool = False, progress: Callable[[str], None] | None = None
) -> SolutionSummary:
    clusters = db.clusters_with_members(conn)
    scored = score_all(clusters, cfg, time.time())
    by_label = {c["label"]: c for c in clusters}
    min_threads = cfg["scoring"]["min_threads_for_solution_check"]
    # Same order as the report (opportunity, with the market gap held neutral), so `--top N` checks the themes
    # at the top of the report rather than the most-repeated ones further down.
    order = sorted(scored, key=lambda s: (-s["opportunity_before_check"], -s["repetition"]))
    ranked = [s for s in order if s["threads"] >= min_threads][:top]

    summary = SolutionSummary()
    model = cfg["models"]["solutions"]
    for i, s in enumerate(ranked, 1):
        cluster = by_label[s["label"]]
        existing = cluster.get("check")
        if existing and existing["status"] == "ok" and not force:
            summary.skipped_existing += 1
            continue
        if progress:
            progress(f"  [{i}/{len(ranked)}] searching: {s['label']}")
        result = llm.search_solutions(s["label"], _context(cluster))
        summary.checked += 1
        if result.status == "ok":
            db.save_solution_check(conn, s["label"], "ok", model, result.data)
            summary.ok += 1
        else:
            db.save_solution_check(conn, s["label"], result.status, model, {"market_summary": result.error})
            summary.no_results += int(result.status == "no_results")
            summary.errors += int(result.status == "error")
    return summary
