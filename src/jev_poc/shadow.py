"""JSONL shadow logging shared by the hooks.

Every hook invocation appends one line, whether it blocked or not, so the
eval harness can compute would-block rates, latency percentiles, and
estimated $/day from real session data.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

DEFAULT_LOG = os.path.expanduser("~/.jev-poc/shadow.jsonl")


def log_path() -> str:
    return os.environ.get("JEV_SHADOW_LOG", DEFAULT_LOG)


def append(record: dict) -> None:
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch_ms": int(time.time() * 1000),
        **record,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def read_all(path: str | None = None):
    path = path or log_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out
