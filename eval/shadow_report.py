#!/usr/bin/env python3
"""Aggregate shadow-mode logs into an operator report.

Reads the JSONL shadow log (default ~/.jev-poc/shadow.jsonl) and prints:
  - decisions distribution per hook (what the gate *would* have done)
  - would-block rate (deny+ask share) — the human-review load to expect
  - p50/p95 latency and estimated $/day at observed call volume
  - mock vs live split

Usage: python3 eval/shadow_report.py [--log PATH] [--days N]
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from jev_poc import shadow


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q / 100 * len(s)))] if s else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    ap.add_argument("--days", type=float, default=7.0)
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    recs = [r for r in shadow.read_all(args.log)
            if r.get("ts", "") >= cutoff.isoformat()]

    if not recs:
        print(f"no shadow records in the last {args.days} days "
              f"(log: {args.log or shadow.log_path()})")
        return

    by_hook = {}
    for r in recs:
        by_hook.setdefault(r.get("hook", "?"), []).append(r)

    print(f"shadow records: {len(recs)} (last {args.days} days)\n")
    for hook, rs in by_hook.items():
        if hook == "risk_gate":
            dist = Counter(x.get("verdict", "?") for x in rs)
            would_block = (dist.get("deny", 0) + dist.get("ask", 0)) / len(rs)
            print(f"== risk_gate (n={len(rs)}) ==")
            print(f"  verdicts: {dict(dist)}")
            print(f"  would-block rate: {would_block:.1%} "
                  "(deny+ask — expected human-review load)")
        else:
            tiers = Counter(x.get("picked_tier", "?") for x in rs)
            print(f"== tier_router (n={len(rs)}) ==")
            print(f"  tiers: {dict(tiers)}")
        lat = [x.get("latency_ms", 0) for x in rs]
        cost = sum(x.get("est_cost_usd", 0) for x in rs)
        per_day = cost / args.days
        mock_share = sum(1 for x in rs if x.get("mock")) / len(rs)
        print(f"  latency ms p50/p95: {pct(lat, 50):.0f}/{pct(lat, 95):.0f}")
        print(f"  est. cost: ${cost:.4f} total, ${per_day:.4f}/day")
        print(f"  mock share: {mock_share:.0%}\n")


if __name__ == "__main__":
    main()
