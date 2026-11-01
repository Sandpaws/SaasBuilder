> **SYNTHETIC SAMPLE - NOT REAL FINDINGS.** Generated from the made-up posts in `tests/data.py` with a fake model, only to show the report format. Domains like `example.test` are placeholders.

# Pain-point report

Generated 2026-11-01. Sample: **22** posts/comments from **3** communities (2026-10-02 to 2026-10-30); **18** pain-point mentions grouped into **3** themes; 1 theme(s) checked for existing solutions. Sorted by opportunity.

| # | Pain point | Repeated /10 | Opportunity /10 | Threads | Communities | Urgency | Budget | Solutions out there | Trend | Evidence |
|--:|---|--:|--:|--:|--:|--:|--:|---|---|---|
| 1 | late invoice payments from clients | 4.8 | 5.3 | 8 | 2 | 4.5 | 10.0 | 6/10 saturated (1 found) | rising | moderate |
| 2 | new users dropping off during onboarding | 3.8 | 4.3 | 5 | 1 | 3.5 | 5.7 | unchecked | new | moderate |
| 3 | finding customers for a brand new product | 2.9 | 2.9 | 3 | 1 | 2.5 | 0.0 | unchecked | new | weak |

## Details

### 1. late invoice payments from clients

*billing_finance* · seen 2026-10-02 to 2026-10-30 · 8 threads, 10 mentions · 50.0% of all pain threads · last 30d: 7 vs prior 30d: 1 (rising) · avg upvotes 5.0

- **Who:** freelance designer
- **Workarounds today:** manual spreadsheet reminders
- **Tools people name:** QuickBooks (2)
- **Budget signals:** 50% of mentions
- **Existing solutions:** saturation 6/10 (confidence: medium). A few direct tools exist; none dominant.
  - [Invoicely](https://www.invoicely.example/pricing) [direct_saas] ($19/mo): Automated invoice reminders
- **Gaps:** Reminders are generic and do not escalate.
- > "I spend 4 hours a week emailing clients about unpaid invoices. QuickBooks remind" (r/SaaS, urgency 4) [source](https://example.test/inv0)
- > "Chasing an invoice every month is the worst part of consulting. I spend hours wr" (r/SaaS, urgency 4) [source](https://example.test/inv6)
- > "Same here, late invoice payments cost me hours every week." (r/SaaS, urgency 4) [source](https://example.test/inv0c1)

### 2. new users dropping off during onboarding

*sales_marketing* · seen 2026-10-05 to 2026-10-29 · 5 threads, 5 mentions · 31.2% of all pain threads · last 30d: 5 vs prior 30d: 0 (new) · avg upvotes 5.0

- **Who:** solo saas founder
- **Budget signals:** 20% of mentions
- **Existing solutions:** not checked yet (`painpoint solutions`).
- > "Onboarding drop-off is our biggest churn driver. Hours of analysis and still no" (r/SaaS, urgency 4) [source](https://example.test/onb1)
- > "Our onboarding flow is too long and new users abandon it. Anyone solved this wit" (r/SaaS, urgency 2) [source](https://example.test/onb2)
- > "Users sign up and vanish during onboarding. I'd pay for a tool that shows where" (r/SaaS, urgency 2) [source](https://example.test/onb3)

### 3. finding customers for a brand new product

*sales_marketing* · seen 2026-10-16 to 2026-10-22 · 3 threads, 3 mentions · 18.8% of all pain threads · last 30d: 3 vs prior 30d: 0 (new) · avg upvotes 5.0

- **Who:** solo saas founder
- **Budget signals:** 0% of mentions
- **Existing solutions:** not checked yet (`painpoint solutions`).
- > "Launching in two weeks and I have no idea how to find my first customers. Cold o" (r/SideProject, urgency 2) [source](https://example.test/cus0)
- > "How do you get first customers when you have zero audience? Spent months buildin" (r/SideProject, urgency 2) [source](https://example.test/cus1)
- > "Struggling to land first customers for my new product, ads are too expensive for" (r/SideProject, urgency 2) [source](https://example.test/cus2)

## How to read the scores

- **Repeated /10**: how often the problem recurs in this sample. Log scale over distinct threads (comments inside one thread add a little), recency-weighted. 1 thread is about 1/10; 100 effective mentions is 10/10. It is relative to what you collected, so compare clusters within one report, not across very different samples.
- **Urgency /10**: mean urgency (1-5 per mention, judged by the model) rescaled.
- **Budget /10**: share of mentions with a money signal (willing to pay, states current spend, complains about price).
- **Solutions out there**: saturation 0-10 from a web search of existing products ("n found" are verified against real search-result URLs). Competition gap = 10 - saturation.
- **Opportunity /10**: weighted blend (repetition 0.4, intensity 0.2, wtp 0.15, competition_gap 0.25). If the solution check has not run, the gap is a neutral 5 and the row says *unchecked*. Treat Opportunity as a ranking aid, not a verdict: talk to the people before you build.
- **Evidence**: strong = 10+ threads across 2+ communities; moderate = 4+ threads; weak = fewer.
- Pain points are model-extracted from public text, quotes are verified to appear verbatim in the source, and no usernames are stored.
