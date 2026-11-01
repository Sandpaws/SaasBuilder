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
painpoint collect reddit                 # official API; first removes items deleted on Reddit, then collects
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

Taken from Reddit's own API guide and Data API terms. Not legal advice; the terms themselves are the authority.

- **Approval first.** Reddit requires approval before any API access, and explicit written approval for commercial use.
  Credentials alone are not permission. Request access through Reddit's support form and describe this use honestly
  (read-only market research on a few subreddits, analysed with an LLM, nothing redistributed).
- **Identify yourself.** The User-Agent must look like `<platform>:<app ID>:<version> (by /u/<username>)`;
  `collect reddit` warns if it does not. Reddit throttles or blocks unidentified clients.
- **Rate limit:** 100 queries per minute per client id, averaged over 10 minutes. PRAW reads Reddit's
  `X-Ratelimit-*` headers and waits, and this tool never runs requests in parallel.
- **Deleted means deleted.** Content deleted on Reddit must be deleted from your copy, including derived data, and
  Reddit recommends doing it within 48 hours. `collect reddit` runs a deletion sync first (`GET /api/info` on
  every stored item); `painpoint purge-deleted` does only that. Purging also erases the bytes (WAL checkpoint and
  VACUUM). Run it at least every 48 hours while you keep Reddit data.
- **No sharing.** Do not sell, license or share Reddit data. Never commit the database, exports or reports that
  quote real posts to a public repo (they are git-ignored; `docs/sample_report.md` is synthetic).
- **No profiling.** The extraction prompt forbids inferring sensitive traits (health, politics, religion, sexuality,
  ...), no usernames are stored, and nothing tries to link users to anyone off Reddit. Only the `read` scope is
  needed; no user-history or write endpoints are used.
- **No AI training.** Sending posts to Claude for analysis is not training, but check your Claude privacy settings:
  on consumer plans, as I understand Anthropic's terms, chats and Claude Code sessions can be used to improve models
  if that setting is on. If it is, switch it off or use the `api` backend, whose commercial terms differ. Verify in
  Anthropic's current terms.
- **Why there is no "no-API" scraper here.** Browser extensions, `.json` URLs, `old.reddit.com` parsing and paid
  scraping tools skip the approval step. Reddit's guide says non-OAuth traffic is blocked and that scraping without
  approval is prohibited, and exported copies cannot be synced for deletions. If approval is denied, use other
  sources (below) rather than working around it.

## Sources

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

80 tests pass on Python 3.11 and 3.13 (`pip install -e ".[dev,reddit,scrape]" && pytest`): scoring maths, dedup,
scrubbing, the Reddit and importer converters (fake PRAW objects), the **real Scrapling spider against a local mock
site** (pagination, rating filter, max pages, robots.txt), the CLI, the Claude Code backend through the real
subprocess path against a fake `claude` (flags, API-key stripping, usage-limit stop, login errors, missing CLI), and
the API backend against a fake client, the deletion sync (including a check that purged text is gone from the
database file), the User-Agent check, complaint-phrase search, and subreddit discovery (all against fake PRAW objects).

**Run for real, end to end, through `claude -p`** (Haiku for extraction, Sonnet for merge and the web-search solution
check) on the 22 synthetic posts in `tests/data.py`: all steps completed with no errors, the merge turned 11 keyword
clusters into the 3 true themes, and the solution check returned verified product URLs. That is a plumbing test on
made-up posts, not evidence about real Reddit data.

**Not yet run for real:** the `api` backend against the live API, live Reddit (collection, search, deletion sync and
subreddit checks only ran against fake PRAW objects), real review sites.

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
            collect/{reddit_api,web_spider,importer,common}.py
            extract.py  cluster.py  solutions.py  scoring.py  report.py  cli.py
tests/      fake Anthropic client, fake `claude` executable, synthetic fixtures (tests/data.py is made up)
```
