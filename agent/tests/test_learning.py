"""Tests for the AGENTS.md learning loop. Stdlib unittest only (pytest not on this box).
Run with: python3 agent/tests/test_learning.py from the repo root."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(ROOT))

from pinpoint import learning  # noqa: E402
from pinpoint.config import BUILDINGS, INCIDENTS, FIXTURES  # noqa: E402

BUILDING_ID = "bayou-medical-plaza"
INTAKE = FIXTURES / "intake_sample.json"


def _clean_building():
    bdir = BUILDINGS / BUILDING_ID
    if bdir.exists():
        shutil.rmtree(bdir)


def _clean_incidents():
    if INCIDENTS.exists():
        shutil.rmtree(INCIDENTS)


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        _clean_building()

    def test_append_then_read(self):
        """One append followed by read returns one entry with fields populated."""
        path = learning.append_correction(
            building_id=BUILDING_ID,
            rejected_room_id={"node_id": "n1", "room_label": "Suite 210"},
            corrected_room_id="Suite 212",
            caller_text="we're at the dialysis place",
            timestamp_iso="2026-11-14T18:00:00Z",
        )
        self.assertTrue(path.exists())
        self.assertTrue(path.read_text().startswith(f"# Dispatcher corrections for {BUILDING_ID}"))

        corr = learning.read_corrections(BUILDING_ID)
        self.assertEqual(len(corr), 1)
        self.assertEqual(corr[0]["timestamp"], "2026-11-14T18:00:00Z")
        self.assertEqual(corr[0]["rejected"], "Suite 210")
        self.assertEqual(corr[0]["corrected"], "Suite 212")
        self.assertIn("dialysis", corr[0]["caller_text"])

    def test_multiple_appends_newest_first(self):
        """Multiple appends come back with most-recent first."""
        learning.append_correction(BUILDING_ID, "r_a", "r_b", "first",
                                   timestamp_iso="2026-11-14T10:00:00Z")
        learning.append_correction(BUILDING_ID, "r_c", "r_d", "second",
                                   timestamp_iso="2026-11-14T11:00:00Z")
        corr = learning.read_corrections(BUILDING_ID)
        self.assertEqual(len(corr), 2)
        self.assertEqual(corr[0]["timestamp"], "2026-11-14T11:00:00Z")
        self.assertEqual(corr[0]["corrected"], "r_d")

    def test_missing_file_is_empty_list(self):
        self.assertEqual(learning.read_corrections("not-a-building"), [])

    def test_summarize_for_packet_tags_source(self):
        corr = [{"timestamp": "2026-11-14T10:00:00Z", "lesson": "prefer A over B"}]
        lines = learning.summarize_for_packet(corr)
        self.assertEqual(len(lines), 1)
        self.assertIn("[src:corrections]", lines[0])
        self.assertIn("2026-11-14", lines[0])

    def test_summarize_for_packet_empty(self):
        self.assertEqual(learning.summarize_for_packet([]), [])


class TestLearningLoop(unittest.TestCase):
    """End-to-end: run incident with --simulate-rejection, then plain run, then check packet."""

    def setUp(self):
        _clean_building()
        _clean_incidents()

    def test_rejection_logs_then_next_run_surfaces_note(self):
        """Full loop: first run rejected with correction -> AGENTS.md entry -> second run's
        packet.md carries a 'Dispatcher notes for this building' section, and the audit marks
        the surfaced claim as sourced."""
        # Pass 1: simulate rejection with a corrected room
        r1 = subprocess.run(
            [sys.executable, str(ROOT / "run_incident.py"),
             "--intake", str(INTAKE),
             "--simulate-rejection", "Suite 212 (dialysis)"],
            capture_output=True, text=True, cwd=str(ROOT.parent),
        )
        self.assertEqual(r1.returncode, 0, f"stderr: {r1.stderr}")
        agents_md = learning.agents_md_path(BUILDING_ID)
        self.assertTrue(agents_md.exists(), "AGENTS.md should exist after simulated rejection")
        text1 = agents_md.read_text()
        self.assertIn("Suite 212 (dialysis)", text1)

        # Pass 2: plain run, same building
        r2 = subprocess.run(
            [sys.executable, str(ROOT / "run_incident.py"),
             "--intake", str(INTAKE)],
            capture_output=True, text=True, cwd=str(ROOT.parent),
        )
        self.assertEqual(r2.returncode, 0, f"stderr: {r2.stderr}")

        # Find the latest incident dir
        incs = sorted(INCIDENTS.glob("inc-*"), key=lambda p: p.stat().st_mtime)
        self.assertGreaterEqual(len(incs), 2)
        latest = incs[-1]

        packet = (latest / "packet.md").read_text()
        self.assertIn("## Dispatcher notes for this building", packet,
                      "packet.md should carry the Dispatcher notes section")
        self.assertIn("Suite 212 (dialysis)", packet,
                      "packet.md should surface the specific correction")
        self.assertIn("[src:corrections]", packet,
                      "the surfaced claim should carry the source tag")

        # Verify the audit marks every claim sourced
        audit = json.loads((latest / "audit.json").read_text())
        self.assertEqual(len(audit["unsupported"]), 0,
                         f"unsupported claims after surfacing correction: {audit['unsupported']}")
        self.assertGreaterEqual(audit["supported"], audit["checked"])

        # Confirm the trace recorded the read_corrections event
        trace_lines = (latest / "trace.jsonl").read_text().splitlines()
        self.assertTrue(any("read_corrections" in ln for ln in trace_lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
