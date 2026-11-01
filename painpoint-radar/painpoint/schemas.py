"""Structured-output schemas the model must fill. Validated again with pydantic on our side."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

CATEGORIES = [
    "billing_finance",
    "sales_marketing",
    "customer_support",
    "hr_recruiting",
    "operations_workflow",
    "dev_tools",
    "data_analytics",
    "compliance_legal",
    "productivity_collab",
    "ecommerce_retail",
    "healthcare",
    "education",
    "other",
]

BUDGET_SIGNALS = ["none", "mentions_current_spend", "willing_to_pay", "price_complaint"]


class PainExtraction(BaseModel):
    is_pain_point: bool = Field(
        description="True only if the author describes a concrete, current problem, unmet need or costly workaround."
    )
    is_self_promotion: bool = Field(
        description="True if the message mainly promotes the author's own product, service or newsletter."
    )
    problem: str = Field(
        description="<=15 words, neutral noun phrase for the underlying friction; no brands, no first person. Empty if not a pain point."
    )
    summary: str = Field(description="One sentence describing the pain in plain words. Empty if not a pain point.")
    category: Literal[
        "billing_finance",
        "sales_marketing",
        "customer_support",
        "hr_recruiting",
        "operations_workflow",
        "dev_tools",
        "data_analytics",
        "compliance_legal",
        "productivity_collab",
        "ecommerce_retail",
        "healthcare",
        "education",
        "other",
    ]
    persona: str = Field(description="Who has the problem, <=6 words (e.g. 'solo SaaS founder'); 'unknown' if unclear.")
    current_workaround: Optional[str] = Field(
        default=None, description="What they do today to cope (manual process, spreadsheet, stitched tools)."
    )
    urgency: int = Field(
        description="1 mild annoyance, 2 recurring irritation, 3 significant time/money cost, 4 blocking now, 5 emergency."
    )
    budget_signal: Literal["none", "mentions_current_spend", "willing_to_pay", "price_complaint"]
    budget_detail: Optional[str] = Field(default=None, description="The amount or phrase behind the budget signal.")
    competitors_named: list[str] = Field(description="Tools/vendors named as current or failed solutions. [] if none.")
    evidence_quote: str = Field(
        description="One verbatim excerpt (<=200 chars) that best shows the pain. Empty if not a pain point."
    )


class IndexedExtraction(PainExtraction):
    n: int = Field(description="The n attribute of the post this entry describes (copy it exactly).")


class BatchExtraction(BaseModel):
    results: list[IndexedExtraction] = Field(description="Exactly one entry per post, in any order.")


class MergeGroups(BaseModel):
    groups: list[list[int]] = Field(
        description="Each inner list holds the numbers of 2+ labels that describe the SAME underlying problem."
    )


class Solution(BaseModel):
    name: str
    url: str = Field(description="URL that appeared in the web search results.")
    kind: Literal["direct_saas", "adjacent_tool", "diy_or_template", "open_source", "service_or_agency"]
    what_it_does: str
    pricing_note: Optional[str] = None
    weakness_or_gap: Optional[str] = Field(
        default=None, description="Documented limitation or user complaint, only if found in the sources."
    )


class SolutionCheck(BaseModel):
    solutions: list[Solution]
    saturation: int = Field(
        description="0 nothing found; 3 only adjacent/DIY; 5 a few direct products, none dominant; "
        "7 several established direct products; 9-10 crowded with dominant incumbents."
    )
    confidence: Literal["low", "medium", "high"]
    market_summary: str
    gap_notes: str = Field(description="Where existing solutions fall short, grounded in the sources. Empty if unknown.")
