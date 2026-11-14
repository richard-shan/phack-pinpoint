"""The contract-facing tools, run against a temporary copy of the toy building."""
import json
import tempfile
import unittest
from pathlib import Path

from env import config, tools
from env.toy import toy_graph


class ToolsOnToy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = config.BUILDINGS
        config.BUILDINGS = Path(self.tmp.name)
        (config.BUILDINGS / "toy").mkdir()
        (config.BUILDINGS / "toy" / "graph.json").write_text(json.dumps(toy_graph()))

    def tearDown(self):
        config.BUILDINGS = self.old
        self.tmp.cleanup()

    def test_contract_shapes(self):
        r = tools.route("toy", "door_front", "room_210", "foot")
        for k in ("path", "seconds", "meters", "uses_stairs", "uses_elevator"):
            self.assertIn(k, r)
        b = tools.best_entry("toy", "room_210", "foot")
        for k in ("entrance_id", "staging_id", "seconds", "path", "runner_up"):
            self.assertIn(k, b)
        u = tools.best_entry_under_belief("toy", [{"node_id": "room_210", "score": 3},
                                                  {"node_id": "room_204", "score": 1}])
        for k in ("entrance_id", "expected_seconds", "spread_seconds"):
            self.assertIn(k, u)
        d = tools.directions("toy", b["path"])
        for k in ("text", "meters", "node_id", "thumbnail_path"):
            self.assertIn(k, d[0])
        x = tools.exit_route("toy", "room_210")
        for k in ("path", "seconds", "entrance_id"):
            self.assertIn(k, x)
        e = tools.entrances("toy")
        self.assertEqual({"entrance_id", "node_id", "side", "latlon"} - set(e[0]), set())
        self.assertEqual(len(e), 3)
        self.assertEqual([x_["entrance_id"] for x_ in e if x_["primary"]], ["door_front"])

    def test_look(self):
        r = tools.look("lobby")                       # building found from the node id
        self.assertEqual(r["building_id"], "toy")
        self.assertIsNone(r["image_path"])
        self.assertEqual(r["attributes"]["equipment"], ["aed", "alarm_panel"])
        heads = {n["node_id"]: n["heading_deg"] for n in r["neighbors"]}
        self.assertEqual(heads, {"door_front": 180.0, "elev1": 90.0, "c1": 0.0})

    def test_errors_come_back_as_data(self):
        self.assertIn("error", tools.route("toy", "nowhere", "room_210"))
        self.assertIn("error", tools.route("no_such_building", "a", "b"))
        self.assertIn("error", tools.route("toy", "lobby", "room_210", "helicopter"))
        self.assertIn("error", tools.look("nowhere"))

    def test_reported_block_persists(self):
        before = tools.best_entry("toy", "room_210", "foot")
        self.assertEqual(before["entrance_id"], "door_dock")
        r = tools.report_edge("toy", "stair2", "stair1", "blocked", note="stairwell full of smoke")
        self.assertEqual(r["passable"], {"foot": False, "stretcher": False})
        after = tools.best_entry("toy", "room_210", "foot")      # a fresh load sees the report
        self.assertEqual(after["entrance_id"], "door_front")
        self.assertTrue(after["uses_elevator"])
        over = json.loads((config.BUILDINGS / "toy" / "overrides.json").read_text())
        self.assertEqual(over["edges"][0]["note"], "stairwell full of smoke")
        tools.report_edge("toy", "stair1", "stair2", "clear")
        self.assertEqual(tools.best_entry("toy", "room_210", "foot")["entrance_id"], "door_dock")

    def test_locked_report_adds_delay_but_stays_passable(self):
        base = tools.route("toy", "door_dock", "c3", "foot")["seconds"]
        tools.report_edge("toy", "door_dock", "c3", "locked")
        locked = tools.route("toy", "door_dock", "c3", "foot")["seconds"]
        self.assertAlmostEqual(locked - base, 87.0, delta=0.2)       # 90 s locked delay replaces 3 s
        tools.report_edge("toy", "door_dock", "c3", "clear")
        self.assertAlmostEqual(tools.route("toy", "door_dock", "c3", "foot")["seconds"], base, delta=0.01)


if __name__ == "__main__":
    unittest.main()
