"""Verify run_real_agent persists the 9 incident artifacts and classifies interrupts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

try:
    from agent.pinpoint import deep_agent  # noqa: F401
    HAVE = True
except Exception:  # deepagents / langchain / pydantic missing
    HAVE = False


def _make_message(content, kind="ai"):
    """Build a minimal object with .content and no other attrs for extraction tests."""
    class _M:
        def __init__(self, c):
            self.content = c
    return _M(content)


@unittest.skipUnless(HAVE, "deep_agent module not importable in this env")
class PersistArtifactsTest(unittest.TestCase):
    def test_full_state_writes_all_nine_artifacts(self):
        from agent.pinpoint import deep_agent as da
        from agent.pinpoint import trace

        intake = {
            "incident_id": "inc-test",
            "address": "1200 Smith St, Houston, TX 77002",
            "building_id": "bayou-medical-plaza",
            "caller_photo_path": "fixtures/building_bayou/pano/suite210_interior.jpg",
            "caller_text": "collapsed in dialysis suite",
        }

        # Build a synthetic message history carrying each subagent's report as JSON in
        # the content string (the simplest shape _extract_report tolerates).
        reports = [
            {"building_id": "bayou-medical-plaza", "address": "1200 Smith St, Houston, TX 77002",
             "tenants": [], "contact": {"name": "Dr. Rao", "method": "sms"}, "evidence": ["costar/..."]},
            {"node_id": "suite210_interior", "room_id": "suite210", "room_label": "Suite 210 dialysis",
             "belief": 0.88, "candidates": [{"node_id": "suite210_interior", "belief": 0.88}],
             "questions_asked": 1, "evidence_path": "fixtures/.../match.json"},
            {"entrances": [{"id": "ext_dock"}], "staging": [], "obstructions": [],
             "aerial_tile_path": "fixtures/.../vexcel_tile.jpg", "evidence": []},
            {"best_entry_id": "ext_dock", "path_nodes": ["ext_dock", "corridor1", "suite210_interior"],
             "meters": 42.0, "seconds": 95.0, "hazards": [], "evidence_path": "graph/..."},
            {"status": "OK", "claims_total": 24, "claims_sourced": 24, "unsupported": []},
        ]
        messages = [_make_message(json.dumps(r)) for r in reports]

        # Fake agent state carrier.
        class _State:
            values = {"messages": messages, "files": {"packet.md": "# Responder Packet\n\n- All claims sourced [src:verifier]\n"}}

        class _Agent:
            def get_state(self, _): return _State()

        with tempfile.TemporaryDirectory() as tmp:
            idir = Path(tmp) / "inc-test"
            idir.mkdir()
            trace.start(idir)  # needed for _audit_from_trace path, though we have VerifierReport here
            da._persist_artifacts(_Agent(), {"configurable": {"thread_id": "inc-test"}}, intake, idir)

            # Confirm all 9 artifacts land.
            for name in ("intake.json", "plan.md", "brief.json", "location.json",
                          "candidates.json", "site.json", "route.json", "audit.json", "packet.md"):
                self.assertTrue((idir / name).exists(), f"missing {name}")

            # Contents sanity checks.
            self.assertEqual(json.loads((idir / "intake.json").read_text())["building_id"],
                             "bayou-medical-plaza")
            self.assertEqual(json.loads((idir / "brief.json").read_text())["building_id"],
                             "bayou-medical-plaza")
            self.assertEqual(json.loads((idir / "audit.json").read_text())["status"], "OK")
            self.assertEqual(json.loads((idir / "route.json").read_text())["best_entry_id"], "ext_dock")
            # candidates.json is just the list
            self.assertIsInstance(json.loads((idir / "candidates.json").read_text()), list)
            # packet.md was read from the virtual fs
            self.assertIn("Responder Packet", (idir / "packet.md").read_text())

    def test_route_falls_back_to_not_computed_stub(self):
        from agent.pinpoint import deep_agent as da
        from agent.pinpoint import trace

        intake = {"incident_id": "inc-noroute", "address": "1200 Smith St, Houston, TX",
                  "building_id": "bayou-medical-plaza"}
        # Only records + location + site; no RouteReport in the messages.
        reports = [
            {"building_id": "bayou-medical-plaza", "address": "1200 Smith St, Houston, TX",
             "tenants": [], "contact": None, "evidence": []},
            {"node_id": "suite210_interior", "room_id": "r", "room_label": "x",
             "belief": 0.7, "candidates": [], "questions_asked": 0, "evidence_path": "x"},
            {"entrances": [], "staging": [], "obstructions": [], "aerial_tile_path": None, "evidence": []},
        ]
        messages = [_make_message(json.dumps(r)) for r in reports]

        class _State:
            values = {"messages": messages, "files": {}}

        class _Agent:
            def get_state(self, _): return _State()

        with tempfile.TemporaryDirectory() as tmp:
            idir = Path(tmp) / "inc-noroute"
            idir.mkdir()
            trace.start(idir)
            da._persist_artifacts(_Agent(), {}, intake, idir)
            route = json.loads((idir / "route.json").read_text())
            self.assertEqual(route.get("status"), "not_computed")
            self.assertIn("router", route.get("reason", ""))

    def test_packet_renders_from_reports_when_virtual_fs_empty(self):
        from agent.pinpoint import deep_agent as da
        from agent.pinpoint import trace

        intake = {"incident_id": "inc-noblob", "address": "1200 Smith St, Houston, TX",
                  "building_id": "bayou-medical-plaza"}
        # VerifierReport only, so packet has at least something to render from.
        reports = [
            {"building_id": "bayou-medical-plaza", "address": "1200 Smith St, Houston, TX",
             "tenants": [], "contact": None, "evidence": []},
            {"status": "OK", "claims_total": 0, "claims_sourced": 0, "unsupported": []},
        ]
        messages = [_make_message(json.dumps(r)) for r in reports]

        class _State:
            values = {"messages": messages, "files": {}}

        class _Agent:
            def get_state(self, _): return _State()

        with tempfile.TemporaryDirectory() as tmp:
            idir = Path(tmp) / "inc-noblob"
            idir.mkdir()
            trace.start(idir)
            da._persist_artifacts(_Agent(), {}, intake, idir)
            packet = (idir / "packet.md").read_text()
            self.assertIn("Responder Packet", packet)


@unittest.skipUnless(HAVE, "deep_agent module not importable in this env")
class InterruptClassificationTest(unittest.TestCase):
    def test_action_requests_classified_by_tool_name(self):
        from agent.pinpoint import deep_agent as da

        # Simulate the interrupt payload shape deepagents actually surfaces.
        class _Int:
            def __init__(self, v): self.value = v

        pending = [_Int({"action_requests": [{"action": "send_packet", "args": {"incident_id": "inc-x"}}]})]
        req = da._interrupt_requests(pending)
        self.assertEqual(len(req), 1)
        self.assertEqual(req[0].get("action"), "send_packet")

        # Empty / malformed falls through.
        empty_pending = [_Int({"action_requests": []})]
        self.assertEqual(da._interrupt_requests(empty_pending), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
