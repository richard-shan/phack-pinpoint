"""Failure-mode specs for Pinpoint.

For each bad-input scenario the orchestrator should:
  (a) never crash,
  (b) produce packet.md with a plain-language explanation,
  (c) log a commander/failure_mode:<slug> trace event,
  (d) set audit.status to UNABLE or DEGRADED,
  (e) keep audit.unsupported == 0 (every claim still cites a source).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"
sys.path.insert(0, str(AGENT_DIR))

from pinpoint import orchestrator, config as cfg, stubs, vexcel


def _trace_events(idir):
    events = []
    for line in (idir / "trace.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def _has_failure_mode(events, mode):
    needle = f"failure_mode:{mode}"
    return any(e.get("tool") == needle for e in events)


class FailureModesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_incidents = cfg.INCIDENTS
        cfg.INCIDENTS = Path(self._tmp.name) / "incidents"
        orchestrator.INCIDENTS = cfg.INCIDENTS

    def tearDown(self):
        cfg.INCIDENTS = self._orig_incidents
        orchestrator.INCIDENTS = self._orig_incidents
        self._tmp.cleanup()

    def _load_outputs(self, res):
        idir = Path(res["dir"])
        packet = (idir / "packet.md").read_text()
        audit = json.loads((idir / "audit.json").read_text())
        events = _trace_events(idir)
        return idir, packet, audit, events

    def _assert_common(self, packet, audit, events, mode, expected_status_in=("UNABLE", "DEGRADED")):
        self.assertIn(audit.get("status"), expected_status_in,
                      f"status={audit.get('status')} not in {expected_status_in}")
        self.assertEqual(audit["supported"], audit["checked"],
                         f"{audit['checked'] - audit['supported']} claims unsupported")
        self.assertEqual(len(audit.get("unsupported", [])), 0,
                         "every claim must cite a source even on UNABLE/DEGRADED")
        self.assertTrue(_has_failure_mode(events, mode),
                        f"trace missing commander/failure_mode:{mode}")
        self.assertTrue(any(e.get("tool") == "done" and e.get("actor") == "commander"
                            for e in events),
                        "trace missing commander/done terminal event")

    # ---- 1. zero candidates from match_photo ----
    def test_empty_candidates_yields_degraded_packet(self):
        intake = {"address": "1200 Smith St, Houston, TX 77002",
                  "building_id": "bayou-medical-plaza",
                  "caller_photo_path": "no_match/this/wont/resolve.jpg",
                  "caller_text": "someone fell"}
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        _, packet, audit, events = self._load_outputs(res)
        self.assertIn("Unable to match the caller photo", packet)
        self.assertIn("main entrance", packet)
        self._assert_common(packet, audit, events, "empty_candidates", ("DEGRADED",))

    # ---- 2. address not in CoStar ----
    def test_property_not_found_yields_unable_packet(self):
        intake = {"address": "UNKNOWN 9999 Nowhere Rd",
                  "building_id": "bayou-medical-plaza",
                  "caller_photo_path": "fixtures/building_bayou/pano/suite210_interior.jpg",
                  "caller_text": "need help"}
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        _, packet, audit, events = self._load_outputs(res)
        self.assertIn("Address not found in the property database", packet)
        self.assertIn("UNKNOWN 9999 Nowhere Rd", packet)
        self._assert_common(packet, audit, events, "property_not_found", ("UNABLE",))

    # ---- 3. no Matterport twin for this building ----
    def test_matterport_unavailable_yields_degraded_packet(self):
        intake = {"address": "1200 Smith St, Houston, TX 77002",
                  "building_id": "no_twin",
                  "caller_photo_path": "fixtures/building_bayou/pano/suite210_interior.jpg",
                  "caller_text": "need help"}
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        _, packet, audit, events = self._load_outputs(res)
        self.assertIn("Unable to match the caller photo", packet)
        self._assert_common(packet, audit, events, "matterport_unavailable", ("DEGRADED",))

    # ---- 4. caller photo path missing or unreadable ----
    def test_photo_path_zero_byte_yields_degraded(self):
        intake = {"address": "1200 Smith St, Houston, TX 77002",
                  "building_id": "bayou-medical-plaza",
                  "caller_photo_path": None,  # missing
                  "caller_text": "need help"}
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        _, packet, audit, events = self._load_outputs(res)
        # intake validation treats None photo_path as incomplete rather than empty_candidates
        # (see _unable_intake_incomplete short-circuit). Either slug is acceptable; assert that
        # the agent did not crash and the audit is clean.
        self.assertIn(audit.get("status"), ("UNABLE", "DEGRADED"))
        self.assertEqual(audit["supported"], audit["checked"])

    def test_photo_path_zero_byte_file_on_disk_yields_degraded(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            empty_path = f.name   # created zero-byte
        try:
            intake = {"address": "1200 Smith St, Houston, TX 77002",
                      "building_id": "bayou-medical-plaza",
                      "caller_photo_path": empty_path,
                      "caller_text": "need help"}
            res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
            _, packet, audit, events = self._load_outputs(res)
            self.assertIn("Unable to match the caller photo", packet)
            self._assert_common(packet, audit, events, "empty_candidates", ("DEGRADED",))
        finally:
            os.unlink(empty_path)

    # ---- 5. intake missing required fields ----
    def test_intake_incomplete_fires_before_subagents(self):
        intake = {"caller_text": "someone fell", "incident_id": "inc-test-incomp"}
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        idir, packet, audit, events = self._load_outputs(res)
        self.assertIn("Required intake field(s) missing", packet)
        self._assert_common(packet, audit, events, "intake_incomplete", ("UNABLE",))
        # none of the subagents should have fired
        for actor in ("records", "locator", "site"):
            self.assertFalse(any(e.get("actor") == actor for e in events),
                             f"{actor} subagent fired on intake_incomplete")
        # one trace event per missing field
        missing_logs = [e for e in events
                        if (e.get("tool") or "").startswith("intake_missing:")]
        self.assertGreaterEqual(len(missing_logs), 1)

    # ---- 6. dispatcher rejects all candidates ----
    def test_exhausted_candidates_when_dispatcher_rejects_twice(self):
        intake = {"address": "1200 Smith St, Houston, TX 77002",
                  "building_id": "bayou-medical-plaza",
                  "caller_photo_path": "fixtures/building_bayou/pano/suite210_interior.jpg",
                  "caller_text": "need help"}
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True,
                                        simulate_rejection_all=True)
        _, packet, audit, events = self._load_outputs(res)
        self.assertIn("Dispatcher rejected the top candidate twice", packet)
        self._assert_common(packet, audit, events, "exhausted_candidates", ("UNABLE",))
        # Confirm one replan happened
        self.assertTrue(any(e.get("tool") == "replan_after_reject" for e in events),
                        "trace missing commander/replan_after_reject")

    # ---- 7. Vexcel unavailable composed with another failure ----
    def test_degraded_locator_still_clean_when_vexcel_also_falls_back(self):
        intake = {"address": "1200 Smith St, Houston, TX 77002",
                  "building_id": "no_twin",   # drives matterport_unavailable
                  "caller_photo_path": "fixtures/building_bayou/pano/suite210_interior.jpg",
                  "caller_text": "need help"}
        # Force vexcel into the fallback path too
        with mock.patch.object(vexcel, "fetch_aerial",
                               side_effect=vexcel.VexcelUnavailable("test-forced")):
            res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        _, packet, audit, events = self._load_outputs(res)
        self.assertEqual(audit["supported"], audit["checked"],
                         "audit must stay clean even with layered failures")
        self.assertIn(audit.get("status"), ("DEGRADED", "UNABLE"))
        self.assertTrue(_has_failure_mode(events, "matterport_unavailable"))
        # Vexcel fallback trace: it logs under site/vexcel_fallback inside detect_site_features.
        self.assertTrue(any("vexcel_fallback" in (e.get("tool") or "") for e in events),
                        "vexcel fallback trace missing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
