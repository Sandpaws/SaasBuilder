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
| 1 | **Find what to build.** Collect public posts and reviews, extract user pain points with Claude, score how often each repeats, and check whether solutions already exist | [`painpoint-radar/`](painpoint-radar/) | Code and tests done (116 passing). Collectors: Hacker News (no key needed), Reddit through Agent Reach (default) or the official API (approved 2026-11-08), web pages via Scrapling. Run end to end on synthetic posts only; first run on live data pending |
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
- **2026-11-08, Reddit said yes; we chose Agent Reach anyway.** Reddit approved our API access. We then made
  [Agent Reach](https://github.com/Panniantong/Agent-Reach) the default Reddit collector. It reads Reddit through a
  logged-in browser instead of the API, so the approval does not cover it, and it can put the account and the approved
  app at risk. The approved route stays available as `collect reddit --via api`, and the API app still runs the
  deletion sync. The trade-offs are written down in
  [Collecting through Agent Reach](painpoint-radar/README.md#collecting-through-agent-reach-the-default).
