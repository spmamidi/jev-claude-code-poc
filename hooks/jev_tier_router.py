#!/usr/bin/env python3
"""Claude Code PreToolUse hook: Jev subagent tier router.

On Task/Agent tool calls, asks Jev which model tier (haiku/sonnet/opus)
the brief deserves and rewrites the call via updatedInput.model.

Route whole work units, not mid-conversation prompts: the brief must be a
complete unit of work ("fix the login bug" is unroutable; "fix the OAuth
token refresh race in auth/refresh.py, add a regression test" routes).

Modes: JEV_SHADOW=1 (log only, default), JEV_MOCK=1 (no key needed).
Fail-open: any error -> no rewrite, log it.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev_poc import client as jev_client_mod
from jev_poc import shadow, tier_router

ROUTED_TOOLS = {"Task", "Agent"}


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        print(json.dumps({}))
        return 0

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {}) or {}
    shadow_mode = os.environ.get("JEV_SHADOW", "1").lower() in ("1", "true", "yes")

    if tool_name not in ROUTED_TOOLS:
        print(json.dumps({}))
        return 0

    brief = tier_router.brief_from_tool_input(tool_input)
    requested = tool_input.get("model") or tool_input.get("subagent_type") or ""

    record = {
        "hook": "tier_router",
        "tool": tool_name,
        "session_id": hook_input.get("session_id"),
        "shadow": shadow_mode,
        "brief_chars": len(brief),
        "requested_model": requested,
    }

    jev = jev_client_mod.JevClient()
    result = jev.evaluate({"brief": brief}, tier_router.ROUTER_QUESTION)
    meta = result["_meta"]
    answer = result["answers"].get("tier", {})

    if meta.get("error") or answer.get("error"):
        pick = {"tier": tier_router.FALLBACK_TIER, "confidence": 0.0,
                "overridden": True,
                "reason": f"jev unavailable ({meta.get('error')}); kept default"}
    else:
        pick = tier_router.pick(answer)

    record.update({
        "picked_tier": pick["tier"],
        "confidence": pick["confidence"],
        "overridden": pick["overridden"],
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
        print(json.dumps({}))
        return 0

    new_input = dict(tool_input)
    new_input["model"] = pick["tier"]
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": new_input,
        },
        "additionalContext": f"[jev tier router] brief routed to {pick['tier']} "
                             f"(confidence {pick['confidence']:.2f})",
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
