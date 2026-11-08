"""Stand-in for the `agent-reach` CLI: answers `agent-reach reddit <action> ...` from canned envelopes.

FAKE_AR_SCRIPT is a JSON file {"rules": [...], "noise": bool}. Each rule has "match" (substrings that must all appear
in the space-joined arguments) and either "envelope" (printed as JSON) or "stdout" + "exit" (printed as is). The first
matching rule wins; with none, it answers like Agent Reach does for an empty result. Calls are logged to FAKE_AR_LOG.
"""

import json
import os
import sys

args = sys.argv[1:]
if os.environ.get("FAKE_AR_LOG"):
    with open(os.environ["FAKE_AR_LOG"], "a") as fh:
        fh.write(json.dumps(args) + "\n")

with open(os.environ["FAKE_AR_SCRIPT"]) as fh:
    script = json.load(fh)

joined = " ".join(args)
rule = next((r for r in script["rules"] if all(s in joined for s in r["match"])), None)
if rule and "stdout" in rule:
    print(rule["stdout"])
    sys.exit(rule.get("exit", 1))

envelope = rule["envelope"] if rule else {
    "schema_version": 1, "ok": False, "channel": "reddit", "action": args[1] if len(args) > 1 else None,
    "backend": None, "items": [], "meta": {"elapsed_ms": 1, "attempts": []},
    "error": {"code": "UPSTREAM_BROKEN", "message": "OpenCLI finished but returned no items", "hint": ""},
}
if script.get("noise"):
    print("(node:4242) ExperimentalWarning: something upstream printed first")
print(json.dumps(envelope))
sys.exit(0 if envelope.get("ok") else 1)
