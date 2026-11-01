"""A fake Anthropic client so every Claude-calling code path is tested without network or a key."""

from __future__ import annotations

import json
from types import SimpleNamespace

KB = {  # theme keyword -> (category, [problem wordings])
    "invoice": ("billing_finance", [
        "chasing clients for late invoice payments",
        "following up on unpaid client invoices",
        "late invoice payments from clients",
    ]),
    "onboarding": ("sales_marketing", [
        "new users dropping off during onboarding",
        "users abandon signup onboarding flow",
        "onboarding drop off for new signups",
    ]),
    "first customers": ("sales_marketing", [
        "finding first customers for a new product",
        "finding customers for a brand new product",
    ]),
}


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def response(content, stop_reason="end_turn", in_tok=120, out_tok=60):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, stop_details=None,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


def _between(text: str, tag: str) -> str:
    start, end = f"<{tag}>", f"</{tag}>"
    return text.split(start, 1)[1].split(end, 1)[0] if start in text else ""


def extraction_json(prompt: str, bad_quote: bool = False) -> str:
    title, body = _between(prompt, "title"), _between(prompt, "body")
    text = f"{title} {body}".lower()
    pain = {
        "is_pain_point": False, "is_self_promotion": False, "problem": "", "summary": "", "category": "other",
        "persona": "unknown", "current_workaround": None, "urgency": 1, "budget_signal": "none",
        "budget_detail": None, "competitors_named": [], "evidence_quote": "",
    }
    if "just launched" in text or "check out my" in text:
        pain["is_self_promotion"] = True
        return json.dumps(pain)
    for key, (category, wordings) in KB.items():
        if key in text:
            problem = wordings[sum(map(ord, text)) % len(wordings)]  # stable across runs, unlike hash()
            pain.update(
                is_pain_point=True, problem=problem, summary=f"Author struggles with {problem}.", category=category,
                persona="freelance designer" if key == "invoice" else "solo SaaS founder",
                urgency=4 if "hours" in text else 2,
                current_workaround="manual spreadsheet reminders" if key == "invoice" else None,
                budget_signal="willing_to_pay" if "pay" in text else "none",
                budget_detail="$30/month" if "$30" in text else None,
                competitors_named=["QuickBooks"] if "quickbooks" in text else [],
                evidence_quote="THIS QUOTE WAS INVENTED" if bad_quote else body.strip()[:80],
            )
            return json.dumps(pain)
    return json.dumps(pain)  # not a pain point


class FakeMessages:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs)


class FakeClient:
    """Exposes both client.messages and client.beta.messages (the code picks one via use_fallbacks)."""

    def __init__(self, handler):
        self.messages = FakeMessages(handler)
        self.beta = SimpleNamespace(messages=self.messages)

    @property
    def calls(self):
        return self.messages.calls


def merge_json(prompt: str) -> str:
    """Group numbered labels by theme keyword, like a sensible model would."""
    labels = [line.split(". ", 1) for line in _between(prompt, "labels").strip().splitlines()]
    themes: dict[str, list[int]] = {}
    for num, label in labels:
        for key in ("invoice", "onboarding", "customers"):
            if key in label:
                themes.setdefault(key, []).append(int(num))
    return json.dumps({"groups": [g for g in themes.values() if len(g) >= 2]})


def default_handler(bad_quote: bool = False):
    def handler(kw):
        content = kw["messages"][0]["content"]
        if "<labels>" in content:
            return response([text_block(merge_json(content))])
        return response([text_block(extraction_json(content, bad_quote))])
    return handler
