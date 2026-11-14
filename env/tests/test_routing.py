"""Routing against the toy building, whose answers are worked out by hand below.

    python -m unittest discover -s env/tests -t .

Expected times are built from the config constants and the listed segments, and every
route is also checked against a brute-force search over all simple paths.
"""
import unittest

from env import routing
from env.config import ROUTING as R
from env.costs import edge_seconds
from env.graph import Graph
from env.toy import toy_graph


def walk(m, p):
    return m / R["walk_mps"][p]


def door(m, p):
    return walk(m, p) + R["door_delay_s"][p]


def drive(m):
    return m / R["vehicle_mps"]


def stairs(p):
    return R["stairs_s_per_floor"][p]


def elevator(p):
    return R["elevator_wait_s"] + R["elevator_s_per_floor"] + R["elevator_load_s"][p]


# Hand-worked foot times.
F = "foot"
OUT_FRONT = drive(19) + walk(6, F)
OUT_DOCK = drive(100) + walk(6, F)
# dock door, east corridor, Stair B, upstairs east corridor, Suite 210
IN_DOCK_210 = door(5, F) + walk(5, F) + stairs(F) + walk(5, F) + door(5, F)
# front door, lobby, three corridor runs, Stair B, Suite 210
IN_FRONT_210 = door(5, F) + walk(45, F) + walk(5, F) + stairs(F) + walk(5, F) + door(5, F)
# front door, lobby, elevator, west corridor, Suite 204 (206 is the same length)
IN_FRONT_204 = door(5, F) + walk(5, F) + elevator(F) + walk(5, F) + door(5, F)
HOP_204_210 = door(5, F) + walk(45, F) + door(5, F)
HOP_204_206 = door(5, F) + door(5, F)


def brute_force(data, src, dst, profile):
    """Least seconds over every simple path, computed from the raw edge list."""
    kinds = {n["id"]: n.get("kind") for n in data["nodes"]}
    driving = kinds[src] in ("arrival", "staging")    # on foot, nobody gets back in the rig
    adj = {}
    for e in data["edges"]:
        c = edge_seconds(e, profile)
        if c is not None and (driving or e["type"] != "exterior_vehicle"):
            adj.setdefault(e["a"], []).append((e["b"], c))
            adj.setdefault(e["b"], []).append((e["a"], c))
    best = [None]

    def dfs(u, cost, seen):
        if best[0] is not None and cost >= best[0]:
            return
        if u == dst:
            best[0] = cost
            return
        for v, c in adj.get(u, []):
            if v not in seen:
                dfs(v, cost + c, seen | {v})

    dfs(src, 0.0, {src})
    return best[0]


class RoutingOnToy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = toy_graph()
        cls.g = Graph(cls.data)

    def near(self, got, want, tol=0.11):
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, want, delta=tol)

    # ---------------------------------------------------------------- costs
    def test_edge_costs(self):
        g = self.g
        self.near(g.cost(g.edge("lobby", "c1"), F), 15 / 1.4)
        self.near(g.cost(g.edge("door_front", "lobby"), "stretcher"), 5 / 1.0 + 8)
        self.near(g.cost(g.edge("c2", "door_side"), F), 10 / 1.4 + 90)
        self.assertIsNone(g.cost(g.edge("stair1", "stair2"), "stretcher"))
        self.near(g.cost(g.edge("elev1", "elev2"), "stretcher"), 30 + 5 + 10)
        self.near(g.cost(g.edge("street", "stage_dock"), F), 100 / 4.5)

    def test_narrow_door_blocks_stretcher_only(self):
        e = {"type": "door", "meters": 2.0, "width_m": 0.6}
        self.assertIsNone(edge_seconds(e, "stretcher"))
        self.assertIsNotNone(edge_seconds(e, "foot"))

    # ---------------------------------------------------------------- single pair
    def test_every_route_matches_brute_force(self):
        ids = list(self.g.nodes)
        checked = 0
        for profile in ("foot", "stretcher"):
            for a in ids:
                for b in ids:
                    if a == b:
                        continue
                    want = brute_force(self.data, a, b, profile)
                    got = routing.route(self.g, a, b, profile)
                    if want is None:
                        self.assertFalse(got["reachable"], (a, b, profile))
                    else:
                        self.assertTrue(got["reachable"], (a, b, profile))
                        self.near(got["seconds"], want)
                        self.assertEqual(got["path"][0], a)
                        self.assertEqual(got["path"][-1], b)
                    checked += 1
        self.assertEqual(checked, 2 * len(ids) * (len(ids) - 1))

    def test_fastest_is_not_shortest(self):
        fast = routing.route(self.g, "door_front", "room_210", F)
        short = routing.shortest_by_meters(self.g, "door_front", "room_210", F)
        self.assertTrue(fast["uses_stairs"] and not fast["uses_elevator"])
        self.assertTrue(short["uses_elevator"] and not short["uses_stairs"])
        self.near(fast["seconds"], IN_FRONT_210)
        self.assertLess(fast["seconds"], short["seconds"])
        self.assertGreater(fast["meters"], short["meters"])

    def test_stretcher_never_uses_stairs(self):
        r = routing.route(self.g, "door_dock", "room_210", "stretcher")
        self.assertTrue(r["uses_elevator"] and not r["uses_stairs"])

    # ---------------------------------------------------------------- best door
    def test_best_entry_on_foot_is_the_dock_not_the_front_door(self):
        r = routing.best_entry(self.g, "room_210", F)
        self.assertEqual(r["entrance_id"], "door_dock")
        self.assertEqual(r["staging_id"], "stage_dock")
        self.near(r["seconds"], OUT_DOCK + IN_DOCK_210)
        self.near(r["outside_seconds"], OUT_DOCK)
        self.near(r["inside_seconds"], IN_DOCK_210)
        self.assertEqual(r["path"], ["street", "stage_dock", "door_dock", "c3", "stair1",
                                     "stair2", "f2_east", "room_210"])
        self.assertEqual(r["runner_up"]["entrance_id"], "door_front")
        self.near(r["runner_up"]["seconds"], OUT_FRONT + IN_FRONT_210)
        self.assertEqual([x["entrance_id"] for x in r["ranked"]],
                         ["door_dock", "door_front", "door_side"])
        self.assertFalse(r["front_door"]["is_best"])
        self.near(r["front_door"]["seconds_saved"],
                  OUT_FRONT + IN_FRONT_210 - OUT_DOCK - IN_DOCK_210, tol=0.21)

    def test_best_entry_for_stretcher_is_the_front_door(self):
        r = routing.best_entry(self.g, "room_210", "stretcher")
        s = "stretcher"
        want = (drive(19) + walk(6, s) + door(5, s) + walk(5, s) + elevator(s)
                + walk(5, s) + walk(45, s) + door(5, s))
        self.assertEqual(r["entrance_id"], "door_front")
        self.assertTrue(r["uses_elevator"] and not r["uses_stairs"])
        self.near(r["seconds"], want)
        self.assertTrue(r["front_door"]["is_best"])

    def test_best_entry_agrees_with_full_graph_search(self):
        for target in ("room_210", "room_204", "room_206", "lobby", "f2_mid"):
            for profile in ("foot", "stretcher"):
                r = routing.best_entry(self.g, target, profile)
                self.near(r["seconds"], brute_force(self.data, "street", target, profile))

    def test_exit_route(self):
        self.assertEqual(routing.exit_route(self.g, "room_210", F)["entrance_id"], "door_dock")
        self.near(routing.exit_route(self.g, "room_210", F)["seconds"], IN_DOCK_210)
        self.assertEqual(routing.exit_route(self.g, "room_210", "stretcher")["entrance_id"],
                         "door_front")

    # ---------------------------------------------------------------- replanning
    def test_replan_when_the_stairwell_is_blocked(self):
        r = routing.replan(self.g, "stair1", "room_210", F, [["stair1", "stair2"]])
        self.assertTrue(r["reachable"] and r["uses_elevator"] and not r["uses_stairs"])
        self.assertEqual(r["path"][0], "stair1")
        self.near(r["seconds"], walk(5, F) + walk(45, F) + walk(5, F) + elevator(F)
                  + walk(5, F) + walk(45, F) + door(5, F))
        self.assertIn("compute_ms", r)

    def test_replan_reports_no_route(self):
        r = routing.replan(self.g, "lobby", "room_210", "stretcher", ["elev1|elev2"])
        self.assertFalse(r["reachable"])
        self.assertEqual(r["path"], [])

    def test_blocked_door_changes_the_best_entry(self):
        r = routing.best_entry(self.g, "room_210", F, blocked=[["c3", "door_dock"]])
        self.assertEqual(r["entrance_id"], "door_front")
        self.assertEqual(r["unusable_entrances"], ["door_dock"])

    def test_backup_route_takes_the_other_way_up(self):
        first = routing.route(self.g, "door_dock", "room_210", F)
        backup = routing.backup_route(self.g, "door_dock", "room_210", F)
        self.assertTrue(first["uses_stairs"])
        self.assertTrue(backup["uses_elevator"] and backup["is_different"])
        self.assertGreater(backup["seconds"], first["seconds"])
        self.assertEqual(sorted(map(tuple, backup["shared_edges"])),
                         [("c3", "door_dock"), ("f2_east", "room_210")])

    # ---------------------------------------------------------------- belief
    def test_single_candidate_equals_best_entry(self):
        b = routing.best_entry_under_belief(self.g, [{"node_id": "room_210", "p": 1.0}], F)
        e = routing.best_entry(self.g, "room_210", F)
        self.assertEqual(b["entrance_id"], e["entrance_id"])
        self.near(b["expected_seconds"], e["seconds"])
        self.near(b["value_of_information_seconds"], 0.0)
        self.assertFalse(b["ask_another_question"])

    def test_confident_belief_keeps_the_top_guess_door(self):
        b = routing.best_entry_under_belief(
            self.g, [{"node_id": "room_210", "score": 0.6}, {"node_id": "room_204", "score": 0.4}], F)
        self.assertEqual(b["entrance_id"], "door_dock")
        self.assertEqual(b["visit_order"], ["room_210", "room_204"])
        self.near(b["expected_seconds"], OUT_DOCK + IN_DOCK_210 + 0.4 * HOP_204_210)
        self.near(b["top_guess"]["seconds_lost_vs_belief"], 0.0)

    def test_split_belief_moves_the_door(self):
        cands = [{"node_id": "room_210", "p": 0.4}, {"node_id": "room_204", "p": 0.3},
                 {"node_id": "room_206", "p": 0.3}]
        b = routing.best_entry_under_belief(self.g, cands, F)
        # Two of the three candidates are by the front door, so go there first.
        self.assertEqual(b["entrance_id"], "door_front")
        self.assertEqual(b["visit_order"][-1], "room_210")
        t204 = OUT_FRONT + IN_FRONT_204
        want = 0.3 * t204 + 0.3 * (t204 + HOP_204_206) + 0.4 * (t204 + HOP_204_206 + HOP_204_210)
        self.near(b["expected_seconds"], want)
        # Baseline: dock door for the top guess, then the other two.
        t210 = OUT_DOCK + IN_DOCK_210
        base = 0.4 * t210 + 0.3 * (t210 + HOP_204_210) + 0.3 * (t210 + HOP_204_210 + HOP_204_206)
        self.assertEqual(b["top_guess"]["entrance_id"], "door_dock")
        self.near(b["top_guess"]["expected_seconds"], base)
        self.near(b["top_guess"]["seconds_lost_vs_belief"], base - want, tol=0.21)
        known = 0.4 * t210 + 0.6 * t204
        self.near(b["value_of_information_seconds"], want - known, tol=0.21)
        self.assertTrue(b["ask_another_question"])

    def test_belief_is_never_worse_than_the_top_guess(self):
        import itertools
        rooms = ["room_210", "room_204", "room_206", "lobby", "f2_mid"]
        for a, b_ in itertools.combinations(rooms, 2):
            for p in (0.5, 0.7, 0.9):
                for profile in ("foot", "stretcher"):
                    r = routing.best_entry_under_belief(
                        self.g, [{"node_id": a, "p": p}, {"node_id": b_, "p": 1 - p}], profile)
                    self.assertGreaterEqual(r["top_guess"]["seconds_lost_vs_belief"], -0.11)
                    self.assertGreaterEqual(r["value_of_information_seconds"], -0.11)

    # ---------------------------------------------------------------- directions
    def test_directions_for_the_foot_crew(self):
        path = routing.best_entry(self.g, "room_210", F)["path"]
        steps = routing.directions(self.g, path)
        self.assertEqual([s["kind"] for s in steps],
                         ["drive", "go", "go", "turn", "stairs", "go", "arrive"])
        text = [s["text"] for s in steps]
        self.assertIn("loading dock apron", text[0])
        self.assertIn("Walk 6 m to the loading dock door", text[1])
        self.assertTrue(text[2].startswith("Enter through the loading dock door, go 5 m"))
        self.assertTrue(text[3].startswith("Turn right at the east corridor"))
        self.assertEqual(text[4], "Take Stair B up to floor 2.")
        self.assertIn("go 10 m through the door", text[5])
        self.assertEqual(text[6], "Arrive at Suite 210.")
        self.near(sum(s["meters"] for s in steps), routing.summarize(self.g, path, F)["meters"])
        self.assertEqual(steps[3]["node_id"], "c3")
        self.assertEqual(steps[3]["heading_deg"], 90.0)      # heading north, toward Stair B

    def test_left_and_right_come_from_geometry(self):
        # Heading east from the front door, the elevator is to the north: a left turn.
        steps = routing.directions(self.g, ["door_front", "lobby", "elev1"])
        self.assertTrue(steps[1]["text"].startswith("Turn left at the lobby"))
        # Coming back west along the corridor, the same elevator is on the right.
        steps = routing.directions(self.g, ["c1", "lobby", "elev1"])
        self.assertTrue(steps[1]["text"].startswith("Turn right at the lobby"))

    def test_straight_runs_merge(self):
        steps = routing.directions(self.g, ["lobby", "c1", "c2", "c3", "door_dock"])
        self.assertEqual([s["kind"] for s in steps], ["go", "arrive"])
        self.near(steps[0]["meters"], 50.0)

    def test_elevator_directions_going_down(self):
        steps = routing.directions(self.g, ["room_204", "f2_west", "elev2", "elev1", "lobby"])
        self.assertIn("Take the lobby elevator down to floor 1.", [s["text"] for s in steps])

    # ---------------------------------------------------------------- unscanned approach
    def test_approach_adds_the_unscanned_leg(self):
        data = toy_graph()
        doors = {n["id"]: n for n in data["nodes"] if n["is_entrance"]}
        doors["door_dock"]["approach"] = {"via": "elevator", "floors": 13, "walk_m": 14}
        doors["door_front"]["approach"] = {"via": "stairs", "floors": 13}
        doors["door_side"]["approach"] = {"via": "stairs", "floors": 13}
        g = Graph(data)
        base = routing.best_entry(self.g, "room_210", F)
        r = routing.best_entry(g, "room_210", F)
        self.assertEqual(r["entrance_id"], "door_dock")
        self.near(r["seconds"], base["seconds"] + walk(14, F) + 30 + 13 * 5)
        self.near(r["scanned_seconds"], base["seconds"])
        front = next(x for x in r["ranked"] if x["entrance_id"] == "door_front")
        self.near(front["outside_seconds"], OUT_FRONT + 13 * stairs(F))
        # nobody carries a stretcher up thirteen floors: that door drops out for the stretcher
        s = routing.best_entry(g, "room_210", "stretcher")
        self.assertNotIn("door_front", [x["entrance_id"] for x in s["ranked"]])
        self.assertEqual(s["entrance_id"], "door_dock")
        steps = routing.directions(g, r["path"][r["path"].index("door_dock"):])
        self.assertEqual(steps[0]["kind"], "approach")
        self.assertIn("Take the building elevator", steps[0]["text"])
        self.assertTrue(steps[1]["text"].startswith("Enter through the loading dock door"))

    def test_locked_entry_slows_the_way_in_but_not_the_way_out(self):
        data = toy_graph()
        dock = next(n for n in data["nodes"] if n["id"] == "door_dock")
        dock["approach"] = {"locked_entry": True, "note": "panic bar: exit only"}
        g = Graph(data)
        r = routing.best_entry(g, "room_210", F)
        self.assertEqual(r["entrance_id"], "door_front")          # 90 s makes the dock lose
        dock_row = next(x for x in r["ranked"] if x["entrance_id"] == "door_dock")
        self.near(dock_row["seconds"], OUT_DOCK + 90 + IN_DOCK_210)
        out = routing.exit_route(g, "room_210", F)                # still the fastest way out
        self.assertEqual(out["entrance_id"], "door_dock")
        self.near(out["seconds"], IN_DOCK_210)
        steps = routing.directions(g, ["door_dock", "c3", "stair1"])
        self.assertIn("expect it locked", steps[0]["text"])
        self.assertNotEqual(steps[0]["kind"], "approach")

    # ---------------------------------------------------------------- node lookups
    def test_find_equipment(self):
        hits = routing.find_equipment(self.g, "AED", "room_210")
        self.assertEqual([h["node_id"] for h in hits], ["lobby"])
        self.assertGreater(hits[0]["seconds"], 0)
        self.assertEqual(routing.find_equipment(self.g, "defibrillator", "room_210"), [])

    def test_hazards_along_flags_the_locked_door(self):
        path = routing.route(self.g, "door_side", "c3", F)["path"]
        self.assertEqual([h["type"] for h in routing.hazards_along(self.g, path)], ["locked_door"])

    def test_unknown_node_is_an_error(self):
        with self.assertRaises(KeyError):
            routing.route(self.g, "nowhere", "room_210", F)


if __name__ == "__main__":
    unittest.main()
