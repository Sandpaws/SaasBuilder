"""Run pain-point extraction over stored posts (resumable, parallel, cost-aware)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from . import db
from .llm import LLM, FatalLLMError

_WS = re.compile(r"\s+")
# Prompt + schema overhead per request and expected output size, used only for the cost estimate.
_OVERHEAD_TOKENS = 900
_OUTPUT_TOKENS = 600  # JSON plus low-effort thinking; deliberately on the high side
MAX_CONSECUTIVE_ERRORS = 8


@dataclass
class ExtractSummary:
    attempted: int = 0
    ok: int = 0
    pain: int = 0
    promo: int = 0
    refused: int = 0
    cut_off: int = 0
    errors: int = 0
    inputs_truncated: int = 0
    quotes_dropped: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    aborted: str | None = None
    notional_usd: float | None = None  # claude_code backend: list-price value of the work (not billed on a subscription)


def _norm(s: str) -> str:
    return _WS.sub(" ", s.lower()).strip()


def make_batches(rows: list[dict], max_items: int, max_chars: int, per_post_cap: int) -> list[list[dict]]:
    """Group rows into batches of at most `max_items` posts and (if max_chars) about `max_chars` characters."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    chars = 0
    for row in rows:
        size = min(len(row["title"]) + len(row["body"]), per_post_cap)
        if current and (len(current) >= max_items or (max_chars and chars + size > max_chars)):
            batches.append(current)
            current, chars = [], 0
        current.append(row)
        chars += size
    if current:
        batches.append(current)
    return batches


def estimate_cost(rows, cfg: dict) -> dict:
    chars = sum(min(len(r["title"]) + len(r["body"]), cfg["llm"]["max_post_chars"]) for r in rows)
    in_tok = chars // 4 + _OVERHEAD_TOKENS * len(rows)
    out_tok = _OUTPUT_TOKENS * len(rows)
    model = cfg["models"]["extract"]
    price = cfg["prices_per_mtok"].get(model)
    cost = (in_tok * price[0] + out_tok * price[1]) / 1e6 if price else None
    return {"posts": len(rows), "input_tokens": in_tok, "output_tokens": out_tok, "model": model, "usd": cost}


def run_extraction(
    conn,
    llm: LLM,
    cfg: dict,
    limit: int | None = None,
    retry_errors: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ExtractSummary:
    retry = ("error", "truncated") if retry_errors else ()
    rows = [dict(r) for r in db.pending_posts(conn, retry, limit)]
    summary = ExtractSummary()
    model = cfg["models"]["extract"]
    consecutive_errors = 0
    done = 0

    def handle(row: dict, result) -> None:
        nonlocal consecutive_errors
        summary.attempted += 1
        summary.inputs_truncated += int(result.input_truncated)
        if result.status == "ok":
            consecutive_errors = 0
            data = result.data
            haystack = _norm(row["title"] + " " + row["body"])
            if data["evidence_quote"] and _norm(data["evidence_quote"]) not in haystack:
                data["evidence_quote"] = ""  # never store a quote the model made up
                summary.quotes_dropped += 1
            db.save_extraction(conn, row["id"], "ok", model, data)
            summary.ok += 1
            summary.pain += int(data["is_pain_point"] and not data["is_self_promotion"])
            summary.promo += int(data["is_self_promotion"])
        else:
            consecutive_errors = consecutive_errors + 1 if result.status == "error" else 0
            db.save_extraction(conn, row["id"], result.status, model, None, result.error)
            if result.status == "refused":
                summary.refused += 1
            elif result.status == "truncated":
                summary.cut_off += 1
            else:
                summary.errors += 1

    batches = make_batches(rows, llm.batch_size, llm.batch_chars, cfg["llm"]["max_post_chars"])
    with ThreadPoolExecutor(max_workers=llm.workers) as pool:
        futures = {pool.submit(llm.extract_batch, b): b for b in batches}
        try:
            for fut in as_completed(futures):
                batch = futures[fut]
                for row, result in zip(batch, fut.result()):
                    handle(row, result)
                    done += 1
                conn.commit()
                if progress:
                    progress(f"  extracted {done}/{len(rows)}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    summary.aborted = f"{MAX_CONSECUTIVE_ERRORS} errors in a row (last: see extractions.error)"
                    break
        except FatalLLMError as exc:
            summary.aborted = f"stopped: {exc}"
        finally:
            for f in futures:
                f.cancel()
    conn.commit()
    summary.input_tokens, summary.output_tokens = llm.usage["input"], llm.usage["output"]
    summary.notional_usd = llm.usage.get("notional_usd")
    return summary
