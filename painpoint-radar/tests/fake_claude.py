"""Stand-in for the `claude` CLI: speaks just enough of `claude -p` for the Claude Code backend tests."""

import json
import os
import re
import sys

sys.path.insert(0, os.environ["FAKE_CLAUDE_TESTS_DIR"])
import fakes  # noqa: E402

args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1] if name in args else None


prompt = sys.stdin.read()
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
        fh.write(json.dumps({"args": args, "prompt": prompt, "cwd": os.getcwd(),
                             "has_api_key": "ANTHROPIC_API_KEY" in os.environ}) + "\n")

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
usage = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 20}


def result(**kw):
    base = {"type": "result", "subtype": "success", "is_error": False, "usage": usage, "total_cost_usd": 0.01,
            "stop_reason": "end_turn", "result": "", "api_error_status": None}
    base.update(kw)
    return base


if mode == "limit":
    print(json.dumps(result(is_error=True, api_error_status=429, result="You've hit your usage limit. Resets 5pm.")))
    sys.exit(1)
if mode == "auth":
    print(json.dumps(result(is_error=True, api_error_status=401, result="Invalid API key - Please run /login")))
    sys.exit(1)
if mode == "garbage":
    print("this is not json")
    sys.exit(0)

if opt("--tools") == "WebSearch":  # solution search, stream-json
    events = [
        {"type": "system"},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content":
            'Links: [{"title": "Invoicely", "url": "https://www.invoicely.example/pricing"}, '
            '{"title": "Review", "url": "https://reviews.example/best-invoice-tools"}]'}]}},
        result(result="Invoicely automates reminders ($19/mo)."),
    ]
    print("\n".join(json.dumps(e) for e in events))
    sys.exit(0)

schema = json.dumps(json.loads(opt("--json-schema"))) if opt("--json-schema") else ""
if "saturation" in schema:
    out = {"solutions": [
        {"name": "Invoicely", "url": "https://www.invoicely.example/pricing", "kind": "direct_saas",
         "what_it_does": "Automated invoice reminders", "pricing_note": "$19/mo", "weakness_or_gap": None},
        {"name": "Imaginary Pay", "url": "https://made-up.example/product", "kind": "direct_saas",
         "what_it_does": "Does everything", "pricing_note": None, "weakness_or_gap": None}],
        "saturation": 6, "confidence": "medium", "market_summary": "A few tools.", "gap_notes": "Basic reminders."}
elif "<labels>" in prompt:
    out = json.loads(fakes.merge_json(prompt))
else:  # batch extraction
    out = {"results": []}
    for m in re.finditer(r'<post n="(\d+)"[^>]*>.*?</post>', prompt, re.S):
        n = int(m.group(1))
        if mode == "drop_one" and n == 1:
            continue
        item = json.loads(fakes.extraction_json(m.group(0)))
        item["n"] = n
        out["results"].append(item)
print(json.dumps(result(structured_output=out, stop_reason="tool_use")))
