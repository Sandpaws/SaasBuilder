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


@dataclass
class LLM:
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

    # ------------------------------------------------------------ plumbing

    def _create(self, **kwargs):
        if self.llm_cfg["use_fallbacks"]:
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
        used: set[int] = set()
        groups = []
        for group in parsed.groups:
            clean = []
            for i in group:
                if 0 <= i < len(labels) and i not in used:  # ignore out-of-range or reused numbers
                    used.add(i)
                    clean.append(i)
            if len(clean) >= 2:
                groups.append(clean)
        return MergeResult("ok", groups)

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

        allowed = {_domain(u) for u in seen_urls}
        verified = [
            s for s in parsed["solutions"] if any(_same_site(_domain(s["url"]), a) for a in allowed)
        ]
        parsed["solutions"] = verified
        parsed["saturation"] = max(0, min(10, int(parsed["saturation"])))
        if not verified and parsed["saturation"] > 2:
            # A high saturation score with no verifiable product behind it is not credible.
            parsed["saturation"] = 2
            parsed["confidence"] = "low"
        return SolutionResult("ok", data=parsed, urls_seen=len(set(seen_urls)))
