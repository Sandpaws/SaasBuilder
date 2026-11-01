# SaasBuilder

Finding and building a SaaS product from real user pain points.

| Step | What | Where | Status |
|---|---|---|---|
| 1. Find what to build | Collect public posts and reviews, extract user pain points with Claude, score how often each repeats, and check whether solutions already exist | [`painpoint-radar/`](painpoint-radar/) | Code and tests done (64 passing). Run end to end on synthetic posts only; first run on live Reddit data is pending API credentials |

The scraper is only the first step: its report decides what gets built next. Later steps get their own folders here.
