# Asking Reddit for API access

**Status:** submitted 2026-11-01. **Approved**: Reddit granted API access (we heard on 2026-11-08). The exact
terms, including the answers to the two open questions below, are not recorded here yet.

**What we did next:** we made [Agent Reach](https://github.com/Panniantong/Agent-Reach), a browser-session tool, the
default Reddit collector, and kept the approved PRAW route as `collect reddit --via api`. Agent Reach does not use the
Data API, so this approval does not cover it. Why that matters, and what the tool still does to follow Reddit's rules
about the data: README, [Collecting through Agent Reach](../README.md#collecting-through-agent-reach-the-default).

This page records how we asked and why, so you can do the same. It describes the request as drafted; the ticket Reddit
received is the authority on exact wording.

## Why we had to ask

Reddit's API guide says all access needs approval first, that scraping without approval is prohibited (commercial or
not), and that traffic without OAuth is blocked. Credentials alone are not permission. We therefore did not build any
scraper that skips the API (see "Reddit's rules" in the [README](../README.md#reddits-rules-read-before-collecting)).

## What we asked for

- **Request type:** developer, for an app Devvit does not support. A commercial request exists for monetized use; see
  "Open questions" below.
- **What it does:** a private, read-only research script (PRAW, application-only OAuth, `read` scope). It lists top
  posts, searches inside each subreddit for complaint phrases ("wish there was", "alternative to", "is there a tool"),
  reads the top comments of those posts, and checks stored items by fullname so deleted, removed or edited ones can be
  purged. It never posts, comments, votes, messages or touches user profiles.
- **Subreddits (10):** SaaS, SideProject, SomebodyMakeThis, AppIdeas, software, smallbusiness, Entrepreneur,
  freelance, sysadmin, selfhosted.
- **Volume:** about 3,000 sequential requests per full run, well under 100 per minute, a few runs a month.
- **Data kept:** IDs, subreddit, permalink, scrubbed title and text, score, timestamp. No usernames or user IDs.
- **Benefit for Redditors:** none directly, and we said so. Claiming one would have been misleading.
- **Source link:** this repository.

## Open questions we put to Reddit

1. Does using the findings to decide what software to build need **commercial approval**? (Developer Terms 4.1 restrict
   monetized use and revenue derived "directly or indirectly" from the data.)
2. Is it permitted to process post text with a **third-party service provider** (Anthropic's Claude) for inference only?
   (Developer Terms 7.2 restrict sharing data with third parties.)

## Mistakes we caught before submitting

- Placeholder text left in the form (`[on an encrypted disk]`, a User-Agent containing `yourname`). Use your real
  identifier: Reddit requires a truthful `<platform>:<app ID>:<version> (by /u/<username>)`.
- A promise that contradicted our own schedule: "purge every 48 hours" while "running a few times a month". Either
  schedule the deletion sync or erase the data after each session, and say which.
- A throwaway answer to "what is missing from Devvit?". Explain the real reason: Devvit apps run inside communities,
  while this tool reads public content across communities you do not moderate and analyses it on your own machine.
- Reddit allows **one** request per use case. Fix the draft first, then submit once.

## What happens next

| Reddit says | We do |
|---|---|
| Approved | Create the app as instructed (a "script" app), set `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, then run `painpoint subreddits check ...` and a tiny `collect reddit --limit 5` test. Run `painpoint purge-deleted` at least every 48 hours. |
| Approved for non-commercial use only | Follow their terms exactly; decide whether we can proceed given our goal of choosing a product to sell. |
| Offers a commercial agreement | Weigh the cost against the value of the research phase. |
| Declines or does not reply | Carry on with Hacker News and other sources that allow it. Nothing built so far depends on Reddit. |

Meanwhile the pipeline runs on Hacker News, which needs neither a key nor an approval.
