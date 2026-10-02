"""Jev System One client.

Live path (in order of preference):
  1. `typesafe-sdk` package (`pip install typesafe-sdk`), reads TYPESAFE_API_KEY.
  2. Raw HTTPS POST to https://api.typesafe.ai/v1/systemone (stdlib only).

Mock path: JEV_MOCK=1 or no API key present. Deterministic keyword
heuristics — clearly labeled in `_meta.mock`. Good enough to test hook
wiring, thresholds, shadow logging, and the eval harness end to end.

Question spec (input):
    {"qid": {"type": "noul"|"choice"|"score",
             "instructions": "...",
             "criteria": {...} or [...] or None}}

evaluate() returns:
    {"answers": {"qid": {"type": "noul", "noul": 0.93} |
                          {"type": "choice", "choice": "sonnet",
                           "confidence": 0.81, "probabilities": {...}} |
                          {"type": "score", "score": 1.2}},
     "_meta": {"latency_ms": 123.4, "mock": False, "model": "jev-latest",
               "est_cost_usd": 0.000003}}
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request

JEV_API_URL = os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_S", "2.0"))
INPUT_USD_PER_MTOK = 0.042  # TypeSafe published pricing, input; outputs free.

# ---------------------------------------------------------------- mock ---
_DESTRUCTIVE_KW = (
    "rm -rf", "rm -r", "delete", "destroy", "drop table", "drop database",
    "overwrite", "truncate", "format", "mkfs", "dd if=", ":(){", "fork bomb",
    "kill -9", "pkill", "shutdown", "reboot", "git reset --hard",
    "git clean -fd", "npm run clean",
)
_OUTWARD_KW = (
    "curl", "wget", "ssh ", "scp ", "rsync", "ftp", "telnet", "nc ",
    "publish", "npm publish", "twine upload", "gh release",
    "sendmail", "mail ", "webhook", "slack", "discord",
)
_SECRET_KW = (
    "api_key", "apikey", "secret", "password", "passwd", "token",
    "private_key", "aws_secret", ".pem", "credentials",
)
_HARD_TASK_KW = (
    "architect", "redesign", "race condition", "deadlock", "security",
    "cryptograph", "distributed", "consensus", "migrate production",
    "threat model", "auth model", "migration", "calibrat", "evaluat",
    "fail-open", "fail-closed",
)
_TRIVIAL_TASK_KW = (
    "typo", "rename", "format", "comment", "docstring",
    "sort imports", "whitespace", "summar", "bullet",
)
# Obviously destructive: score at deny level even on a single hit.
_CRITICAL_KW = (
    "rm -rf", "rm -r ", "dd if=", "drop table", "drop database",
    ":(){", "mkfs", "format c:",
)


def _kw_score(text: str, keywords) -> float:
    t = text.lower()
    if any(k in t for k in _CRITICAL_KW):
        return 0.92
    hits = sum(1 for k in keywords if k in t)
    return min(0.95, 0.15 + 0.35 * hits) if hits else 0.05


def _mock_noul(qid: str, state_text: str) -> float:
    q = qid.lower()
    if "destruct" in q or "irreversib" in q:
        return _kw_score(state_text, _DESTRUCTIVE_KW)
    if "outward" in q or "network" in q or "external" in q:
        return _kw_score(state_text, _OUTWARD_KW)
    if "secret" in q or "credential" in q or "pii" in q:
        return _kw_score(state_text, _SECRET_KW)
    if "unrelated" in q or "scope" in q:
        return 0.05  # mock cannot judge task relevance; stays permissive
    # generic: hash-deterministic mid value so thresholds are exercisable
    h = int(hashlib.sha256((qid + state_text).encode()).hexdigest(), 16)
    return 0.05 + (h % 90) / 100.0


def _mock_choice(criteria: dict, state_text: str):
    keys = list(criteria.keys())
    t = state_text.lower()
    if any(k in t for k in _HARD_TASK_KW) and "opus" in keys:
        return "opus", 0.78
    if any(k in t for k in _TRIVIAL_TASK_KW) and "haiku" in keys:
        return "haiku", 0.82
    # default middle tier
    for cand in ("sonnet", "other"):
        if cand in keys:
            return cand, 0.64
    return keys[0], 0.55


# --------------------------------------------------------------- client ---
class JevClient:
    def __init__(self, api_key: str | None = None, mock: bool | None = None):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        env_mock = os.environ.get("JEV_MOCK", "").lower() in ("1", "true", "yes")
        self.mock = True if mock is None and (env_mock or not self.api_key) else bool(mock)
        self.model = JEV_MODEL

    # -- public ----------------------------------------------------------
    def evaluate(self, state, questions: dict) -> dict:
        """One parallel pass over all questions sharing the same state."""
        t0 = time.perf_counter()
        try:
            if self.mock:
                answers = self._evaluate_mock(state, questions)
                mock = True
            else:
                answers = self._evaluate_live(state, questions)
                mock = False
        except Exception as exc:  # fail-open: callers decide, never crash hooks
            return {
                "answers": {},
                "_meta": {
                    "latency_ms": (time.perf_counter() - t0) * 1000.0,
                    "mock": False,
                    "model": self.model,
                    "error": f"{type(exc).__name__}: {exc}",
                    "est_cost_usd": 0.0,
                },
            }
        latency_ms = (time.perf_counter() - t0) * 1000.0
        payload_chars = len(json.dumps(state, default=str)) + len(
            json.dumps(questions, default=str)
        )
        return {
            "answers": answers,
            "_meta": {
                "latency_ms": latency_ms,
                "mock": mock,
                "model": self.model,
                "est_cost_usd": (payload_chars / 4.0 / 1e6) * INPUT_USD_PER_MTOK,
            },
        }

    # -- live ------------------------------------------------------------
    def _evaluate_live(self, state, questions: dict) -> dict:
        try:
            from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
        except ImportError:
            return self._evaluate_http(state, questions)

        sdk_q = {}
        for qid, q in questions.items():
            qtype, instr, crit = q["type"], q["instructions"], q.get("criteria")
            if qtype == "noul":
                sdk_q[qid] = Noul(instructions=instr, criteria=crit)
            elif qtype == "choice":
                sdk_q[qid] = Choice(instructions=instr, criteria=crit or {})
            elif qtype == "score":
                sdk_q[qid] = Score(instructions=instr, criteria=crit or [])
            else:
                raise ValueError(f"unknown question type: {qtype}")

        with TypeSafeClient() as client:
            resp = client.system_one(state=state, questions=sdk_q)
        return self._translate_sdk(resp, questions)

    @staticmethod
    def _translate_sdk(resp, questions: dict) -> dict:
        out = {}
        for qid, q in questions.items():
            qtype = q["type"]
            try:
                if qtype == "noul":
                    ans = resp.nouls[qid]
                    out[qid] = {"type": "noul", "noul": float(ans.noul)}
                elif qtype == "choice":
                    ans = resp.choices[qid]
                    probs = getattr(ans, "probabilities", None)
                    out[qid] = {
                        "type": "choice",
                        "choice": str(ans.choice),
                        "confidence": float(getattr(ans, "confidence", 0.0)),
                        "probabilities": dict(probs) if probs else {},
                    }
                else:
                    ans = resp.scores[qid]
                    out[qid] = {"type": "score", "score": float(ans.score)}
            except (KeyError, AttributeError, TypeError):
                out[qid] = {"type": qtype, "error": "missing answer"}
        return out

    def _evaluate_http(self, state, questions: dict) -> dict:
        body = {
            "model": self.model,
            "state": state,
            "questions": {
                qid: {
                    "type": q["type"],
                    "instructions": q["instructions"],
                    **({"criteria": q["criteria"]} if q.get("criteria") else {}),
                }
                for qid, q in questions.items()
            },
        }
        req = urllib.request.Request(
            JEV_API_URL,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = json.loads(r.read().decode())
        return self._translate_http(raw, questions)

    @staticmethod
    def _translate_http(raw: dict, questions: dict) -> dict:
        out = {}
        buckets = {
            "noul": raw.get("nouls", {}),
            "choice": raw.get("choices", {}),
            "score": raw.get("scores", {}),
        }
        flat = raw.get("answers", {})
        for qid, q in questions.items():
            qtype = q["type"]
            ans = buckets[qtype].get(qid) or flat.get(qid)
            if not ans:
                out[qid] = {"type": qtype, "error": "missing answer"}
                continue
            if qtype == "noul":
                out[qid] = {"type": "noul", "noul": float(ans.get("noul", ans.get("prob", 0.0)))}
            elif qtype == "choice":
                out[qid] = {
                    "type": "choice",
                    "choice": str(ans.get("choice")),
                    "confidence": float(ans.get("confidence", 0.0)),
                    "probabilities": dict(ans.get("probabilities", {})),
                }
            else:
                out[qid] = {"type": "score", "score": float(ans.get("score", 0.0))}
        return out

    # -- mock ------------------------------------------------------------
    @staticmethod
    def _evaluate_mock(state, questions: dict) -> dict:
        state_text = json.dumps(state, default=str)
        out = {}
        for qid, q in questions.items():
            qtype = q["type"]
            if qtype == "noul":
                out[qid] = {"type": "noul", "noul": round(_mock_noul(qid, state_text), 3)}
            elif qtype == "choice":
                choice, conf = _mock_choice(q.get("criteria") or {}, state_text)
                out[qid] = {
                    "type": "choice",
                    "choice": choice,
                    "confidence": conf,
                    "probabilities": {choice: conf},
                }
            else:
                out[qid] = {"type": "score", "score": 1.0}
        return out
