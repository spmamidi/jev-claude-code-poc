"""Risk-gate policy: Noul probabilities -> allow / ask / deny.

Pure function, fully unit-testable without a Jev key.
"""
from __future__ import annotations

import os

DENY_AT = float(os.environ.get("JEV_DENY_AT", "0.80"))
ASK_AT = float(os.environ.get("JEV_ASK_AT", "0.50"))

RISK_QUESTIONS = {
    "destructive": {
        "type": "noul",
        "instructions": (
            "Would executing this tool call delete, overwrite, or irreversibly "
            "modify files, data, or infrastructure? Answer with the probability "
            "that it is destructive or irreversible."
        ),
    },
    "outward_facing": {
        "type": "noul",
        "instructions": (
            "Does this tool call send data outside the local machine — network "
            "requests, uploads, publishing, payments, or messages to external "
            "services? Answer with the probability."
        ),
    },
    "credential_touching": {
        "type": "noul",
        "instructions": (
            "Does this tool call read, transmit, print, or otherwise expose "
            "credentials, API keys, tokens, passwords, or other secrets? "
            "Answer with the probability."
        ),
    },
}


def decide(probs: dict[str, float], deny_at: float = DENY_AT,
           ask_at: float = ASK_AT) -> dict:
    """Return {"decision", "max_prob", "trigger", "reason"}.

    - deny  : max_prob >= deny_at
    - ask   : ask_at <= max_prob < deny_at
    - allow : max_prob < ask_at
    """
    if not probs:
        return {"decision": "allow", "max_prob": 0.0, "trigger": None,
                "reason": "no risk scores returned (fail-open)"}
    trigger = max(probs, key=lambda k: probs[k])
    top = probs[trigger]
    if top >= deny_at:
        return {"decision": "deny", "max_prob": top, "trigger": trigger,
                "reason": (f"jev risk gate: '{trigger}' scored {top:.2f} "
                           f"(>= {deny_at:.2f}); refusing once with reason so the "
                           "agent can answer it")}
    if top >= ask_at:
        return {"decision": "ask", "max_prob": top, "trigger": trigger,
                "reason": (f"jev risk gate: '{trigger}' scored {top:.2f}; "
                           "needs human confirmation")}
    return {"decision": "allow", "max_prob": top, "trigger": trigger,
            "reason": f"jev risk gate: highest risk '{trigger}' scored {top:.2f}"}


def summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Compact, secret-safe rendering of the tool call for the Jev state."""
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))[:1500]
        return f"Bash: {cmd}"
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path", tool_input.get("notebook_path", "?"))
        snippet = str(tool_input.get("content", tool_input.get("new_string", "")))[:800]
        return f"{tool_name} {path}: {snippet}"
    return f"{tool_name}: {str(tool_input)[:1200]}"
