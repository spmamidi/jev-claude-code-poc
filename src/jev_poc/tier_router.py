"""Tier-router policy: Choice over model tiers for a subagent brief.

Pure function, fully unit-testable without a Jev key.
"""
from __future__ import annotations

TIERS = ("haiku", "sonnet", "opus")

TIER_CRITERIA = {
    "haiku": (
        "Small, well-scoped tasks: fixing typos, renames, formatting, "
        "simple lookups, running one known command, summarizing a file."
    ),
    "sonnet": (
        "Standard engineering tasks: bug fixes, feature implementation, "
        "refactors across a few files, writing tests, routine code review."
    ),
    "opus": (
        "Hard tasks: novel architecture, ambiguous requirements, deep "
        "debugging across systems, security-sensitive design, distributed "
        "systems reasoning."
    ),
    "other": "Anything that does not clearly fit haiku, sonnet, or opus.",
}

ROUTER_QUESTION = {
    "tier": {
        "type": "choice",
        "instructions": (
            "Which model tier should handle this subagent task? Choose the "
            "cheapest tier that can plausibly do the work well."
        ),
        "criteria": TIER_CRITERIA,
    }
}

# Below this confidence we do not trust the pick; fall back to the
# capable default instead of a possibly-too-cheap tier.
MIN_CONFIDENCE = 0.60
FALLBACK_TIER = "sonnet"


def pick(answer: dict) -> dict:
    """Map a Jev Choice answer to {"tier", "confidence", "overridden"}."""
    choice = (answer or {}).get("choice", "other")
    conf = float((answer or {}).get("confidence", 0.0))
    if choice not in TIERS:
        choice = FALLBACK_TIER
    if conf < MIN_CONFIDENCE and choice == "haiku":
        # Never downgrade on a shaky read; upgrading on a shaky read is
        # safe but wasteful, so hold the default there too.
        return {"tier": FALLBACK_TIER, "confidence": conf,
                "overridden": True, "reason": "low confidence, kept default"}
    if conf < MIN_CONFIDENCE:
        return {"tier": FALLBACK_TIER, "confidence": conf,
                "overridden": True, "reason": "low confidence, kept default"}
    return {"tier": choice, "confidence": conf, "overridden": False,
            "reason": f"jev tier router: {choice} at {conf:.2f}"}


def brief_from_tool_input(tool_input: dict) -> str:
    desc = str(tool_input.get("description", "")).strip()
    prompt = str(tool_input.get("prompt", "")).strip()
    brief = f"{desc}\n{prompt}".strip()
    return brief[:2000] if brief else "(empty brief — unroutable)"
