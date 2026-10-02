"""Unit tests for the Jev x Claude Code POC.

No API key needed: covers the pure policy functions, the deterministic
mock client, hook I/O parsing, and shadow logging.
Run:  python3 -m unittest discover -s tests -v   (from repo root)
"""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev_poc import client as jev_client_mod
from jev_poc import risk_gate, shadow, tier_router


class TestRiskPolicy(unittest.TestCase):
    def test_deny_band(self):
        v = risk_gate.decide({"destructive": 0.93, "outward_facing": 0.1})
        self.assertEqual(v["decision"], "deny")
        self.assertEqual(v["trigger"], "destructive")

    def test_ask_band(self):
        v = risk_gate.decide({"outward_facing": 0.62})
        self.assertEqual(v["decision"], "ask")

    def test_allow_band(self):
        v = risk_gate.decide({"destructive": 0.2, "outward_facing": 0.05})
        self.assertEqual(v["decision"], "allow")

    def test_empty_probs_fail_open(self):
        v = risk_gate.decide({})
        self.assertEqual(v["decision"], "allow")

    def test_custom_thresholds(self):
        v = risk_gate.decide({"destructive": 0.7}, deny_at=0.9, ask_at=0.6)
        self.assertEqual(v["decision"], "ask")

    def test_summarize_bash(self):
        s = risk_gate.summarize_tool_input("Bash", {"command": "ls"})
        self.assertIn("Bash", s)

    def test_summarize_write_truncates(self):
        s = risk_gate.summarize_tool_input(
            "Write", {"file_path": "a.py", "content": "x" * 5000})
        self.assertLessEqual(len(s), 1200)


class TestTierPolicy(unittest.TestCase):
    def test_confident_pick_kept(self):
        p = tier_router.pick({"choice": "haiku", "confidence": 0.85})
        self.assertEqual(p["tier"], "haiku")
        self.assertFalse(p["overridden"])

    def test_low_confidence_downgrade_blocked(self):
        p = tier_router.pick({"choice": "haiku", "confidence": 0.4})
        self.assertEqual(p["tier"], "sonnet")
        self.assertTrue(p["overridden"])

    def test_unknown_choice_falls_back(self):
        p = tier_router.pick({"choice": "ultra", "confidence": 0.9})
        self.assertEqual(p["tier"], "sonnet")

    def test_brief_extraction(self):
        b = tier_router.brief_from_tool_input(
            {"description": "fix bug", "prompt": "in auth.py"})
        self.assertIn("fix bug", b)

    def test_empty_brief_flagged(self):
        b = tier_router.brief_from_tool_input({})
        self.assertIn("unroutable", b)


class TestMockClient(unittest.TestCase):
    def setUp(self):
        self.jev = jev_client_mod.JevClient(mock=True)

    def test_mock_flags_itself(self):
        r = self.jev.evaluate({"t": "x"}, {"q": {"type": "noul", "instructions": "?"}})
        self.assertTrue(r["_meta"]["mock"])

    def test_mock_destructive_scores_high(self):
        r = self.jev.evaluate(
            {"call": "Bash: rm -rf /tmp/x"},
            {"destructive": {"type": "noul", "instructions": "destructive?"}})
        self.assertGreater(r["answers"]["destructive"]["noul"], 0.5)

    def test_mock_safe_scores_low(self):
        r = self.jev.evaluate(
            {"call": "Bash: git status --short"},
            {"destructive": {"type": "noul", "instructions": "destructive?"}})
        self.assertLess(r["answers"]["destructive"]["noul"], 0.5)

    def test_mock_is_deterministic(self):
        q = {"destructive": {"type": "noul", "instructions": "destructive?"}}
        a = self.jev.evaluate({"call": "Bash: ls"}, q)
        b = self.jev.evaluate({"call": "Bash: ls"}, q)
        self.assertEqual(a["answers"], b["answers"])

    def test_mock_tier_routes_trivial_to_haiku(self):
        r = self.jev.evaluate(
            {"brief": "Fix typo in README"},
            tier_router.ROUTER_QUESTION)
        self.assertEqual(r["answers"]["tier"]["choice"], "haiku")

    def test_error_shape_is_fail_open_friendly(self):
        jev = jev_client_mod.JevClient(api_key="bogus", mock=False)
        # unreachable host -> error captured in _meta, no exception
        os.environ["JEV_API_URL"] = "http://127.0.0.1:1/nope"
        try:
            r = jev_client_mod.JevClient(api_key="bogus", mock=False).evaluate(
                {"t": "x"}, {"q": {"type": "noul", "instructions": "?"}})
            self.assertIn("error", r["_meta"])
        finally:
            del os.environ["JEV_API_URL"]


class TestShadowLog(unittest.TestCase):
    def test_append_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["JEV_SHADOW_LOG"] = os.path.join(d, "s.jsonl")
            try:
                shadow.append({"hook": "risk_gate", "verdict": "allow"})
                recs = shadow.read_all()
                self.assertEqual(len(recs), 1)
                self.assertIn("ts", recs[0])
            finally:
                del os.environ["JEV_SHADOW_LOG"]

    def test_corrupt_lines_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            os.environ["JEV_SHADOW_LOG"] = p
            try:
                with open(p, "w") as f:
                    f.write('{"ok": true}\nnot json\n')
                self.assertEqual(len(shadow.read_all()), 1)
            finally:
                del os.environ["JEV_SHADOW_LOG"]


if __name__ == "__main__":
    unittest.main()
