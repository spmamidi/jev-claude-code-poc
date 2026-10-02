# Jev × Claude Code POC

**What:** Two [Claude Code](https://docs.anthropic.com/en/docs/claude-code) hooks powered by
[Jev](https://www.typesafe.ai) (TypeSafe AI's "System One" decision model) — a non-generative
model that returns calibrated decisions (Choice / Score / Noul) in one forward pass,
in ~70–500ms, at a fraction of a frontier-LLM call's cost.

1. **`hooks/jev_risk_gate.py`** — `PreToolUse` gate on `Bash | Write | Edit | MultiEdit | NotebookEdit`.
   Asks Jev three Noul questions per tool call (destructive? outward-facing? credential-touching?)
   and maps the top probability to **allow / ask / deny**. Fail-open on any error.
2. **`hooks/jev_tier_router.py`** — `PreToolUse` on `Task | Agent`. Asks Jev one Choice question
   over `{haiku, sonnet, opus}` for the subagent brief and rewrites the call via
   `updatedInput.model`. Low-confidence picks fall back to `sonnet` (never downgrades on a shaky read).

Jev replaces the *bounded judgments* (router/judge calls, JSON-schema retry loops), never the
agent's reasoning. Rule of thumb from community measurements: **route whole work units
(sessions/subagents), not mid-conversation prompts** — mid-loop steering busts the prompt cache.

## Quick start

```bash
pip install typesafe-sdk          # live mode; omit for mock mode
export TYPESAFE_API_KEY="..."     # get one at https://console.typesafe.ai
export JEV_SHADOW=1               # shadow mode first: observe, never block

# wire into a project
cp .claude/settings.example.json /path/to/project/.claude/settings.json
# (adjust $CLAUDE_PROJECT_DIR — in settings.json it resolves to the project root)
```

Run a normal Claude Code session. Every gated tool call appends one JSONL line to
`~/.jev-poc/shadow.jsonl` (override with `JEV_SHADOW_LOG`).

## Modes & env vars

| Var | Default | Meaning |
|---|---|---|
| `JEV_SHADOW` | `1` | `1` = observe only (hook always approves, logs verdict). Set `0` to enforce. |
| `JEV_MOCK` | auto | `1` = deterministic keyword mock (no key needed). Auto-on when no `TYPESAFE_API_KEY`. |
| `TYPESAFE_API_KEY` | — | Live Jev API key. |
| `JEV_MODEL` | `jev-latest` | Model id. |
| `JEV_TIMEOUT_S` | `2.0` | API timeout; on timeout the gate **fails open** (allows + logs). |
| `JEV_DENY_AT` / `JEV_ASK_AT` | `0.80` / `0.50` | Risk-gate thresholds on the top Noul probability. |
| `JEV_SHADOW_LOG` | `~/.jev-poc/shadow.jsonl` | Shadow log path. |

**Rollout order:** shadow (`JEV_SHADOW=1`, mock or live) → review `eval/shadow_report.py` →
enforce (`JEV_SHADOW=0`) on the risk gate only after the would-block rate looks sane.
Keep the tier router in shadow until its accuracy is measured on your briefs.

## Eval

```bash
python3 -m unittest discover -s tests          # 20 unit tests, no key needed
python3 eval/run_eval.py                        # labeled fixtures (mock by default)
JEV_MOCK=0 TYPESAFE_API_KEY=... python3 eval/run_eval.py --live
python3 eval/shadow_report.py                   # aggregate real shadow logs
```

`eval/fixtures_risk.jsonl` (20 labeled tool calls) and `eval/fixtures_tier.jsonl`
(18 labeled briefs) are samples — expand toward ~200 human-labeled calls for a real
measurement. The harness checks the POC success criteria:

- risk-gate agreement with labels ≥ 0.95, deny-band recall ≥ 0.90
- p95 latency < 600 ms, zero fail-open incidents in live mode
- tier accuracy ≥ 0.80, zero unsafe downgrades (never route below the expected tier)
- estimated $/decision and $/day reported from live runs

## Recommendation: how this helps Qwipo vs aiVora

**aiVora (direct fit).** This POC is a working prototype of the Decision→Action gate at the
heart of aiVora's runtime loop. The risk gate is the *policy enforcement point* pattern every
domain needs, with calibrated numbers instead of vibes:
- **Legal (Lexari):** privilege-screening gate — "does this document contain privileged material?
  p=0.93 → quarantine route" on every document, not just sampled ones.
- **Health (Cura):** safety-critical gating — "needs clinician review?" on every agent-drafted
  patient message or prior-auth recommendation; auto-send only below a calibrated threshold.
- **Insurance (Harbor):** straight-through-processing vs adjuster routing — fraud-score and
  `{auto-adjudicate, adjuster review, SIU referral}` Choice per claim, inline at ~$0.00003/decision.
- **Real Estate (Terra):** "requires broker review?" on AI-drafted listings/offers; lead-tier
  routing. Bounded decisions brokerages already make by gut feel, now with a calibrated number.
- The tier router is the cost-control primitive for a 4-domain platform: cheap tiers for
  routine work, frontier models only where Jev's confidence says they're needed.

**Qwipo (transfer).** The same two patterns port directly to Qwipo's agent ambitions
(DigiDukaan retailer ops): a risk gate in front of any agent that touches seller/retailer
data (refunds, catalog edits, payouts — the exact blast radius that needs allow/ask/deny),
and tier routing to keep support-agent inference spend down. Code is plain Python hooks —
no framework lock-in — so a successful POC here transfers to Qwipo's Azure DevOps
environment as-is (the hooks are Claude Code-specific, but the `JevClient` + policy
functions are harness-agnostic).

## Limits & honest caveats

- Jev is in **limited early access** (Sept 2026); pricing/latency figures are vendor-published.
  Re-verify on your own decision tasks before relying on them.
- Jev **cannot** generate, reason open-endedly, explain itself, or recover evidence it wasn't given
  ("retrieval still sets the ceiling").
- The injection-screening signal is a *filtering* signal, not a security boundary.
- Never ship full session transcripts to a third-party API for compaction-style use without a
  privacy review (some community compaction hooks do this — this POC does not).
- Mock mode validates wiring only; all quality bars apply to live Jev.

## Layout

```
hooks/  jev_risk_gate.py, jev_tier_router.py   # Claude Code hook entry points
src/jev_poc/
  client.py      # JevClient: typesafe-sdk -> raw HTTPS -> deterministic mock
  risk_gate.py   # Noul questions + allow/ask/deny policy (pure, tested)
  tier_router.py # Choice question + tier pick policy (pure, tested)
  shadow.py      # JSONL shadow logging
eval/   run_eval.py, shadow_report.py, fixtures_*.jsonl
tests/  test_logic.py                            # 20 unit tests, no key needed
```
