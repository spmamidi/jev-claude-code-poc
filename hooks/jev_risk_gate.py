#!/usr/bin/env python3
"""Claude Code PreToolUse hook: Jev risk gate for Bash/Write/Edit.

Reads the hook JSON from stdin, scores the tool call with Jev Noul
questions, and returns a permission decision.

Modes (env):
  JEV_SHADOW=1     shadow mode: always allow, but log what the gate
                   *would* have decided. Run this first.
  JEV_MOCK=1       deterministic mock instead of the Jev API (no key needed).
  JEV_DENY_AT=0.80 / JEV_ASK_AT=0.50   decision thresholds.

Fail-open: any error (no key, timeout, API failure) -> allow + log.
Never ship transcripts anywhere except the Jev API call itself.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev_poc import client as jev_client_mod
from jev_poc import risk_gate, shadow

GATED_TOOLS = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        # Unparseable input: fail open, say nothing.
        print(json.dumps({}))
        return 0

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {}) or {}
    shadow_mode = os.environ.get("JEV_SHADOW", "1").lower() in ("1", "true", "yes")

    record = {
        "hook": "risk_gate",
        "tool": tool_name,
        "session_id": hook_input.get("session_id"),
        "shadow": shadow_mode,
    }

    if tool_name not in GATED_TOOLS:
        print(json.dumps({}))
        return 0

    state = {
        "tool": tool_name,
        "call": risk_gate.summarize_tool_input(tool_name, tool_input),
        "cwd": hook_input.get("cwd", ""),
    }

    jev = jev_client_mod.JevClient()
    result = jev.evaluate(state, risk_gate.RISK_QUESTIONS)
    meta = result["_meta"]
    probs = {
        qid: ans["noul"]
        for qid, ans in result["answers"].items()
        if ans.get("type") == "noul" and "noul" in ans
    }

    if meta.get("error"):
        verdict = {"decision": "allow", "max_prob": 0.0, "trigger": None,
                   "reason": f"jev unavailable ({meta['error']}); fail-open allow"}
    else:
        verdict = risk_gate.decide(probs)

    record.update({
        "probs": probs,
        "verdict": verdict["decision"],
        "trigger": verdict["trigger"],
        "max_prob": verdict["max_prob"],
        "latency_ms": round(meta["latency_ms"], 1),
        "mock": meta["mock"],
        "est_cost_usd": meta["est_cost_usd"],
        "error": meta.get("error"),
    })
    try:
        shadow.append(record)
    except Exception:
        pass

    if shadow_mode:
        # Observe only: never block in shadow mode.
        print(json.dumps({}))
        return 0

    decision = verdict["decision"]
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": verdict["reason"],
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
