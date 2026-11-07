"""Scores for each pain-point cluster. Pure functions: no I/O, fully unit-tested.

Repetition /10   log-scaled count of distinct threads (recency-weighted; extra comments in the same thread
                 count a little). repetition_ref_mentions effective mentions = 10/10.
Intensity  /10   mean urgency (1-5) rescaled.
WTP        /10   share of mentions carrying a budget signal (pay / current spend / price complaint).
Competition gap  10 - saturation, where saturation (0-10) comes from the web-search solution check.
Opportunity /10  weighted blend of the four. If the solution check has not run, the gap defaults to a
                 neutral 5 and the row is flagged competition_checked=False.

Small themes: intensity and WTP are averages, so a theme seen once with one angry, budget-mentioning post would
score 10/10 on both and outrank themes seen in many threads. score_all() therefore pulls both toward the sample-wide
average, as if each theme also had `prior_threads` typical threads (a standard Bayesian average): one thread
counts for 1/(1+3) of its own value, twelve threads for 12/15. score_cluster() on its own scores without that pull.

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


def _wtp_raw(members: list[dict]) -> float:
    return sum(BUDGET_WEIGHTS.get(m["budget_signal"], 0.0) for m in members) / len(members)


def sample_priors(clusters: list[dict]) -> dict | None:
    """Sample-wide averages that small themes are pulled toward (see the module docstring)."""
    members = [m for c in clusters for m in c["members"]]
    if not members:
        return None
    return {"urgency": sum(m["urgency"] for m in members) / len(members), "wtp_raw": _wtp_raw(members)}


def score_cluster(
    cluster: dict, cfg: dict, now: float, total_pain_threads: int, priors: dict | None = None
) -> dict:
    sc = cfg["scoring"]
    members = cluster["members"]

    threads: dict[str, list[dict]] = defaultdict(list)
    for m in members:
        threads[m["thread_id"]].append(m)
    n_threads = len(threads)
    # Weight of this theme's own averages against the sample-wide ones; 1.0 (no pull) when scored alone.
    k = sc.get("prior_threads", 0) if priors else 0
    own = n_threads / (n_threads + k)

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
    budget_share = sum(1 for m in members if m["budget_signal"] != "none") / len(members)

    def to_intensity(urgency: float) -> float:
        return (urgency - 1) / 4 * 10

    def to_wtp(raw: float) -> float:
        return 10 * min(1.0, raw / sc["wtp_full_share"])

    intensity = to_intensity(avg_urgency)
    wtp = to_wtp(_wtp_raw(members))
    if priors:  # pull the 0-10 scores (after the WTP cap, or the cap would undo the pull)
        intensity = own * intensity + (1 - own) * to_intensity(priors["urgency"])
        wtp = own * wtp + (1 - own) * to_wtp(priors["wtp_raw"])

    check = cluster.get("check")
    saturation = check["saturation"] if check and check.get("status") == "ok" and check.get("saturation") is not None else None
    gap = (10 - saturation) if saturation is not None else NEUTRAL_GAP
    w = sc["weights"]

    def blend(gap_value: float) -> float:
        return (
            w["repetition"] * repetition + w["intensity"] * intensity + w["wtp"] * wtp + w["competition_gap"] * gap_value
        ) / sum(w.values())

    opportunity = blend(gap)
    # The same score with the gap held neutral: what `solutions` ranks by, so checks follow the report's order
    # before any check has run, and running more checks does not reshuffle which themes come next.
    opportunity_before_check = blend(NEUTRAL_GAP)

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
        "opportunity_before_check": round(opportunity_before_check, 2),
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
    priors = sample_priors(clusters)
    return [score_cluster(c, cfg, now, total_threads, priors) for c in clusters]
