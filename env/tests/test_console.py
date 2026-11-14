"""The export for lane D's console, run against a temporary copy of the toy building."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from env import config, console, tools
from env.graph import Graph
from env.toy import toy_graph

PLANNER = config.ROOT / "app" / "src" / "live-route-model.js"     # lane D's own planner, once merged


def _node(i, xyz, floor=0, kind="pano", **more):
    return {"id": i, "xyz": xyz, "floor": floor, "kind": kind, **more}


class ConsoleExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = config.BUILDINGS
        config.BUILDINGS = Path(self.tmp.name)
        (config.BUILDINGS / "toy").mkdir()
        (config.BUILDINGS / "toy" / "graph.json").write_text(json.dumps(toy_graph()))

    def tearDown(self):
        config.BUILDINGS = self.old
        self.tmp.cleanup()

    def test_scan_graph_shape(self):
        scan = console.scan_graph("toy")
        g = Graph.load("toy")
        ids = {n["id"] for n in scan["nodes"]}
        self.assertEqual(ids, {n for n in g.nodes if g.kind(n) == "pano"})
        for e in scan["edges"]:
            self.assertIn(e["a"], ids)
            self.assertIn(e["b"], ids)
            self.assertIn(e["kind"], ("walk", "stairs", "elevator"))
        self.assertEqual(len({e["id"] for e in scan["edges"]}), len(scan["edges"]))
        self.assertEqual({e["id"] for e in scan["entries"]}, {n["id"] for n in g.entrances()})
        for e in scan["entries"]:
            self.assertIn(e["nodeId"], ids)
            self.assertTrue(e["approach"])
            self.assertEqual({"lat", "lng"}, set(e["approach"][-1]))
        self.assertIn(scan["defaultDestination"], ids)
        self.assertEqual({f["sequence"] for f in scan["floors"]}, {n["floor"] for n in scan["nodes"]})

    def test_positions_use_viewer_axes(self):
        scan = console.scan_graph("toy")
        g = Graph.load("toy")
        n = scan["nodes"][0]
        x, y, z = g.xyz(n["id"])
        self.assertEqual(n["position"], {"x": round(x, 3), "y": round(z, 3), "z": round(-y, 3)})

    def test_first_entry_is_best_door_for_default_destination(self):
        scan = console.scan_graph("toy", "room_210")
        self.assertEqual(scan["defaultDestination"], "room_210")
        self.assertEqual(scan["entries"][0]["id"], tools.best_entry("toy", "room_210", "foot")["entrance_id"])

    def test_every_route_passes_the_console_rules(self):
        scan = console.scan_graph("toy")
        posted = 0
        for profile in config.PROFILES:
            for n in scan["nodes"]:
                body = console.route_update("toy", n["id"], profile, scan=scan)
                if body.get("reachable") is False:
                    self.assertFalse(tools.best_entry("toy", n["id"], profile).get("reachable"))
                    continue
                self.assertIsNone(console.check_path(scan, body), (profile, n["id"]))
                self.assertEqual(body["entryId"], tools.best_entry("toy", n["id"], profile)["entrance_id"])
                self.assertLessEqual(len(body["reason"]), 500)
                posted += 1
        self.assertGreater(posted, len(scan["nodes"]))

    def test_blocked_edge_is_passed_on_and_avoided(self):
        scan = console.scan_graph("toy")
        open_route = console.route_update("toy", "room_210", "foot", scan=scan)
        a, b = open_route["nodeIds"][1:3]
        body = console.route_update("toy", "room_210", "foot", blocked_edges=[[a, b]], scan=scan)
        self.assertTrue(body.get("blockedEdges"))
        self.assertIsNone(console.check_path(scan, body))
        self.assertNotIn((a, b), list(zip(body["nodeIds"], body["nodeIds"][1:])))

    def test_tools_wrappers(self):
        self.assertIn("nodes", tools.console_scan("toy"))
        self.assertIn("nodeIds", tools.console_route("toy", "room_210", "stretcher"))
        self.assertIn("error", tools.console_scan("no_such_building"))
        self.assertFalse(tools.console_route("toy", "door_front")["reachable"])

    def test_elevator_cars_fold_into_one_edge(self):
        data = {"building_id": "lift", "nodes": [
            _node("a", [0, 0, 0]), _node("b", [0, 0, 3.5], floor=1),
            _node("car0", [2, 0, 0], kind="elevator"), _node("car1", [2, 0, 3.5], floor=1, kind="elevator"),
            _node("door", [-2, 0, 0], kind="entrance", is_entrance=True)],
            "edges": [{"a": "a", "b": "car0", "type": "open", "meters": 2.0},
                      {"a": "car0", "b": "car1", "type": "elevator", "meters": 3.5},
                      {"a": "car1", "b": "b", "type": "open", "meters": 2.0},
                      {"a": "door", "b": "a", "type": "door", "meters": 2.0}]}
        (config.BUILDINGS / "lift").mkdir()
        (config.BUILDINGS / "lift" / "graph.json").write_text(json.dumps(data))
        scan = console.scan_graph("lift", "b")
        self.assertEqual({n["id"] for n in scan["nodes"]}, {"a", "b"})
        (edge,) = scan["edges"]
        self.assertEqual(edge["kind"], "elevator")
        self.assertEqual(len(edge["points"]), 4)
        body = console.route_update("lift", "b", "stretcher", scan=scan)
        self.assertEqual(body["nodeIds"], ["a", "b"])
        self.assertIsNone(console.check_path(scan, body))

    @unittest.skipUnless(PLANNER.exists() and shutil.which("node"), "lane D's console is not in this checkout")
    def test_console_planner_accepts_the_routes(self):
        scan = console.scan_graph("toy")
        bodies = [b for p in config.PROFILES for n in scan["nodes"]
                  if (b := console.route_update("toy", n["id"], p, scan=scan)).get("reachable") is not False]
        script = ("import {planRoute} from %s;"
                  "const {scan,bodies}=JSON.parse(await new Promise(r=>{let s='';process.stdin.on('data',d=>s+=d).on('end',()=>r(s))}));"
                  "for(const b of bodies){const r=planRoute(scan,b);if(r.status!=='ready')throw new Error(b.destination)}"
                  "console.log(bodies.length)") % json.dumps(PLANNER.as_uri())
        done = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps({"scan": scan, "bodies": bodies}),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        self.assertEqual(done.stdout.strip(), str(len(bodies)))


if __name__ == "__main__":
    unittest.main()
