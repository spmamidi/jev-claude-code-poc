"""jev_poc: Jev (TypeSafe System One) decision gates for Claude Code.

Modules:
  client      - JevClient: live calls via `typesafe-sdk` (falls back to raw
                HTTPS), plus a deterministic mock for testing without a key.
  risk_gate   - PreToolUse hook logic: Noul risk scoring -> allow/ask/deny.
  tier_router - PreToolUse hook logic: Choice-based subagent model routing.
  shadow      - JSONL shadow logging used by both hooks.
"""

__version__ = "0.1.0"
