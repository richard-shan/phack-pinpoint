"""Graph building pieces that need no network: geometry, spanner, overrides."""
import math
import unittest

from env import build_graph as bg
from env.graph import Graph
from env.toy import toy_graph


def wall(a, b, openings=(), kind="wall", wid="w"):
    length = math.dist(a, b)
    return {"id": wid, "type": kind, "a": a, "b": b, "length": length,
            "openings": [dict(o) for o in openings]}


class Geometry(unittest.TestCase):
    def test_link_through_solid_wall_is_dropped(self):
        a = {"id": "a", "floor_seq": 0, "floor_xyz": [0, 0, 0]}
        b = {"id": "b", "floor_seq": 0, "floor_xyz": [4, 0, 0]}
        walls = {0: [wall((2, -3), (2, 3))]}
        self.assertIsNone(bg.classify_link(a, b, walls))

    def test_link_through_a_doorway_becomes_a_door_edge_with_its_width(self):
        a = {"id": "a", "floor_seq": 0, "floor_xyz": [0, 0, 0]}
        b = {"id": "b", "floor_seq": 0, "floor_xyz": [4, 0, 0]}
        door = {"type": "doorway", "center_m": 3.0, "width": 0.9}
        e = bg.classify_link(a, b, {0: [wall((2, -3), (2, 3), [door])]})
        self.assertEqual((e["type"], e["width_m"]), ("door", 0.9))
        window = {"type": "window", "center_m": 3.0, "width": 1.5}
        self.assertIsNone(bg.classify_link(a, b, {0: [wall((2, -3), (2, 3), [window])]}))
        # an "invisible" wall is only a room divider on the plan
        self.assertEqual(bg.classify_link(a, b, {0: [wall((2, -3), (2, 3), kind="invisible"),
                                                     wall((9, -3), (9, 3))]})["type"], "open")

    def test_rise_and_run_decide_what_is_a_stair(self):
        low = {"id": "a", "floor_seq": 0, "floor_xyz": [0, 0, 0]}
        step = {"id": "b", "floor_seq": 0, "floor_xyz": [1.5, 0, 0.9]}
        across_atrium = {"id": "c", "floor_seq": 1, "floor_xyz": [12, 0, 3.4]}
        straight_down = {"id": "d", "floor_seq": 1, "floor_xyz": [0.5, 0, 3.4]}
        e = bg.classify_link(low, step, {})
        self.assertEqual((e["type"], e["rise_m"]), ("stairs", 0.9))
        self.assertIsNone(bg.classify_link(low, across_atrium, {}))
        self.assertIsNone(bg.classify_link(low, straight_down, {}))

    def test_link_across_a_void_is_measured(self):
        import numpy as np
        alpha = np.ones((100, 100), dtype=bool)          # 10 px per meter: a 10 m square of floor
        alpha[30:70, 40:60] = False                       # with a 2 m wide void down the middle
        mask = {"alpha": alpha, "ppm": 10.0, "ox": 0.0, "oy": 0.0, "h": 100}
        across = bg.off_floor_run_m(mask, (1.0, 5.0, 0), (9.0, 5.0, 0))
        self.assertAlmostEqual(across, 2.0, delta=0.6)   # the 2 px reach on each side shortens it
        self.assertGreater(across, bg.VOID_MAX_M)
        self.assertEqual(bg.off_floor_run_m(mask, (1.0, 1.0, 0), (9.0, 1.0, 0)), 0.0)   # around it

    def test_spanner_drops_the_long_shortcut_only(self):
        e = lambda a, b, m: {"a": a, "b": b, "meters": m}
        kept = bg.spanner(["a", "b", "c", "d"], [e("a", "b", 2), e("b", "c", 2), e("a", "c", 3.9),
                                                 e("c", "d", 9)])
        self.assertEqual(sorted((k["a"], k["b"]) for k in kept), [("a", "b"), ("b", "c"), ("c", "d")])

    def test_exterior_doorway_needs_open_air_on_one_side(self):
        door = {"type": "doorway", "center_m": 5.0, "width": 1.0}
        box = [wall((0, 0), (10, 0), [door], wid="south"), wall((10, 0), (10, 10), wid="east"),
               wall((10, 10), (0, 10), wid="north"), wall((0, 10), (0, 0), wid="west"),
               wall((4, 4), (6, 4), [dict(door, center_m=1.0)], wid="inner")]
        found = bg.exterior_doorways({0: box}, {"south": 1, "inner": 1})
        self.assertEqual([d["wall_id"] for d in found], ["south"])
        self.assertEqual(found[0]["outward"], (0.0, -1.0))


class Overrides(unittest.TestCase):
    def test_node_placed_by_sweep_heading_and_distance(self):
        g = toy_graph()
        bg.apply_overrides(g, {"add_nodes": [
            {"id": "door_roof", "near": "f2_mid", "heading_deg": 90, "distance_m": 4.0,
             "edge_type": "door", "is_entrance": True, "label": "the roof door",
             "approach": {"via": "stairs", "floors": 1}}]})
        node = next(n for n in g["nodes"] if n["id"] == "door_roof")
        self.assertEqual(node["xyz"], [27.5, 14.0, 4.0])
        self.assertEqual((node["floor"], node["kind"], node["attributes"]["seen_from"]),
                         (1, "entrance", "f2_mid"))
        edge = Graph(g).edge("f2_mid", "door_roof")
        self.assertEqual((edge["type"], edge["meters"]), ("door", 4.0))

    def test_elevator_rides_pay_the_wait_once(self):
        g = toy_graph()
        for n in g["nodes"]:
            if n["id"] == "f2_west":
                third = dict(n, id="f3_west", floor=2, xyz=[5, 10, 8.0])
        g["nodes"].append(third)
        bg.apply_overrides(g, {"elevators": [{"id": "lift", "offset_xy": [0, -2],
                                              "stops": {"0": "lobby", "1": "f2_west", "2": "f3_west"}}]})
        graph = Graph(g)
        ride = graph.edge("lift_f0", "lift_f2")
        self.assertEqual(ride["floors"], 2)
        self.assertAlmostEqual(graph.cost(ride, "foot"), 30 + 2 * 5)
        from env import routing
        r = routing.route(graph, "lobby", "f3_west", "foot")
        self.assertEqual(r["path"], ["lobby", "lift_f0", "lift_f2", "f3_west"])

    def test_lane_b_facts_merge_and_hand_edits_win(self):
        g = toy_graph()
        unknown = bg.apply_attributes(g["nodes"], {
            "lobby": {"room_label": "Main lobby", "attributes": {"window": True, "signage": ["Suite 100"]},
                      "equipment": ["aed", {"type": "extinguisher", "evidence_path": "e.jpg"}]},
            "no_such_node": {"room_label": "x"}})
        self.assertEqual(unknown, ["no_such_node"])
        bg.apply_overrides(g, {"nodes": {"lobby": {"attributes": {"window": False}, "label": "the front lobby"}}})
        lobby = next(n for n in g["nodes"] if n["id"] == "lobby")
        self.assertEqual(lobby["room_label"], "Main lobby")
        self.assertEqual(lobby["attributes"], {"window": False, "signage": ["Suite 100"]})
        self.assertEqual(lobby["equipment"], ["aed", "alarm_panel", {"type": "extinguisher", "evidence_path": "e.jpg"}])
        from env import routing
        hit = routing.find_equipment(Graph(g), "extinguisher", "lobby")[0]
        self.assertEqual((hit["node_id"], hit["evidence_path"]), ("lobby", "e.jpg"))

    def test_lane_b_records_convert_to_node_patches(self):
        from env.import_attributes import convert
        g = dict(toy_graph(), matterport_model_id="MODEL")
        records = {
            "MODEL/lobby": {"room_type": "lobby", "furnished": True, "has_window": True, "floor_surface": "tile",
                            "signage_text": ["LOBBY"], "door_count": 2, "stairs_visible": False,
                            "elevator_visible": True, "landmark": "the marble reception desk", "confidence": "high",
                            "equipment": [{"type": "aed", "view": "left", "note": "on the wall", "evidence_path": "c.jpg"}],
                            "hazards": [{"type": "obstruction_in_path", "view": "ahead", "note": "planter", "evidence_path": "h.jpg"}]},
            "OTHER/abc": {"room_type": "office"},
            "MODEL/gone": {"room_type": "office"}}
        patches, counts = convert(records, g)
        self.assertEqual(counts, {"imported": 1, "other_scan": 1, "not_in_graph": 1})
        self.assertEqual(patches["lobby"]["label"], "the marble reception desk")
        self.assertEqual(patches["lobby"]["room_label"], "Lobby")
        bg.apply_attributes(g["nodes"], patches)
        graph = Graph(g)
        from env import routing
        self.assertEqual(routing.hazards_along(graph, ["lobby", "c1"])[0]["type"], "obstruction_in_path")
        hit = routing.find_equipment(graph, "aed", "c1")[0]
        self.assertEqual((hit["node_id"], hit["evidence_path"], hit["note"]), ("lobby", "c.jpg", "on the wall"))
        self.assertEqual(graph.nodes["lobby"]["attributes"]["elevator_visible"], True)

    def test_unknown_ids_are_refused(self):
        with self.assertRaises(KeyError):
            bg.apply_overrides(toy_graph(), {"nodes": {"no_such_node": {"label": "x"}}})
        with self.assertRaises(KeyError):
            bg.apply_overrides(toy_graph(), {"add_nodes": [{"id": "d", "near": "no_such_sweep",
                                                           "heading_deg": 0, "distance_m": 1}]})


if __name__ == "__main__":
    unittest.main()
