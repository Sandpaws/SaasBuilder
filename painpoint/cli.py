"""Command-line entry point: painpoint <command>."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import db
from .config import load_config


def _llm(cfg):
    if cfg["backend"] == "claude_code":
        from .cc_backend import ClaudeCodeLLM

        return ClaudeCodeLLM(cfg)
    if cfg["backend"] != "api":
        sys.exit(f"unknown backend {cfg['backend']!r}; use 'claude_code' or 'api'")
    import anthropic

    from .llm import LLM

    return LLM(anthropic.Anthropic(), cfg)


def _conn(cfg):
    conn = db.connect(cfg["db_path"])
    db.init(conn)
    return conn


def cmd_init(args, cfg):
    _conn(cfg)
    print(f"database ready: {cfg['db_path']}")


def cmd_collect(args, cfg):
    conn = _conn(cfg)
    if args.source_type == "reddit":
        from .collect import reddit_api

        reddit = reddit_api.make_client()
        names = args.subreddit or cfg["reddit"]["subreddits"]
        failed = []
        for name in names:
            posts, error = reddit_api.collect_safely(reddit, name, cfg["reddit"])
            ins, skip = db.insert_posts(conn, posts)
            print(f"r/{name}: {ins} new, {skip} already stored or duplicate")
            if error:
                failed.append(name)
                print(f"  WARNING r/{name}: {error}")
        if failed:
            print(f"{len(failed)} subreddit(s) had problems: {', '.join('r/' + n for n in failed)}")
    elif args.source_type == "web":
        from .collect import web_spider

        sources = [s for s in cfg["web_sources"] if not args.source or s["name"] in args.source]
        if not sources:
            sys.exit("no matching web_sources in the config")
        for src in sources:
            posts, stats = web_spider.collect_web_source(src)
            ins, skip = db.insert_posts(conn, posts)
            print(f"{src['name']}: {ins} new, {skip} duplicate | {stats['requests']} requests")
            if stats["robots_disallowed"]:
                print(f"  WARNING: robots.txt disallowed {stats['robots_disallowed']} request(s); that content was not fetched.")
            if stats["items"] == 0:
                print("  WARNING: 0 items. Check the selectors, or whether the site blocks or forbids crawling.")
    else:  # import
        from .collect import importer

        ins, skip = db.insert_posts(conn, importer.load_posts(args.path, args.source_name))
        print(f"imported: {ins} new, {skip} duplicate")


def cmd_extract(args, cfg):
    from .extract import estimate_cost, make_batches, run_extraction

    conn = _conn(cfg)
    retry = ("error", "truncated") if args.retry_errors else ()
    rows = [dict(r) for r in db.pending_posts(conn, retry, args.limit)]
    if not rows:
        print("nothing to extract.")
        return
    est = estimate_cost([dict(r) for r in rows], cfg)
    if cfg["backend"] == "claude_code":
        cc = cfg["cc"]
        calls = len(make_batches(rows, cc["batch_size"], cc["batch_chars"], cfg["llm"]["max_post_chars"]))
        print(f"{len(rows)} posts in ~{calls} `{cc['command']} -p` calls (model alias '{cc['models']['extract']}', "
              f"~{est['input_tokens']:,} in / ~{est['output_tokens']:,} out tokens). No API charges: this counts "
              "toward your Claude subscription usage limits. If you hit them the run stops and can be resumed.")
        if args.max_usd is not None:
            print("note: --max-usd only applies to the api backend and is ignored here.")
    else:
        cost = f"~${est['usd']:.2f}" if est["usd"] is not None else "unknown (no price configured)"
        print(f"{est['posts']} posts, model {est['model']}, rough estimate {cost} "
              f"(~{est['input_tokens']:,} in / ~{est['output_tokens']:,} out tokens)")
    if args.dry_run:
        return
    if (cfg["backend"] == "api" and args.max_usd is not None and est["usd"] is not None
            and est["usd"] > args.max_usd):
        sys.exit(f"estimate exceeds --max-usd {args.max_usd}; lower --limit, change the model, or raise the cap.")
    s = run_extraction(conn, _llm(cfg), cfg, args.limit, args.retry_errors, progress=print)
    extra = f" | list-price value ${s.notional_usd:.3f} (not billed on a subscription)" if s.notional_usd else ""
    print(f"done: {s.ok} ok ({s.pain} pain points, {s.promo} self-promo), {s.refused} refused, "
          f"{s.cut_off} cut off, {s.errors} errors | {s.inputs_truncated} long posts shortened, "
          f"{s.quotes_dropped} unverifiable quotes dropped | tokens in/out {s.input_tokens:,}/{s.output_tokens:,}{extra}")
    if s.aborted:
        sys.exit(f"{s.aborted}\nProgress is saved; re-run `painpoint extract` to continue where it stopped.")


def cmd_cluster(args, cfg):
    from .cluster import cluster_pain_points

    r = cluster_pain_points(_conn(cfg), cfg, None if args.no_merge else _llm(cfg))
    print(f"{r['mentions']} pain-point mentions -> {r['before_merge']} clusters (keyword pass)"
          + ("" if args.no_merge else f" -> {r['clusters']} after semantic merge ({r['merged_groups']} merges)"))
    if r["merge_errors"]:
        print(f"  WARNING: {r['merge_errors']} merge call(s) failed ({r['merge_error']}); those batches stay unmerged, "
              "so repetition may be understated. Re-run `painpoint cluster`.")
    if args.no_merge:
        print("  note: without the semantic merge, paraphrased complaints are counted separately (repetition understated).")


def cmd_solutions(args, cfg):
    from .solutions import run_solution_checks

    s = run_solution_checks(_conn(cfg), _llm(cfg), cfg, args.top, args.force, progress=print)
    print(f"checked {s.checked}: {s.ok} ok, {s.no_results} no results, {s.errors} errors; "
          f"{s.skipped_existing} already checked (use --force to redo)")


def cmd_report(args, cfg):
    from .report import build_report, write_csv

    md, rows = build_report(_conn(cfg), cfg, top_n=args.top, sort=args.sort, category=args.category)
    Path(args.out).write_text(md, encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} themes)")
    if args.csv:
        write_csv(rows, args.csv)
        print(f"wrote {args.csv}")


def cmd_stats(args, cfg):
    for k, v in db.table_counts(_conn(cfg)).items():
        print(f"{k:16} {v}")


def cmd_doctor(args, cfg):
    """One tiny live call per feature, to verify credentials, model, structured output and fallbacks."""
    llm = _llm(cfg)
    sample = {
        "title": "Chasing late invoices is eating my Fridays",
        "body": "I run a 3-person agency and spend about 4 hours a week emailing clients about unpaid invoices. "
                "QuickBooks reminders are useless. I'd happily pay $30/month for something that just handles this.",
        "source": "doctor", "community": "doctor", "kind": "post",
    }
    r = llm.extract_post(sample)
    print(f"extraction: {r.status}" + (f" ({r.error})" if r.error else ""))
    if r.data:
        print(f"  problem: {r.data['problem']!r} urgency={r.data['urgency']} budget={r.data['budget_signal']}")
    print(f"  tokens in/out: {llm.usage['input']}/{llm.usage['output']}")
    if args.search:
        s = llm.search_solutions("chasing clients for late invoice payments", {})
        print(f"solution check: {s.status}" + (f" ({s.error})" if s.error else ""))
        if s.data:
            print(f"  saturation {s.data['saturation']}/10, {len(s.data['solutions'])} verified solutions")
    if r.status == "error" and "fallback" in (r.error or "").lower():
        print("hint: set llm.use_fallbacks: false in your config if your platform does not support server-side fallbacks.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="painpoint", description=__doc__)
    p.add_argument("--config", help="YAML config (default: ./config.yaml if present)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database").set_defaults(fn=cmd_init)

    c = sub.add_parser("collect", help="collect posts")
    csub = c.add_subparsers(dest="source_type", required=True)
    r = csub.add_parser("reddit", help="official Reddit API via PRAW")
    r.add_argument("--subreddit", action="append", help="repeatable; defaults to config reddit.subreddits")
    w = csub.add_parser("web", help="Scrapling web sources from config web_sources")
    w.add_argument("--source", action="append", help="repeatable; defaults to all")
    i = csub.add_parser("import", help="import a JSONL/CSV file")
    i.add_argument("path")
    i.add_argument("--source-name", default="import")
    c.set_defaults(fn=cmd_collect)

    e = sub.add_parser("extract", help="extract pain points with Claude")
    e.add_argument("--limit", type=int)
    e.add_argument("--dry-run", action="store_true", help="only print the cost estimate")
    e.add_argument("--retry-errors", action="store_true")
    e.add_argument("--max-usd", type=float, help="refuse to run if the rough estimate is higher")
    e.set_defaults(fn=cmd_extract)

    cl = sub.add_parser("cluster", help="group similar pain points (keyword pass + semantic merge)")
    cl.add_argument("--no-merge", action="store_true", help="skip the Claude merge pass (no API call)")
    cl.set_defaults(fn=cmd_cluster)

    s = sub.add_parser("solutions", help="web-search existing solutions for the top themes")
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_solutions)

    rp = sub.add_parser("report", help="write the scored report")
    rp.add_argument("--top", type=int)
    rp.add_argument("--sort", choices=["opportunity", "repetition"])
    rp.add_argument("--category")
    rp.add_argument("--out", default="report.md")
    rp.add_argument("--csv")
    rp.set_defaults(fn=cmd_report)

    sub.add_parser("stats", help="row counts").set_defaults(fn=cmd_stats)

    d = sub.add_parser("doctor", help="verify API access with one tiny live call")
    d.add_argument("--search", action="store_true", help="also test the web-search solution check")
    d.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    path = args.config or ("config.yaml" if os.path.exists("config.yaml") else None)
    cfg = load_config(path)
    from .llm import FatalLLMError

    try:
        args.fn(args, cfg)
    except FatalLLMError as exc:
        sys.exit(f"stopped: {exc}\nProgress so far is saved; fix the cause and re-run the same command.")


if __name__ == "__main__":
    main()
