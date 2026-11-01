# painpoint-radar

Find real user pain points for a SaaS idea. It collects public posts, comments and reviews, uses Claude to pull
out the pain points, groups repeats, scores each theme out of 10, and web-searches whether solutions already exist.

```
 collect ──► extract ──► cluster ──► solutions ──► report
 PRAW (Reddit API)   Claude, one     keyword pass +   Claude + web     scored table,
 Scrapling (web)     call per post   Claude merge     search, URLs     quotes with links,
 JSONL/CSV import    structured JSON                  verified         CSV
```

Example of the output: [`docs/sample_report.md`](docs/sample_report.md) (**synthetic data**, format only).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[reddit,scrape]"        # drop extras you do not need
scrapling install                        # only if you use browser-based Scrapling fetching
cp config.example.yaml config.yaml       # edit subreddits / models / web_sources

export ANTHROPIC_API_KEY=...             # or `ant auth login`
painpoint doctor --search                # 1 tiny live call per feature: key, model, structured output, fallbacks, web search

export REDDIT_CLIENT_ID=... REDDIT_CLIENT_SECRET=... REDDIT_USER_AGENT="painpoint-radar by u/yourname"
painpoint init
painpoint collect reddit                 # official API; add --subreddit X (repeatable)
painpoint collect web                    # Scrapling sources from config.yaml
painpoint collect import my_export.jsonl --source-name g2   # any JSONL/CSV you already have
painpoint extract --dry-run              # rough cost estimate first
painpoint extract --max-usd 20           # refuses to start if the estimate is higher
painpoint cluster                        # keyword pass + Claude merge (use --no-merge to skip the API call)
painpoint solutions --top 15             # web search for existing products
painpoint report --top 25 --csv scores.csv
```

Every step is resumable and idempotent: re-running `collect` skips duplicates, `extract` only touches posts without
a result (`--retry-errors` retries failures), `solutions` skips themes already checked (`--force` to redo).

## Sources

- **Reddit: official API via PRAW.** Needs your own app credentials. Reddit's terms govern what you may do with the
  data, especially for a commercial product, and access rules have been changing; check
  Reddit's current Data API terms (start at redditinc.com/policies) and your access before building on it.
  This tool never reads or stores usernames. If you cannot get API access, use `collect import` with data you are
  entitled to use.
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

Let Claude open a review page, find the right selectors (the MCP `browser_network_requests` tool also shows when a
page loads its data from a JSON endpoint, which is more stable than HTML selectors), then copy them into
`web_sources`. For quick one-offs use `scrapling extract get URL out.md -s ".review" --ai-targeted`.

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

## Cost

`painpoint extract --dry-run` prints an estimate before spending anything. With the built-in assumptions (about
2,000 characters per post, conservative output size), per 1,000 posts: **Opus 5.5 about $18, Sonnet 5.5 about $9,
Haiku 5.5 about $0.45.** The default model is `claude-opus-5-5`; extraction is the dominant cost, so run a
100-post sample on a cheaper model, compare the pain points, and switch `models.extract` if quality holds. The
Batches API (50% off) is not wired in yet. `--max-usd` is a guard against typos, not a billing limit.

## Safety and privacy

- Scraped text is untrusted. Extraction calls have no tools and wrap the text in tags with an explicit
  "data, not instructions" system prompt; the text cannot close the wrapper tag.
- Usernames are never stored; emails, phone numbers and `u/name` mentions are scrubbed before storage.
- Secrets live in environment variables, not in `config.yaml` (git-ignored).
- Clusters, labels and report text come from a model summarising public posts and can be wrong.

## What is and is not verified

51 tests pass on Python 3.11 and 3.13 (`pip install -e ".[dev,reddit,scrape]" && pytest`). They cover scoring maths,
dedup, scrubbing, the Reddit and importer converters (with fake PRAW objects), the **real Scrapling spider against a
local mock site including pagination, rating filter, max pages and robots.txt**, the CLI, and every Claude-calling
path against a **fake client** (resumability, refusals, `max_tokens`, auth/transient errors, injection wrapping,
invented quotes and invented products being dropped, `pause_turn` continuation).

**Not yet run against real services:** the live Anthropic API (structured output together with `effort`, the
`fallbacks: "default"` beta, the `web_search_20260209` tool), live Reddit, and real review sites. `painpoint doctor
--search` exercises the first three; the extraction part costs a fraction of a cent and the web-search part usually well under a dollar. Run it first.

## Known limitations

- Keyword clustering alone misses paraphrases (measured: true paraphrases score about 0.15 TF-IDF similarity);
  that is why `cluster` adds a Claude merge pass. Merging happens within batches of 300 labels, largest clusters first.
- "Repeated /10" depends on how much you collected and from where. Collect broadly before comparing themes.
- Reddit comments are optional and prefiltered by a keyword heuristic to control cost.
- No license file yet; add one before sharing the code.

## Layout

```
painpoint/  config.py  db.py  schemas.py  prompts.py  llm.py          # all Claude calls live in llm.py
            collect/{reddit_api,web_spider,importer,common}.py
            extract.py  cluster.py  solutions.py  scoring.py  report.py  cli.py
tests/      fake Anthropic client + synthetic fixtures (tests/data.py is made-up, not real posts)
```
