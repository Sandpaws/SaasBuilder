"""All Claude API calls live here, so the rest of the pipeline is testable with a fake client.

- Extraction: one structured-output call per post, no tools (scraped text is untrusted).
- Solution check: web search (server tool) -> plain findings -> second structured call. Every returned solution
  must have a URL whose domain appeared in the actual search results (guards against invented products).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import anthropic

from .prompts import EXTRACT_SYSTEM, MERGE_SYSTEM, SOLUTION_SEARCH_PROMPT, SOLUTION_STRUCTURE_PROMPT, render_post
from .schemas import MergeGroups, PainExtraction, SolutionCheck

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = "web_search_20260209"
# Models for which the Claude API offers `fallbacks: "default"`. Haiku 5.5 does not.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class FatalLLMError(RuntimeError):
    """Problem that will repeat for every request (bad key, unknown model). Stop the run."""


@dataclass
class ExtractResult:
    status: str  # ok | refused | truncated | error
    data: dict | None = None
    error: str | None = None
    input_truncated: bool = False


@dataclass
class SolutionResult:
    status: str  # ok | no_results | error
    data: dict | None = None
    error: str | None = None
    urls_seen: int = 0


@dataclass
class MergeResult:
    status: str  # ok | error
    groups: list[list[int]] = field(default_factory=list)
    error: str | None = None


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def sanitize_groups(groups: list[list[int]], n_labels: int) -> list[list[int]]:
    """Keep only valid, unused label numbers; drop groups that end up with fewer than two."""
    used: set[int] = set()
    out = []
    for group in groups:
        clean = []
        for i in group:
            if 0 <= i < n_labels and i not in used:
                used.add(i)
                clean.append(i)
        if len(clean) >= 2:
            out.append(clean)
    return out


def finalize_solution_check(parsed: dict, seen_urls: list[str]) -> dict:
    """Drop products whose URL domain was not in the real search results; cap unsupported saturation scores."""
    allowed = {_domain(u) for u in seen_urls}
    parsed["solutions"] = [s for s in parsed["solutions"] if any(_same_site(_domain(s["url"]), a) for a in allowed)]
    parsed["saturation"] = max(0, min(10, int(parsed["saturation"])))
    if not parsed["solutions"] and parsed["saturation"] > 2:
        # A high saturation score with no verifiable product behind it is not credible.
        parsed["saturation"] = 2
        parsed["confidence"] = "low"
    return parsed


@dataclass
class LLM:
    """Anthropic API backend (billed per token)."""

    batch_size = 1  # one post per request

    client: Any
    cfg: dict
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.llm_cfg = self.cfg["llm"]
        self.models = self.cfg["models"]
        self._extract_schema = anthropic.transform_schema(PainExtraction)
        self._solution_schema = anthropic.transform_schema(SolutionCheck)
        self._merge_schema = anthropic.transform_schema(MergeGroups)
        self.workers = self.llm_cfg["workers"]
        self.batch_chars = 0

    # ------------------------------------------------------------ plumbing

    def _use_fallbacks(self, model: str) -> bool:
        setting = self.llm_cfg["use_fallbacks"]
        return model in FALLBACK_MODELS if setting == "auto" else bool(setting)

    def _create(self, **kwargs):
        if self._use_fallbacks(kwargs["model"]):
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)

    def _record(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        with self._lock:
            self.usage["input"] += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage["output"] += int(getattr(usage, "output_tokens", 0) or 0)

    @staticmethod
    def _text(resp) -> str:
        return next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")

    @staticmethod
    def _describe(exc: Exception) -> str:
        return f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"

    # ------------------------------------------------------------ extraction

    def extract_batch(self, posts: list[dict]) -> list[ExtractResult]:
        return [self.extract_post(p) for p in posts]

    def extract_post(self, post: dict) -> ExtractResult:
        text, truncated = render_post(post, self.llm_cfg["max_post_chars"])
        try:
            resp = self._create(
                model=self.models["extract"],
                max_tokens=self.llm_cfg["extract_max_tokens"],
                system=EXTRACT_SYSTEM,
                messages=[{"role": "user", "content": text}],
                output_config={
                    "format": {"type": "json_schema", "schema": self._extract_schema},
                    "effort": self.llm_cfg["extract_effort"],
                },
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise FatalLLMError(self._describe(exc)) from exc
        except anthropic.BadRequestError as exc:
            return ExtractResult("error", error=self._describe(exc), input_truncated=truncated)
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            return ExtractResult("error", error=self._describe(exc), input_truncated=truncated)

        self._record(resp)
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            return ExtractResult("refused", error=str(getattr(details, "category", None)), input_truncated=truncated)
        if resp.stop_reason == "max_tokens":
            return ExtractResult("truncated", error="hit max_tokens", input_truncated=truncated)
        try:
            data = PainExtraction.model_validate_json(self._text(resp)).model_dump()
        except ValueError as exc:  # pydantic.ValidationError is a ValueError
            return ExtractResult("error", error=f"bad structured output: {exc}"[:300], input_truncated=truncated)
        return ExtractResult("ok", data=data, input_truncated=truncated)

    # ------------------------------------------------------------ cluster merge

    def merge_labels(self, labels: list[str]) -> MergeResult:
        """Ask which short problem labels describe the same problem. Returns validated index groups."""
        listing = "\n".join(f"{i}. {label}" for i, label in enumerate(labels))
        try:
            resp = self._create(
                model=self.models["merge"],
                max_tokens=self.llm_cfg["solutions_max_tokens"],
                system=MERGE_SYSTEM,
                messages=[{"role": "user", "content": f"<labels>\n{listing}\n</labels>"}],
                output_config={
                    "format": {"type": "json_schema", "schema": self._merge_schema},
                    "effort": self.llm_cfg["merge_effort"],
                },
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise FatalLLMError(self._describe(exc)) from exc
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            return MergeResult("error", error=self._describe(exc)[:300])
        self._record(resp)
        if resp.stop_reason in ("refusal", "max_tokens"):
            return MergeResult("error", error=resp.stop_reason)
        try:
            parsed = MergeGroups.model_validate_json(self._text(resp))
        except ValueError as exc:
            return MergeResult("error", error=f"bad structured output: {exc}"[:300])
        return MergeResult("ok", sanitize_groups(parsed.groups, len(labels)))

    # ------------------------------------------------------------ solution check

    def search_solutions(self, label: str, context: dict) -> SolutionResult:
        prompt = SOLUTION_SEARCH_PROMPT.format(
            label=label,
            personas=", ".join(context.get("personas") or ["unknown"]),
            competitors=", ".join(context.get("competitors") or ["none named"]),
            quotes="\n".join(f'- "{q}"' for q in (context.get("quotes") or [])) or "- (none)",
        )
        tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": self.llm_cfg["max_web_searches"]}]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        findings: list[str] = []
        seen_urls: list[str] = []
        try:
            for _ in range(self.llm_cfg["max_search_turns"]):
                resp = self._create(
                    model=self.models["solutions"],
                    max_tokens=self.llm_cfg["solutions_max_tokens"],
                    tools=tools,
                    messages=messages,
                    output_config={"effort": self.llm_cfg["solutions_effort"]},
                )
                self._record(resp)
                if resp.stop_reason == "refusal":
                    return SolutionResult("error", error="refused")
                for block in resp.content:
                    btype = getattr(block, "type", "")
                    if btype == "text":
                        findings.append(block.text)
                    elif btype == "web_search_tool_result":
                        content = getattr(block, "content", None)
                        if isinstance(content, list):  # an error is an object, not a list
                            seen_urls.extend(u for u in (getattr(r, "url", None) for r in content) if u)
                if resp.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": resp.content})
                    continue
                break

            if not seen_urls:
                return SolutionResult("no_results", error="web search returned no results")

            structured = self._create(
                model=self.models["solutions"],
                max_tokens=self.llm_cfg["solutions_max_tokens"],
                messages=[{
                    "role": "user",
                    "content": SOLUTION_STRUCTURE_PROMPT.format(
                        label=label, urls="\n".join(sorted(set(seen_urls))), findings="\n".join(findings)
                    ),
                }],
                output_config={
                    "format": {"type": "json_schema", "schema": self._solution_schema},
                    "effort": "low",
                },
            )
            self._record(structured)
            if structured.stop_reason in ("refusal", "max_tokens"):
                return SolutionResult("error", error=structured.stop_reason, urls_seen=len(seen_urls))
            parsed = SolutionCheck.model_validate_json(self._text(structured)).model_dump()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise FatalLLMError(self._describe(exc)) from exc
        except (anthropic.APIStatusError, anthropic.APIConnectionError, ValueError) as exc:
            return SolutionResult("error", error=self._describe(exc)[:300])

        parsed = finalize_solution_check(parsed, seen_urls)
        return SolutionResult("ok", data=parsed, urls_seen=len(set(seen_urls)))
