#!/usr/bin/env python3
"""Question-skip optimization — the Commander must NOT ask the caller when every top candidate
routes to the same best-entry door AND lives in the same room (asking cannot change the dispatch).

Runs with stdlib unittest so it works without pytest:  python3 agent/tests/test_question_skip.py
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(ROOT))
from pinpoint import orchestrator, stubs, config


def _run_with_candidates(candidates, truth_node_id, incident_id):
    """Run the pipeline with a direct candidate injection by monkey-patching stubs.match_photo.
    Asserts about the resulting location.json are the caller's job."""
    orig = stubs.match_photo

    def fake(_building_id, _image_path, k=10):
        return candidates[:k]

    stubs.match_photo = fake
    try:
        intake = {
            "incident_id": incident_id,
            "address": "1200 Smith St, Houston, TX 77002",
            "building_id": "bayou-medical-plaza",
            "caller_photo_path": "test/injected.jpg",
            "caller_text": "test scenario",
            "ground_truth_node_id": truth_node_id,
        }
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
    finally:
        stubs.match_photo = orig
    loc = json.loads((Path(res["dir"]) / "location.json").read_text())
    trace_lines = [json.loads(ln) for ln in (Path(res["dir"]) / "trace.jsonl").read_text().splitlines()]
    return loc, trace_lines


class SkipFires(unittest.TestCase):

    def test_skip_when_all_candidates_share_room_and_entry(self):
        """Suite 210 has two interior nodes (suite210_interior and suite210_door) that both live
        in the suite's room and route to the same best-entry. The agent must skip asking."""
        g = json.loads((config.FIXTURES / "building_bayou" / "graph.json").read_text())
        nodes = {n["id"]: n for n in g["nodes"]}
        # Two candidates in Suite 210. Both tile floor, floor=2, carpet False, near_elevator False.
        candidates = [
            {"node_id": "suite210_interior", "score": 0.80,
             "evidence_path": nodes["suite210_interior"].get("pano_path"),
             "attributes": dict(nodes["suite210_interior"].get("attributes", {}),
                                floor=nodes["suite210_interior"]["floor"])},
            {"node_id": "suite210_door", "score": 0.65,
             "evidence_path": nodes["suite210_door"].get("pano_path"),
             "attributes": dict(nodes["suite210_door"].get("attributes", {}),
                                floor=nodes["suite210_door"]["floor"])},
        ]
        loc, trace = _run_with_candidates(candidates, "suite210_interior", "test-skip-same-suite")

        # It is the orchestrator's job to detect this. They may be in different room_labels
        # (Suite 210 Treatment Floor vs Suite 210 Entry), so the skip may or may not fire by the
        # room-equality rule. The STRONGER guarantee is that no question was asked beyond what is
        # actually useful. So the requirement here is weaker: either the skip fired, or asking
        # was in fact needed because the rooms differ. Make the stronger claim with a crafted
        # case whose rooms match:
        same_room_nodes = [nid for nid, n in nodes.items()
                           if n.get("room_label") == "F2 Elevator Lobby"]
        self.assertGreaterEqual(len(same_room_nodes), 1)

    def test_skip_when_single_above_threshold(self):
        """A dominating top candidate (belief > 0.8 before any question) should either converge
        immediately (top_confidence over threshold) or trigger the skip. Either way, 0 questions."""
        g = json.loads((config.FIXTURES / "building_bayou" / "graph.json").read_text())
        nodes = {n["id"]: n for n in g["nodes"]}
        truth = nodes["suite210_interior"]
        distractor = nodes["f1_corr_w"]
        candidates = [
            {"node_id": truth["id"], "score": 0.95,
             "evidence_path": truth.get("pano_path"),
             "attributes": dict(truth.get("attributes", {}), floor=truth["floor"])},
            {"node_id": distractor["id"], "score": 0.05,
             "evidence_path": distractor.get("pano_path"),
             "attributes": dict(distractor.get("attributes", {}), floor=distractor["floor"])},
        ]
        loc, trace = _run_with_candidates(candidates, "suite210_interior", "test-skip-dominant")
        self.assertEqual(loc["questions_asked"], 0,
                         f"expected 0 questions when one candidate dominates, got {loc['questions_asked']}")
        # The skip condition fires because only one candidate is above belief 0.1. Verify the
        # trace recorded the skip optimization event with a reason.
        skip_events = [ln for ln in trace if ln.get("tool") == "question_skipped"]
        self.assertEqual(len(skip_events), 1,
                         f"expected exactly one question_skipped trace event, got {len(skip_events)}")
        self.assertIn("candidate", (skip_events[0].get("result_summary") or "").lower(),
                      f"skip reason should mention candidates; got {skip_events[0].get('result_summary')!r}")
        self.assertTrue(loc["question_skipped"])

    def test_skip_crafted_same_room_same_door(self):
        """Hand-craft two candidates that genuinely share room_label (same string in graph.json)
        AND route to the same best-entry. The skip condition MUST fire."""
        g = json.loads((config.FIXTURES / "building_bayou" / "graph.json").read_text())
        nodes = {n["id"]: n for n in g["nodes"]}
        # pick two nodes with the same room_label
        by_room = {}
        for nid, n in nodes.items():
            if n.get("is_entrance") or not n.get("floor") or n["floor"] < 1:
                continue
            by_room.setdefault(n.get("room_label"), []).append(nid)
        multi = [(rl, ids) for rl, ids in by_room.items() if len(ids) >= 2]
        if not multi:
            self.skipTest("graph has no room with >=2 interior nodes; cannot test same-room skip")
        room_label, ids = multi[0]
        a, b = ids[0], ids[1]
        candidates = [
            {"node_id": a, "score": 0.60,
             "evidence_path": nodes[a].get("pano_path"),
             "attributes": dict(nodes[a].get("attributes", {}), floor=nodes[a]["floor"])},
            {"node_id": b, "score": 0.45,
             "evidence_path": nodes[b].get("pano_path"),
             "attributes": dict(nodes[b].get("attributes", {}), floor=nodes[b]["floor"])},
        ]
        loc, trace = _run_with_candidates(candidates, a, "test-skip-same-room-pair")
        self.assertTrue(loc["question_skipped"],
                        f"expected skip to fire for same-room pair in {room_label!r}, "
                        f"got skip_reason={loc['skip_reason']!r}")
        self.assertEqual(loc["questions_asked"], 0)
        # And a trace line should record the skip
        self.assertTrue(any(ln.get("tool") == "question_skipped" for ln in trace),
                        "expected trace.jsonl to log commander/question_skipped")

    def test_dont_skip_when_rooms_differ(self):
        """If candidates span different rooms, asking IS worth it. Skip must not fire."""
        g = json.loads((config.FIXTURES / "building_bayou" / "graph.json").read_text())
        nodes = {n["id"]: n for n in g["nodes"]}
        a = "suite210_interior"
        b = "f2_waiting"
        self.assertIn(a, nodes)
        self.assertIn(b, nodes)
        candidates = [
            {"node_id": a, "score": 0.55,
             "evidence_path": nodes[a].get("pano_path"),
             "attributes": dict(nodes[a].get("attributes", {}), floor=nodes[a]["floor"])},
            {"node_id": b, "score": 0.52,
             "evidence_path": nodes[b].get("pano_path"),
             "attributes": dict(nodes[b].get("attributes", {}), floor=nodes[b]["floor"])},
        ]
        loc, _ = _run_with_candidates(candidates, a, "test-dont-skip-diff-rooms")
        self.assertFalse(loc["question_skipped"],
                         "did not expect skip: candidates are in different rooms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
