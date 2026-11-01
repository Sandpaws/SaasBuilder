# painpoint-radar

A Reddit pain-point finder, built to discover the idea for our next SaaS product. It collects public posts, comments
and reviews, uses Claude to pull out the pain points, groups repeats, scores each theme out of 10, and web-searches
whether solutions already exist.

```
 collect ──► extract ──► cluster ──► solutions ──► report
 PRAW (Reddit API)   Claude, posts   keyword pass +   Claude + web     scored table,
 Scrapling (web)     in small        Claude merge     search, URLs     quotes with links,
 JSONL/CSV import    batches                          verified         CSV
```

Output example: [`docs/sample_report.md`](docs/sample_report.md) (**synthetic data**, format only).

## Two ways to run the Claude steps

| `backend:` | Uses | Cost |
|---|---|---|
| **`claude_code`** (default) | your logged-in Claude Code, via the CLI's documented headless mode (`claude -p`) | no per-token billing; counts toward your subscription's usage limits |
| `api` | the Anthropic API (`ANTHROPIC_API_KEY`) | per token; Haiku 5.5 is roughly $0.5 per 1,000 posts |

`claude_code` does not read or forward any credential: it just runs the real `claude` binary you already signed in
with. `ANTHROPIC_API_KEY` is removed from that child process (`cc.strip_api_key_env`) because when it is set the CLI
bills the API account instead of your subscription. Do not paste a subscription/OAuth token into API code; Anthropic's
terms (check the current text) restrict using consumer-plan tokens outside Claude Code itself. Running `claude -p` is
Claude Code itself.

## Quick start (on a machine where you are signed in to Claude Code)

```bash
cd painpoint-radar                      # from the repo root
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[reddit,scrape]"        # drop extras you do not need
cp config.example.yaml config.yaml

claude                                   # once: sign in with your subscription
painpoint doctor --search                # one tiny real call per feature: login, structured output, web search

export REDDIT_CLIENT_ID=... REDDIT_CLIENT_SECRET=...
export REDDIT_USER_AGENT="python:com.yourname.painpoint-radar:v0.1.0 (by /u/yourname)"   # Reddit's required format
painpoint init
painpoint subreddits check SaaS SideProject SomebodyMakeThis   # exists? usable? how big?
painpoint collect hn --limit 20          # Hacker News test run: no key, no approval needed
painpoint collect hn                     # full run (comments + Ask HN posts matching complaint phrases)
painpoint collect reddit --limit 5       # small Reddit test run (needs approved API access); then drop --limit
painpoint extract --dry-run              # call count + token estimate
painpoint extract --limit 200            # start small and watch your usage
painpoint extract                        # resumes where it stopped, also after a usage limit
painpoint cluster                        # keyword pass + Claude merge (--no-merge skips the Claude call)
painpoint solutions --top 15             # web search for existing products
painpoint report --top 25 --csv scores.csv
painpoint purge-deleted                  # at least every 48 hours while you hold Reddit data
```

Every step is resumable and idempotent: re-running `collect` skips duplicates, `extract` only touches posts without
a result (`--retry-errors` retries failures), `solutions` skips themes already checked (`--force` to redo). If the
subscription's usage limit is hit mid-run, `extract` stops cleanly and the next run continues.

## Reddit's rules (read before collecting)

Checked against Reddit's Developer Terms (revised March 2026), Data API Terms and API guide. Not legal advice; the
terms are the authority.

### Open issues only Reddit can settle (put them in the access request)

1. **Commercial use** (Developer Terms 4.1, Data API 3.1-3.2). Without written approval you may not use Reddit data
   "by or on behalf of a business or as part of a service or product that is monetized", nor derive revenue "directly
   or indirectly ... including from any data derived from" it. This project exists to choose a SaaS idea to sell, so
   it probably needs a separate agreement with Reddit (the commercial request type), not just free developer access.
   If that is out of reach, Reddit stays a place to read by hand, and other sources carry the project.
2. **Sharing with third parties** (Developer Terms 7.2: "You will not share Reddit Services and Data with any third
   party"). Sending posts to Claude means sharing them with Anthropic as a service provider. Disclose it and ask.
3. **Model training** (4.2: no use "to train large language, artificial intelligence, or other algorithmic models"
   without permission). We only run inference, and the keyword index is rebuilt and thrown away on every run. Say so
   in the request, and check your Claude privacy setting (on consumer plans, chats can be opted into model improvement).

### What the tool does about the rest

| Rule | In this repo |
|---|---|
| Approval before any access; truthful identity, one app per use case (guide; Dev Terms 1.4, 2.8, 4.2) | No collection without approved credentials. No "no-API" scraping. `collect reddit` warns if the User-Agent is not `<platform>:<app ID>:<version> (by /u/<username>)`. |
| Rate limit 100 queries/min per client id (guide) | PRAW reads Reddit's `X-Ratelimit-*` headers and waits; requests are sequential. |
| Content deleted, removed **or modified** on Reddit must be deleted/updated promptly; Reddit suggests within 48 hours (Dev Terms 3.3; guide) | `collect reddit` first runs a sync (`GET /api/info`, 100 per request) that purges deleted, removed and edited items with their extractions; edited ones are re-collected. `painpoint purge-deleted` does only that: run it at least every 48 hours while you hold Reddit data. Purging erases the bytes (WAL checkpoint + VACUUM). |
| On termination, delete all Reddit data "and any data or models derived" from it (Data API 6; Dev Terms 8.2) | `painpoint wipe --yes` erases every Reddit item and everything derived from it. Delete `report.md` and CSV exports yourself. |
| No selling, sharing or redistributing Reddit data (guide; Dev Terms 4.1-4.2) | Data stays local. Databases, exports and reports are git-ignored; `docs/sample_report.md` is synthetic. Never publish real quotes. |
| Attribution when you display User Content: link back, cite the username, say it is from Reddit (Dev Terms 5.2) | Reports link every quote to its source. We deliberately do not store usernames, so **do not show or publish Reddit quotes to anyone** unless Reddit agrees how to attribute them. |
| No inferring sensitive traits, no re-identification (guide) | The prompt forbids it, no usernames are stored, nothing is matched to off-Reddit identity. |
| Encryption at rest, security (Dev Terms 7.4) | Not built in. Keep the database on an encrypted disk (FileVault, BitLocker, LUKS), not on shared or cloud machines. |
| Privacy law and Reddit's Developer DPA (Dev Terms 7.1) | No author identifiers are stored; emails, phone numbers and `u/name` mentions are scrubbed. Read the DPA yourself. |
| Audit and cooperation (Dev Terms 2.2), account in good standing (1.1) | Keep this section accurate; Reddit may ask how you handle data. |

The Reddit Ads API and Devvit (for non-commercial apps) are not used here.

**Why there is no "no-API" scraper.** Browser extensions, `.json` URLs, `old.reddit.com` parsing and paid scraping
tools skip the approval step. Reddit's guide says non-OAuth traffic is blocked and scraping without approval is
prohibited, and exported copies cannot be synced for deletions. If approval is denied, use other sources (below).

## Sources

- **Hacker News: public Algolia search API** (`painpoint collect hn`). No key. Searches comments and "Ask HN" posts for
  complaint phrases (`hackernews.queries`), converts the HTML to text, never reads author names, and runs sequentially
  with a delay and a User-Agent that names this project. Audience skews toward developers and founders. Check the
  API's current terms before relying on it. Written from the documented response shape and tested against fixtures;
  **not yet run against the live service** (it is blocked in the cloud sandbox: allow `hn.algolia.com` or run it locally).
- **Reddit: official API via PRAW.** Needs your own app credentials. Reddit's terms govern what you may do with the
  data, especially for a commercial product, and access rules have been changing; check Reddit's current Data API
  terms (start at redditinc.com/policies) and your access before building on it. Usernames are never read or stored.
  Besides the listing (`top` of the month by default) it runs a search per complaint phrase (`reddit.search_queries`,
  e.g. "wish there was", "alternative to"), which finds far more on-topic posts than the top list.
  r/SaaS and r/SideProject are mostly founders and builders, so the "general" pain points you get are weighted toward
  founder problems (finding customers, onboarding, pricing, tooling); the `Who` line in the report shows the persona.
  If you cannot get API access, use `collect import` with data you are entitled to use, or other sources whose terms
  allow it (for example Hacker News, Stack Exchange, GitHub issues/discussions, or app-store reviews; none built yet,
  and each has its own terms to read first).
- **Everything else: Scrapling** (`painpoint collect web`). Review sites, forums, directories, changelogs: define
  CSS selectors in `config.yaml` (see `config.example.yaml`). `robots.txt` is obeyed by default; Scrapling drops
  disallowed requests silently, so the CLI prints a warning with the count and another when a source yields 0 items.
  Respect each site's terms and do not collect personal data.
- **Import** JSONL/CSV from any other tool. Columns: `title`, `body|text|content`, `url`, `community`,
  `created_utc|date`, `score`; author columns are ignored.

### Building selectors fast with Scrapling's MCP server and skill

```bash
pip install "scrapling[all]" && scrapling install
claude mcp add ScraplingServer -- "$(which scrapling-mcp)"           # lets Claude fetch pages and inspect them
npx skills add D4Vinci/Scrapling --skill scrapling-official          # teaches Claude the current Scrapling API
```

Let Claude open a review page and find the selectors (the MCP `browser_network_requests` tool also shows when a page
loads its data from a JSON endpoint, which is more stable than HTML selectors), then copy them into `web_sources`.

## How the scores work

| Score | Meaning |
|---|---|
| **Repeated /10** | Log-scaled count of distinct threads where the problem appears (comments inside one thread add a little), recency-weighted (180-day half-life). 1 thread is about 1.5/10, 100 effective mentions is 10/10 (`scoring.repetition_ref_mentions`). Relative to *your* sample. |
| **Urgency /10** | Mean urgency (1-5 per mention, judged by the model) rescaled. |
| **Budget /10** | Share of mentions with a money signal: willing to pay, states current spend, complains about price. |
| **Solutions out there** | Saturation 0-10 from a web search. Only products whose URL domain appeared in the real search results are kept; a high score with nothing verifiable is capped at 2/10 with low confidence. |
| **Opportunity /10** | Weighted blend (0.40 repetition, 0.20 urgency, 0.15 budget, 0.25 competition gap). Unchecked themes use a neutral gap of 5 and are labelled *unchecked*. |

Also reported per theme: threads, mentions, communities, share of all pain threads, trend (last 30 days vs the 30
before), evidence strength (weak / moderate / strong), tools people name, current workarounds, personas, and quotes
with links. Quotes are verified to appear verbatim in the source; invented ones are dropped. Treat the ranking as a
shortlist to validate with interviews, not a verdict.

## Safety and privacy

- Scraped text is untrusted. Extraction and merge calls run with **every tool disabled** (`--tools ""`, no MCP, no
  slash commands, empty temp directory, replacement system prompt) and wrap the text in tags with a "data, not
  instructions" prompt; the text cannot close the wrapper tag. Only the solution check gets `WebSearch`, and it sees
  only our own cluster label and quotes.
- Usernames are never stored; emails, phone numbers and `u/name` mentions are scrubbed before storage.
- Secrets live in environment variables, not in `config.yaml` (git-ignored).
- Clusters, labels and report text come from a model summarising public posts and can be wrong.

## What is and is not verified

99 tests pass on Python 3.11 and 3.13 (`pip install -e ".[dev,reddit,scrape]" && pytest`): scoring maths, dedup,
scrubbing, the Reddit and importer converters (fake PRAW objects), the **real Scrapling spider against a local mock
site** (pagination, rating filter, max pages, robots.txt), the CLI, the Claude Code backend through the real
subprocess path against a fake `claude` (flags, API-key stripping, usage-limit stop, login errors, missing CLI), and
the API backend against a fake client, the deletion sync (including a check that purged text is gone from the
database file), the User-Agent check, complaint-phrase search, and subreddit discovery (all against fake PRAW objects), and the Hacker
News collector (HTML cleanup, paging, dedup, failure handling) against responses shaped like the Algolia API.

**Run for real, end to end, through `claude -p`** (Haiku for extraction, Sonnet for merge and the web-search solution
check) on the 22 synthetic posts in `tests/data.py`: all steps completed with no errors, the merge turned 11 keyword
clusters into the 3 true themes, and the solution check returned verified product URLs. That is a plumbing test on
made-up posts, not evidence about real Reddit data.

**Not yet run for real:** the `api` backend against the live API, live Reddit (collection, search, deletion sync and
subreddit checks only ran against fake PRAW objects), the Hacker News collector (fixtures only), real review sites.

### Measured: batch size

On those 22 posts, at 12 posts per call Haiku skipped the same clear pain post in both runs; at 6 and at 3 it caught
all 18. The default is therefore 6 (about 1.8x the tokens of 12). Small sample; tune `cc.batch_size` and spot-check
your own data.

## Known limitations

- Keyword clustering alone misses paraphrases (true paraphrases scored about 0.15 TF-IDF similarity); that is why
  `cluster` adds a Claude merge pass. Merging happens within batches of 300 labels, largest clusters first.
- "Repeated /10" depends on how much you collected and from where. Collect broadly before comparing themes.
- A small model in batches can miss pain posts (see above) or misjudge urgency. Spot-check 30-50 results.
- Reddit comments are optional and prefiltered by a keyword heuristic to control cost.
- No license file yet; add one before sharing the code.

## Layout

```
painpoint/  config.py  db.py  schemas.py  prompts.py
            llm.py            # Anthropic API backend + shared helpers
            cc_backend.py     # Claude Code (`claude -p`) backend
            collect/{reddit_api,hackernews,web_spider,importer,common}.py
            extract.py  cluster.py  solutions.py  scoring.py  report.py  cli.py
tests/      fake Anthropic client, fake `claude` executable, synthetic fixtures (tests/data.py is made up)
```
