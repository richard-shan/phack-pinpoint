"""Evidence court wiring: when it convenes, and what a ruling changes on the incident."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from live import court_bridge

VERDICT = {"winner": "B", "node_id": "n-b", "label": "Hallway, Floor 1", "confidence": 0.75,
           "decisive_detail": "a large '1' on the directory sign", "ruling": "Floor 1 hallway.",
           "cases": [{"candidate": "A", "label": "Atrium", "argument": "x", "weaknesses": "y", "strength": 3},
                     {"candidate": "B", "label": "Hallway, Floor 1", "argument": "x", "weaknesses": "y", "strength": 8}],
           "models": {"judge": "claude-opus-5-5"}, "seconds": {"evidence": 5, "deliberation": 20}, "scouts": 2}


class FakeConsole:
    def __init__(self):
        self.inc = {"id": "inc-x", "location": "Floor 1, Hallway. Confidence 58%.", "messages": [],
                    "spatialRoute": {"destination": "n-a"}}
        self.routes, self.updates = [], []

    def get(self, cid):
        return self.inc

    def has_scan(self, building):
        return True

    def route(self, cid, body):
        self.routes.append(body)

    def update(self, cid, **fields):
        self.updates.append(fields)


class Court(unittest.TestCase):
    def test_confident_match_does_not_convene(self):
        console = FakeConsole()
        console.inc["location"] = "Floor 2, Hallway. Confidence 97%."
        with patch.object(court_bridge, "_python", return_value="/bin/true"):
            case, why = court_bridge.build_case(console, "inc-x", "princeton-rabinowitz")
        self.assertIsNone(case)
        self.assertIn("97%", why)

    def test_ruling_reroutes_and_is_recorded(self):
        console = FakeConsole()
        case = {"candidates": [{}, {}]}
        done = SimpleNamespace(stdout=json.dumps(VERDICT), stderr="")
        with patch.object(court_bridge, "_python", return_value="/bin/true"), \
             patch.object(court_bridge, "build_case", return_value=(case, "/tmp/pinpoint-court-test")), \
             patch("pathlib.Path.write_text"), patch("subprocess.run", return_value=done), \
             patch("env.console.route_update", return_value={"destination": "n-b", "reason": ""}):
            got = court_bridge.hold_court(console, "inc-x", "princeton-rabinowitz", log=lambda s: None)
        self.assertEqual(got["node_id"], "n-b")
        (route,) = console.routes
        self.assertIn("evidence court", route["reason"])
        (update,) = console.updates
        self.assertEqual(update["court"]["winner"], "B")
        self.assertTrue(update["court"]["overruled"])
        self.assertEqual(update["location"], "Hallway, Floor 1 · Evidence court 75%")

    def test_agreement_changes_nothing_visible(self):
        console = FakeConsole()
        console.inc["spatialRoute"]["destination"] = "n-b"          # the court picks the same spot
        done = SimpleNamespace(stdout=json.dumps(VERDICT), stderr="")
        with patch.object(court_bridge, "_python", return_value="/bin/true"), \
             patch.object(court_bridge, "build_case", return_value=({"candidates": [{"node_id": "n-b"}, {}]}, "/tmp/pinpoint-court-test")), \
             patch("pathlib.Path.write_text"), patch("subprocess.run", return_value=done):
            court_bridge.hold_court(console, "inc-x", "b", log=lambda s: None)
        self.assertEqual(console.routes, [])                         # no reroute
        (update,) = console.updates
        self.assertEqual(set(update), {"updatedBy", "court"})         # location untouched
        self.assertFalse(update["court"]["overruled"])

    def test_abstention_changes_nothing(self):
        console = FakeConsole()
        done = SimpleNamespace(stdout=json.dumps({"abstained": "RuntimeError: no verdict"}), stderr="")
        with patch.object(court_bridge, "_python", return_value="/bin/true"), \
             patch.object(court_bridge, "build_case", return_value=({"candidates": [{}, {}]}, "/tmp/pinpoint-court-test")), \
             patch("pathlib.Path.write_text"), patch("subprocess.run", return_value=done):
            self.assertIsNone(court_bridge.hold_court(console, "inc-x", "b", log=lambda s: None))
        self.assertEqual((console.routes, console.updates), ([], []))


if __name__ == "__main__":
    unittest.main()
