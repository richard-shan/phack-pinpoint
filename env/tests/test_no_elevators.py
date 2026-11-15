"""Routes never go through an elevator: stairs on foot, carry point for a stretcher."""
import unittest

from env.config import ROUTING
from env.graph import Graph
from env import routing


class NoElevators(unittest.TestCase):
    def setUp(self):
        self.was = ROUTING["avoid_elevators"]
        ROUTING["avoid_elevators"] = True

    def tearDown(self):
        ROUTING["avoid_elevators"] = self.was

    def test_upper_floors_by_stairs_only(self):
        for building in ("princeton-rabinowitz", "259587"):          # the scans that have elevators
            g = Graph.load(building)
            assert any(e.get("type") == "elevator" for e in g.edges.values())
            panos = [n for n in g.nodes.values() if g.kind(n["id"]) == "pano"]
            top = max(panos, key=lambda n: n.get("floor", 0))
            foot = routing.best_entry(g, top["id"], "foot")
            self.assertTrue(foot["entrance_id"], building)
            self.assertFalse(foot["uses_elevator"], building)
            self.assertTrue(foot["uses_stairs"], building)
            stretcher = routing.best_entry(g, top["id"], "stretcher")
            self.assertFalse(stretcher.get("uses_elevator"), building)

    def test_elevator_edges_cost_nothing_usable(self):
        g = Graph.load("259587")
        for e in g.edges.values():
            if e.get("type") == "elevator":
                self.assertIsNone(g.cost(e, "foot"))
                self.assertIsNone(g.cost(e, "stretcher"))


if __name__ == "__main__":
    unittest.main()
