# SaasBuilder

**A step-by-step guide to building a SaaS product from the real pain points of real users, from the first idea all the way to a deployed product.**

This repo documents everything: how we find a problem worth solving, check that people really have it, decide what to
build, build it, and deploy it. It is built in the open, for everyone, and co-developed with
[Claude](https://claude.com/product/claude-code).

## How to follow along

Each step lives in its own folder with its own README, and every commit explains what we did and why. Read the steps
below in order, or read `git log` to watch the project grow.

## The steps

| # | Step | Where | Status |
|---|---|---|---|
| 1 | **Find what to build.** Collect public posts and reviews, extract user pain points with Claude, score how often each repeats, and check whether solutions already exist | [`painpoint-radar/`](painpoint-radar/) | Code and tests done. Collectors: Hacker News (no key needed, run live), Stack Exchange, Lemmy and Discourse forums (built, waiting for their hosts to be allowed), Reddit (access declined, see the [write-up](painpoint-radar/docs/reddit-access-request.md#outcome)), web pages via Scrapling. Full run on Hacker News (2026-11-07): 3,215 items, 1,036 pain points, 727 themes, top 10 checked for existing products. Next: a second source, so themes can be confirmed across communities |
| 2 | **Validate the idea** | not started | planned |
| 3 | **Build the product** | not started | planned |
| 4 | **Deploy it** | not started | planned |

Steps 2 to 4 will be shaped by what the first report shows, so they stay planned until we have real results.

## Progress log

Newest last. Every entry maps to commits in `git log`.

- **2026-11-01, build the finder.** Built the pain-point pipeline in [`painpoint-radar/`](painpoint-radar/): collect, extract
  with Claude, cluster, score out of 10, check whether solutions already exist. Tested end to end on synthetic posts,
  including a real run through Claude Code (`claude -p`) on a subscription.
- **2026-11-01, Reddit's rules.** Read Reddit's API guide, Developer Terms and Data API Terms. Added a deletion sync that
  also catches edited posts, a wipe command, a User-Agent check, and a clause-by-clause rules section. Decided not to build
  a "no-API" scraper.
- **2026-11-01, a source that needs no approval.** Added a Hacker News collector (written from the documented API, not yet
  run live).
- **2026-11-01, asked Reddit for access.** Submitted a read-only Data API request for ten subreddits. Write-up and lessons:
  [`painpoint-radar/docs/reddit-access-request.md`](painpoint-radar/docs/reddit-access-request.md). Waiting for a reply.
- **2026-11-07, first run on live data.** Ran every step on Hacker News in a cloud environment that allows only
  `hn.algolia.com`: 210 posts and comments collected in 31 s, 90 pain points extracted (Haiku via `claude -p`, about
  2 minutes, 1 post retried), 77 themes, 3 solution checks, one report. Nothing in the pipeline broke. The test suite did:
  it needed the optional Reddit package to even start, now fixed. What the run taught us about report quality:
  - Ask HN posts are far richer than comments (62% contain a pain point, against 35%).
  - About a quarter of the pain points are not software problems (cars, coffee brewers, broadband), because nothing
    tells extraction to drop them.
  - One budget signal in a one-mention theme scores 10/10, so weak themes jump up the ranking. The "too expensive"
    search query adds to this, and the cheap model sometimes tags a budget signal that isn't there.
  - `solutions --top N` picks themes by repetition while the report sorts by opportunity, so the report's top themes
    can stay unchecked.
  - With 210 items almost every theme is mentioned once; repetition needs a much bigger sample.
  Collected data and the report stay local and out of git.
- **2026-11-07, 15x more data.** Fixed the three scoring and filtering problems above, then widened Hacker News
  collection: 27 stronger complaint phrases over two years, and every answer in open "what problem do you have?"
  Ask HN threads. Result: 3,215 items, 1,036 pain points (about 590 Haiku calls, 20 minutes), 727 themes, 10
  solution checks. Two more fixes came out of looking at that output: one "Tell HN: X is frustrating" thread
  had become the #1 theme (threads must now be open questions), and paraphrases split across merge batches never
  met (the merge now runs up to three passes; 871 themes became 727). Top themes are now repeated in 4 to 13 threads,
  with "moderate" evidence; none can be "strong" yet, because that needs a second community. The clearest gaps the
  solution checks found: a hard spending cap for pay-as-you-go cloud accounts, verifying that an AI agent's change
  does what was asked, and automatic fallback when an AI coding subscription hits its limit.
- **2026-11-08, Reddit declined; more sources built.** Reddit turned down the Data API request (no reason that we
  know of). We will not scrape Reddit instead; why, and the two allowed options left, are in the
  [write-up](painpoint-radar/docs/reddit-access-request.md#outcome). Built collectors for Stack Exchange, Lemmy (an
  open Reddit-like network) and Discourse product forums, tested against sample responses; they run once their
  hosts are allowed in the cloud environment and their terms are read.
