"""End-to-end test: run the full mock-LLM incident, confirm all required artifacts, and
confirm 2-candidate floor-splits-perfectly info-gain is exactly 1 bit.
Run: python agent/tests/test_e2e.py   (stdlib unittest, no deps)"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pinpoint import localization as L
from pinpoint import orchestrator
from pinpoint import config as C


REQUIRED_ARTIFACTS = {
    "intake.json", "plan.md", "brief.json", "candidates.json", "location.json",
    "site.json", "route.json", "audit.json", "packet.md", "trace.jsonl",
}


class TestFloorSplit(unittest.TestCase):
    """best_question on a 2-candidate case where floor perfectly splits must choose
    floor and yield expected info gain of 1.0 bit (prior H = 1, posterior = 0)."""

    def test_perfect_split_is_one_bit(self):
        cands = [
            {"node_id": "A", "score": 0.5,
             "attributes": {"floor": 1, "has_window": True, "room_type": "lobby"}},
            {"node_id": "B", "score": 0.5,
             "attributes": {"floor": 2, "has_window": True, "room_type": "lobby"}},
        ]
        q = L.best_question(cands)
        self.assertEqual(q["attribute"], "floor")
        self.assertTrue(q["eligible"])
        self.assertAlmostEqual(q["expected_info_gain"], 1.0, places=4)


class TestEndToEnd(unittest.TestCase):
    """Run the orchestrator mock-LLM path and verify every artifact + evidence chain."""

    @classmethod
    def setUpClass(cls):
        intake = json.loads((C.FIXTURES / "intake_sample.json").read_text())
        cls.res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        cls.inc_dir = Path(cls.res["dir"])

    def test_all_required_artifacts_present(self):
        present = {p.name for p in self.inc_dir.iterdir()}
        missing = REQUIRED_ARTIFACTS - present
        self.assertFalse(missing, f"missing artifacts: {missing}")

    def test_every_packet_claim_has_a_source(self):
        audit = json.loads((self.inc_dir / "audit.json").read_text())
        self.assertEqual(audit["unsupported"], [],
                         f"unsupported claims: {audit['unsupported']}")
        self.assertEqual(audit["checked"], audit["supported"])
        self.assertGreater(audit["checked"], 0)

    def test_approval_gate_fired(self):
        tool_names = []
        for line in (self.inc_dir / "trace.jsonl").read_text().splitlines():
            tool_names.append(json.loads(line)["tool"])
        self.assertIn("interrupt:send_packet", tool_names)
        gate_ix = tool_names.index("interrupt:send_packet")
        self.assertIn("send_packet", tool_names[gate_ix:])

    def test_records_locator_site_run_in_parallel(self):
        """Start times (elapsed_ms) for the three subagents' main tool calls must bunch
        within ~20ms; sequential would show much larger gaps."""
        recs = [json.loads(l) for l in (self.inc_dir / "trace.jsonl").read_text().splitlines()]
        starts = {}
        for r in recs:
            if r["actor"] in ("records", "locator", "site") and r["tool"] in (
                    "get_property", "match_photo", "detect_site_features"):
                starts[r["actor"]] = r["elapsed_ms"]
        self.assertEqual(set(starts), {"records", "locator", "site"})
        spread = max(starts.values()) - min(starts.values())
        self.assertLess(spread, 20.0, f"starts spread {spread}ms suggests sequential")

    def test_dock_beats_front_door_in_packet(self):
        packet = (self.inc_dir / "packet.md").read_text()
        self.assertIn("ext_dock", packet)
        self.assertIn("NOT the street-address door", packet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
