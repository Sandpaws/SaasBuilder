"""Markdown + CSV reports from scored clusters."""

from __future__ import annotations

import csv
import time
from pathlib import Path

from . import db
from .scoring import score_all

METHODOLOGY = """\
## How to read the scores

- **Repeated /10**: how often the problem recurs in this sample. Log scale over distinct threads (comments inside \
one thread add a little), recency-weighted. 1 thread is about 1/10; {ref} effective mentions is 10/10. It is relative \
to what you collected, so compare clusters within one report, not across very different samples.
- **Urgency /10**: mean urgency (1-5 per mention, judged by the model) rescaled.
- **Budget /10**: share of mentions with a money signal (willing to pay, states current spend, complains about price).
- **Solutions out there**: saturation 0-10 from a web search of existing products ("n found" are verified against \
real search-result URLs). Competition gap = 10 - saturation.
- **Opportunity /10**: weighted blend ({weights}). If the solution check has not run, the gap is a neutral 5 and the \
row says *unchecked*. Treat Opportunity as a ranking aid, not a verdict: talk to the people before you build.
- **Evidence**: strong = 10+ threads across 2+ communities; moderate = 4+ threads; weak = fewer.
- Pain points are model-extracted from public text, quotes are verified to appear verbatim in the source, and no usernames are stored.
"""


def _date(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _solutions_cell(r: dict) -> str:
    if not r["competition_checked"]:
        return "unchecked"
    return f"{r['saturation']}/10 saturated ({len(r['solutions'])} found)"


def build_report(
    conn,
    cfg: dict,
    now: float | None = None,
    top_n: int | None = None,
    sort: str | None = None,
    category: str | None = None,
    min_threads: int | None = None,
) -> tuple[str, list[dict]]:
    now = now or time.time()
    rc = cfg["report"]
    top_n = top_n or rc["top_n"]
    sort = sort or rc["sort"]
    min_threads = rc["min_threads"] if min_threads is None else min_threads

    scored = score_all(db.clusters_with_members(conn), cfg, now)
    if category:
        scored = [s for s in scored if s["category"] == category]
    scored = [s for s in scored if s["threads"] >= min_threads]
    key = "repetition" if sort == "repetition" else "opportunity"
    scored.sort(key=lambda s: (-s[key], -s["threads"]))
    rows = scored[:top_n]

    counts = db.table_counts(conn)
    span = conn.execute("SELECT MIN(created_utc), MAX(created_utc), COUNT(DISTINCT community) FROM posts").fetchone()
    lines = [
        "# Pain-point report",
        "",
        f"Generated {_date(int(now))}. Sample: **{counts['posts']}** posts/comments from **{span[2]}** communities "
        f"({_date(span[0]) if span[0] else '-'} to {_date(span[1]) if span[1] else '-'}); "
        f"**{counts['pain_points']}** pain-point mentions grouped into **{counts['clusters']}** themes; "
        f"{counts['solution_checks']} theme(s) checked for existing solutions. Sorted by {key}.",
        "",
        "| # | Pain point | Repeated /10 | Opportunity /10 | Threads | Communities | Urgency | Budget | Solutions out there | Trend | Evidence |",
        "|--:|---|--:|--:|--:|--:|--:|--:|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {_cell(r['label'])} | {r['repetition']:.1f} | {r['opportunity']:.1f} | {r['threads']} | "
            f"{r['communities']} | {r['intensity']:.1f} | {r['wtp']:.1f} | {_solutions_cell(r)} | {r['trend']} | {r['evidence']} |"
        )

    lines += ["", "## Details", ""]
    for i, r in enumerate(rows[:10], 1):
        lines += [
            f"### {i}. {r['label']}",
            "",
            f"*{r['category']}* · seen {_date(r['first_seen'])} to {_date(r['last_seen'])} · "
            f"{r['threads']} threads, {r['mentions']} mentions · {r['share_of_pain_threads']:.1%} of all pain threads · "
            f"last 30d: {r['recent_30d']} vs prior 30d: {r['prior_30d']} ({r['trend']}) · avg upvotes {r['avg_upvotes']}",
            "",
        ]
        if r["personas"]:
            lines.append(f"- **Who:** {', '.join(r['personas'])}")
        if r["workarounds"]:
            lines.append(f"- **Workarounds today:** {'; '.join(_cell(w) for w in r['workarounds'])}")
        if r["competitors_named"]:
            lines.append("- **Tools people name:** " + ", ".join(f"{n} ({c})" for n, c in r["competitors_named"]))
        lines.append(f"- **Budget signals:** {r['budget_share']:.0%} of mentions")
        if r["competition_checked"]:
            lines.append(f"- **Existing solutions:** saturation {r['saturation']}/10 (confidence: {r['check_confidence']}). {_cell(r['market_summary'] or '')}")
            for s in r["solutions"]:
                weak = f" Gap: {_cell(s['weakness_or_gap'])}" if s.get("weakness_or_gap") else ""
                price = f" ({_cell(s['pricing_note'])})" if s.get("pricing_note") else ""
                lines.append(f"  - [{_cell(s['name'])}]({s['url']}) [{s['kind']}]{price}: {_cell(s['what_it_does'])}{weak}")
            if r["gap_notes"]:
                lines.append(f"- **Gaps:** {_cell(r['gap_notes'])}")
        else:
            lines.append("- **Existing solutions:** not checked yet (`painpoint solutions`).")
        for q in r["quotes"]:
            lines.append(f'- > "{_cell(q["text"])}" ({q["community"]}, urgency {q["urgency"]}) [source]({q["url"]})')
        lines.append("")

    weights = ", ".join(f"{k} {v:g}" for k, v in cfg["scoring"]["weights"].items())
    lines.append(METHODOLOGY.format(ref=cfg["scoring"]["repetition_ref_mentions"], weights=weights))
    return "\n".join(lines), rows


CSV_FIELDS = [
    "label", "category", "repetition", "opportunity", "intensity", "wtp", "saturation", "competition_checked",
    "threads", "mentions", "communities", "share_of_pain_threads", "avg_urgency", "budget_share", "trend",
    "recent_30d", "prior_30d", "evidence", "avg_upvotes", "first_seen", "last_seen",
]


def write_csv(rows: list[dict], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_FIELDS + ["top_communities", "competitors_named", "existing_solutions", "example_quote", "example_url"])
        for r in rows:
            w.writerow(
                [r[f] for f in CSV_FIELDS]
                + [
                    "; ".join(f"{n} ({c})" for n, c in r["top_communities"]),
                    "; ".join(n for n, _ in r["competitors_named"]),
                    "; ".join(s["name"] for s in r["solutions"]),
                    r["quotes"][0]["text"] if r["quotes"] else "",
                    r["quotes"][0]["url"] if r["quotes"] else "",
                ]
            )
