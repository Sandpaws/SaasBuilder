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
| 1 | **Find what to build.** Collect public posts and reviews, extract user pain points with Claude, score how often each repeats, and check whether solutions already exist | [`painpoint-radar/`](painpoint-radar/) | Code and tests done (95 passing). Collectors: Hacker News (no key needed), Reddit (waits on Reddit's API access approval), web pages via Scrapling. Run end to end on synthetic posts only; first run on live data pending |
| 2 | **Validate the idea** | not started | planned |
| 3 | **Build the product** | not started | planned |
| 4 | **Deploy it** | not started | planned |

Steps 2 to 4 will be shaped by what the first report shows, so they stay planned until we have real results.
