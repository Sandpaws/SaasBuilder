"""Scores for each pain-point cluster. Pure functions: no I/O, fully unit-tested.

Repetition /10   log-scaled count of distinct threads (recency-weighted; extra comments in the same thread
                 count a little). repetition_ref_mentions effective mentions = 10/10.
Intensity  /10   mean urgency (1-5) rescaled.
WTP        /10   share of mentions carrying a budget signal (pay / current spend / price complaint).
Competition gap  10 - saturation, where saturation (0-10) comes from the web-search solution check.
Opportunity /10  weighted blend of the four. If the solution check has not run, the gap defaults to a
                 neutral 5 and the row is flagged competition_checked=False.

"Repetition" is relative to what you collected: it measures how often a problem recurs in YOUR sample.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

DAY = 86400
BUDGET_WEIGHTS = {"willing_to_pay": 1.0, "mentions_current_spend": 0.8, "price_complaint": 0.6, "none": 0.0}
NEUTRAL_GAP = 5.0


def recency_weight(age_days: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def _top(counter: Counter, n: int) -> list[str]:
    return [k for k, _ in counter.most_common(n)]


def score_cluster(cluster: dict, cfg: dict, now: float, total_pain_threads: int) -> dict:
    sc = cfg["scoring"]
    members = cluster["members"]

    threads: dict[str, list[dict]] = defaultdict(list)
    for m in members:
        threads[m["thread_id"]].append(m)

    effective = 0.0
    newest_per_thread = []
    for ms in threads.values():
        newest = max(m["created_utc"] for m in ms)
        newest_per_thread.append(newest)
        weight = recency_weight((now - newest) / DAY, sc["half_life_days"])
        extras = min(len(ms) - 1, sc["extra_comment_cap"])
        effective += weight * (1 + sc["extra_comment_weight"] * extras)
    repetition = min(10.0, 10 * math.log10(1 + effective) / math.log10(1 + sc["repetition_ref_mentions"]))

    avg_urgency = sum(m["urgency"] for m in members) / len(members)
    intensity = (avg_urgency - 1) / 4 * 10
    budget_share = sum(1 for m in members if m["budget_signal"] != "none") / len(members)
    wtp_raw = sum(BUDGET_WEIGHTS.get(m["budget_signal"], 0.0) for m in members) / len(members)
    wtp = 10 * min(1.0, wtp_raw / sc["wtp_full_share"])

    check = cluster.get("check")
    saturation = check["saturation"] if check and check.get("status") == "ok" and check.get("saturation") is not None else None
    gap = (10 - saturation) if saturation is not None else NEUTRAL_GAP
    w = sc["weights"]
    opportunity = (
        w["repetition"] * repetition + w["intensity"] * intensity + w["wtp"] * wtp + w["competition_gap"] * gap
    ) / sum(w.values())

    recent = sum(1 for t in newest_per_thread if now - t <= 30 * DAY)
    prior = sum(1 for t in newest_per_thread if 30 * DAY < now - t <= 60 * DAY)
    if recent == 0 and prior == 0:
        trend = "older"
    elif prior == 0:
        trend = "new"
    elif recent >= 1.5 * prior:
        trend = "rising"
    elif recent <= prior / 1.5:
        trend = "falling"
    else:
        trend = "steady"

    n_threads = len(threads)
    communities = Counter(m["community"] for m in members)
    competitors: Counter = Counter()
    display: dict[str, str] = {}
    for m in members:
        for c in m["competitors"]:
            key = c.strip().lower()
            if key:
                competitors[key] += 1
                display.setdefault(key, c.strip())
    workarounds = Counter(m["workaround"].strip() for m in members if m.get("workaround"))
    personas = Counter(m["persona"].strip().lower() for m in members if m.get("persona") and m["persona"] != "unknown")

    seen_quotes: set[str] = set()
    quotes = []
    for m in sorted(members, key=lambda x: (-x["urgency"], -x["upvotes"])):
        q = (m.get("quote") or "").strip()
        if q and q not in seen_quotes:
            seen_quotes.add(q)
            quotes.append({"text": q, "url": m["url"], "community": m["community"], "urgency": m["urgency"]})
        if len(quotes) == 3:
            break

    evidence = "strong" if n_threads >= 10 and len(communities) >= 2 else "moderate" if n_threads >= 4 else "weak"

    return {
        "label": cluster["label"],
        "category": cluster["category"],
        "mentions": len(members),
        "threads": n_threads,
        "share_of_pain_threads": n_threads / total_pain_threads if total_pain_threads else 0.0,
        "communities": len(communities),
        "top_communities": communities.most_common(3),
        "repetition": round(repetition, 2),
        "intensity": round(intensity, 2),
        "wtp": round(wtp, 2),
        "avg_urgency": round(avg_urgency, 2),
        "budget_share": round(budget_share, 3),
        "saturation": saturation,
        "competition_gap": round(gap, 2),
        "competition_checked": saturation is not None,
        "opportunity": round(opportunity, 2),
        "evidence": evidence,
        "trend": trend,
        "recent_30d": recent,
        "prior_30d": prior,
        "first_seen": min(m["created_utc"] for m in members),
        "last_seen": max(m["created_utc"] for m in members),
        "avg_upvotes": round(sum(max(m["upvotes"], 0) for m in members) / len(members), 1),
        "competitors_named": [(display[k], n) for k, n in competitors.most_common(5)],
        "workarounds": _top(workarounds, 3),
        "personas": _top(personas, 3),
        "quotes": quotes,
        "solutions": (check or {}).get("solutions", []) if saturation is not None else [],
        "market_summary": (check or {}).get("market_summary") if saturation is not None else None,
        "gap_notes": (check or {}).get("gap_notes") if saturation is not None else None,
        "check_confidence": (check or {}).get("confidence") if saturation is not None else None,
    }


def score_all(clusters: list[dict], cfg: dict, now: float) -> list[dict]:
    total_threads = len({m["thread_id"] for c in clusters for m in c["members"]})
    return [score_cluster(c, cfg, now, total_threads) for c in clusters]
