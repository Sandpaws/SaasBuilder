"""Synthetic posts for tests. NOT real data: these exist only to exercise the pipeline."""

from __future__ import annotations

from painpoint.collect.common import Post

DAY = 86400

INVOICE_BODIES = [
    "I spend 4 hours a week emailing clients about unpaid invoices. QuickBooks reminders are useless and I'd pay $30/month for a fix.",
    "Late invoice payments are killing my cash flow. I track them in a spreadsheet and chase every client manually, hours every week.",
    "Does anyone have a better way to follow up on an overdue invoice without sounding rude? Tired of manual chasing.",
    "Our agency has 12 clients who pay their invoice late. We lost two weeks of runway last month, would pay for automation.",
    "Invoice reminders in my accounting tool are clunky. I want something that escalates automatically.",
    "Freelancer here: unpaid invoice from a client again, 60 days late. There has to be a tool for this.",
    "Chasing an invoice every month is the worst part of consulting. I spend hours writing polite emails.",
    "Is there any app that nags clients about an invoice automatically? QuickBooks does not do this well.",
]
ONBOARDING_BODIES = [
    "Half of our trial signups never finish onboarding. We lose users in the first three screens and I can't tell why.",
    "Onboarding drop-off is our biggest churn driver. Hours of analysis and still no clear fix.",
    "Our onboarding flow is too long and new users abandon it. Anyone solved this without a full redesign?",
    "Users sign up and vanish during onboarding. I'd pay for a tool that shows where they get stuck.",
    "We see a huge onboarding drop at step two. Session replay tools are overkill for our size.",
]
CUSTOMER_BODIES = [
    "Launching in two weeks and I have no idea how to find my first customers. Cold outreach feels hopeless.",
    "How do you get first customers when you have zero audience? Spent months building, nothing sold.",
    "Struggling to land first customers for my new product, ads are too expensive for me.",
]
PROMO = [
    ("I just launched my invoice tool!", "Check out my new invoice reminder app, 50% off this week, link in profile."),
    ("Check out my onboarding analytics SaaS", "Please check out my product and sign up to the waitlist."),
]
NOISE = [
    ("Weekly thread: what are you building?", "Share what you are working on this week and what you learned."),
    ("We hit $10k MRR", "Wanted to share a milestone with this community, thank you all for the support."),
]


def make_posts(now: float) -> list[Post]:
    posts: list[Post] = []

    def add(pid, community, title, body, age_days, kind="post", thread=None, upvotes=5):
        posts.append(Post(
            id=pid, source="test", community=community, kind=kind, thread_id=thread or pid,
            url=f"https://example.test/{pid}", title=title, body=body,
            created_utc=int(now - age_days * DAY), upvotes=upvotes, num_comments=3,
        ))

    for i, body in enumerate(INVOICE_BODIES):
        add(f"inv{i}", "r/SaaS" if i % 2 == 0 else "r/freelance", f"Invoice trouble #{i}", body, age_days=2 + i * 4)
    # two extra comments in the first invoice thread: same thread, so they add only a little
    add("inv0c1", "r/SaaS", "Re: Invoice trouble #0", "Same here, late invoice payments cost me hours every week.", 2,
        kind="comment", thread="inv0")
    add("inv0c2", "r/SaaS", "Re: Invoice trouble #0", "I'd pay for an invoice nagging tool, manual chasing is awful.", 2,
        kind="comment", thread="inv0")
    for i, body in enumerate(ONBOARDING_BODIES):
        add(f"onb{i}", "r/SaaS", f"Onboarding problem #{i}", body, age_days=3 + i * 6)
    for i, body in enumerate(CUSTOMER_BODIES):
        add(f"cus{i}", "r/SideProject", f"First customers #{i}", body, age_days=10 + i * 3)
    for i, (t, b) in enumerate(PROMO):
        add(f"pro{i}", "r/SaaS", t, b, 5)
    for i, (t, b) in enumerate(NOISE):
        add(f"noi{i}", "r/SaaS", t, b, 6)
    return posts
