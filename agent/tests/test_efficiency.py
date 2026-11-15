"""Efficiency specs: preplan cache, locator early exit, incremental verifier, routing fix."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parent.parent
sys.path.insert(0, str(AGENT_DIR.parent))  # repo root

from agent.pinpoint import routing, preplan, pipeline, orchestrator, config, stubs
from agent.pinpoint.errors import MatterportUnavailable


SAMPLE_INTAKE_PATH = AGENT_DIR / "pinpoint" / "fixtures" / "intake_sample.json"


def _load_sample_intake():
    return json.loads(SAMPLE_INTAKE_PATH.read_text())


def _load_trace(idir: Path) -> list:
    return [json.loads(ln) for ln in (idir / "trace.jsonl").read_text().splitlines() if ln.strip()]


class TestRoutingResolvesByBuildingId(unittest.TestCase):
    """F3: load_graph honors its building_id argument."""

    def setUp(self):
        routing.load_graph.cache_clear()

    def test_known_building_loads(self):
        g = routing.load_graph("bayou-medical-plaza")
        self.assertIn("nodes", g)
        self.assertGreater(len(g["nodes"]), 0, "graph should have nodes")

    def test_unknown_building_raises(self):
        with self.assertRaises(MatterportUnavailable):
            routing.load_graph("no-such-building-xyz")

    def test_alias_and_bare_folder_both_work(self):
        # 'building_bayou' is the on-disk folder name; must resolve too.
        g = routing.load_graph("building_bayou")
        self.assertIn("nodes", g)


class TestPreplanCache(unittest.TestCase):
    """Second incident on same building hits the preplan cache."""

    def setUp(self):
        preplan._reset_for_tests()
        routing.load_graph.cache_clear()

    def test_two_incidents_share_cache(self):
        intake = _load_sample_intake()
        res1 = orchestrator.run_incident(dict(intake), mock_llm=True, auto_approve=True)
        s1 = preplan.cache_stats()
        self.assertGreaterEqual(s1["misses"], 1, "first incident should populate cache")
        intake2 = dict(intake)
        intake2.pop("incident_id", None)
        res2 = orchestrator.run_incident(intake2, mock_llm=True, auto_approve=True)
        s2 = preplan.cache_stats()
        self.assertGreaterEqual(s2["hits"], 1,
                                f"second incident should register hits: stats={s2}")
        self.assertEqual(s2["misses"], s1["misses"],
                         "miss count must not grow on second run")

    def test_cache_stats_shape(self):
        preplan._reset_for_tests()
        stats = preplan.cache_stats()
        self.assertEqual(stats, {"hits": 0, "misses": 0})


class TestLocatorEarlyExit(unittest.TestCase):
    """When one answer dominates belief, stop asking even if MAX_QUESTIONS not reached."""

    def setUp(self):
        preplan._reset_for_tests()
        routing.load_graph.cache_clear()

    def _crafted_candidates(self):
        # Two REAL graph nodes with DIFFERENT room_labels and room_types so
        # should_skip_questions does not short-circuit. Top at belief 0.78 so the
        # pre-loop CONFIDENCE_THRESHOLD=0.80 does not break out first, but one
        # matching answer on room_type (ground truth suite210_interior => 'clinic')
        # blasts the top past EARLY_EXIT_THRESHOLD=0.95 on its first update.
        return [
            {"node_id": "suite210_interior",
             "score": 0.78, "belief": 0.78,
             "attributes": {"room_type": "clinic"},
             "evidence_path": "fixtures/building_bayou/match.json"},
            {"node_id": "lobby",
             "score": 0.22, "belief": 0.22,
             "attributes": {"room_type": "lobby"},
             "evidence_path": "fixtures/building_bayou/match.json"},
        ]

    def test_first_answer_triggers_early_exit(self):
        intake = _load_sample_intake()
        intake["incident_id"] = None
        # Force the crafted candidate set regardless of caller_photo_path.
        with patch.object(stubs, "match_photo",
                          return_value=self._crafted_candidates()):
            res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        idir = Path(res["dir"])
        location = json.loads((idir / "location.json").read_text())
        self.assertEqual(location["questions_asked"], 1,
                         f"expected 1 question, got {location['questions_asked']}")
        events = _load_trace(idir)
        tags = [(e["actor"], e["tool"]) for e in events]
        self.assertIn(("commander", "locator_early_exit"), tags,
                      f"locator_early_exit not in trace tags: {tags[-10:]}")


class TestIncrementalVerifier(unittest.TestCase):
    """Verification happens per-section, interleaved with subagent returns."""

    def setUp(self):
        preplan._reset_for_tests()
        routing.load_graph.cache_clear()

    def test_verify_section_events_interleaved(self):
        intake = _load_sample_intake()
        intake.pop("incident_id", None)
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        events = _load_trace(Path(res["dir"]))
        verify_events = [e for e in events if e["tool"].startswith("verify_section:")]
        self.assertGreater(len(verify_events), 1,
                           f"expected multiple verify_section events, got {len(verify_events)}")
        # Interleaving check: verify events are NOT all contiguous at the end.
        # Find a verify event index, then find a subagent return index AFTER it.
        indexes = [i for i, e in enumerate(events) if e["tool"].startswith("verify_section:")]
        first_verify = indexes[0]
        later_subagent_returns = [
            i for i, e in enumerate(events)
            if i > first_verify and e.get("phase") == "subagent"
        ]
        self.assertGreater(len(later_subagent_returns), 0,
                           "expected subagent activity after the first verify_section")
        # Audit stays clean on the happy path.
        audit = json.loads((Path(res["dir"]) / "audit.json").read_text())
        self.assertEqual(audit["supported"], audit["checked"],
                         f"unsupported after incremental verify: {audit}")
        self.assertEqual(audit.get("status"), "OK")


if __name__ == "__main__":
    unittest.main()
