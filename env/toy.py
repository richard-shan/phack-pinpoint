"""A hand-made two-floor building with answers we can work out on paper.

A 55 m by 20 m building. The street address is on the west side (front door). Stair B is
at the east end next to the loading dock; the only elevator is by the front lobby. The
north side door is locked. Suite 210 (the usual target) is upstairs at the east end.

    floor 2   206 - west ---------- mid ---------- east - Stair B
                    | 204 elevator                  | Suite 210
    floor 1   front - lobby - c1 - c2 - c3 - loading dock          street is to the west
                      elevator     |    Stair B
                              north side door (locked)

    python -m env.toy          # writes buildings/toy/graph.json for the other lanes
"""
from __future__ import annotations

import json

from . import config
from .costs import fill_costs

Z2 = 4.0


def toy_graph() -> dict:
    def node(id_, x, y, z, floor, label=None, **extra):
        n = {"id": id_, "floor": floor, "xyz": [float(x), float(y), float(z)], "pano_path": None,
             "room_label": None, "attributes": {}, "is_entrance": False, "equipment": [],
             "kind": "pano"}
        if label:
            n["label"] = label
        n.update(extra)
        return n

    def edge(a, b, type_, meters, **extra):
        e = {"a": a, "b": b, "meters": float(meters), "type": type_, "confidence": 1.0,
             "evidence_path": None}
        e.update(extra)
        return e

    ent = dict(is_entrance=True, kind="entrance")
    nodes = [
        # floor 1
        node("door_front", 0, 10, 0, 0, "the front door", side="west", primary=True, **ent),
        node("lobby", 5, 10, 0, 0, "the lobby", room_label="Lobby",
             equipment=["aed", "alarm_panel"]),
        node("elev1", 5, 15, 0, 0, "the lobby elevator", attributes={"elevator": True}),
        node("c1", 20, 10, 0, 0),
        node("c2", 35, 10, 0, 0, "the side-door junction"),
        node("door_side", 35, 20, 0, 0, "the north side door", side="north", **ent),
        node("c3", 50, 10, 0, 0, "the east corridor"),
        node("stair1", 50, 15, 0, 0, "Stair B", attributes={"stairs": True}),
        node("door_dock", 55, 10, 0, 0, "the loading dock door", side="east", **ent),
        # floor 2
        node("elev2", 5, 15, Z2, 1, "the lobby elevator", attributes={"elevator": True}),
        node("f2_west", 5, 10, Z2, 1, "the west corridor"),
        node("f2_mid", 27.5, 10, Z2, 1, equipment=["fire_extinguisher"]),
        node("f2_east", 50, 10, Z2, 1, "the east corridor"),
        node("stair2", 50, 15, Z2, 1, "Stair B", attributes={"stairs": True}),
        node("room_210", 50, 5, Z2, 1, "Suite 210", room_label="Suite 210"),
        node("room_204", 5, 5, Z2, 1, "Suite 204", room_label="Suite 204"),
        node("room_206", 0, 10, Z2, 1, "Suite 206", room_label="Suite 206"),
        # outside
        node("street", -25, 10, 0, 0, "the street", kind="arrival"),
        node("stage_front", -6, 10, 0, 0, "the front curb", kind="staging"),
        node("stage_side", 35, 26, 0, 0, "the north lot", kind="staging"),
        node("stage_dock", 61, 10, 0, 0, "the loading dock apron", kind="staging"),
    ]
    edges = [
        edge("door_front", "lobby", "door", 5, width_m=1.8),
        edge("lobby", "elev1", "open", 5),
        edge("lobby", "c1", "open", 15),
        edge("c1", "c2", "open", 15),
        edge("c2", "c3", "open", 15),
        edge("c2", "door_side", "locked_door", 10, width_m=0.9),
        edge("c3", "stair1", "open", 5),
        edge("c3", "door_dock", "door", 5, width_m=2.4),
        edge("stair1", "stair2", "stairs", 8, floors=1),
        edge("elev1", "elev2", "elevator", 4, floors=1),
        edge("elev2", "f2_west", "open", 5),
        edge("f2_west", "f2_mid", "open", 22.5),
        edge("f2_mid", "f2_east", "open", 22.5),
        edge("f2_east", "stair2", "open", 5),
        edge("f2_east", "room_210", "door", 5, width_m=0.9),
        edge("f2_west", "room_204", "door", 5, width_m=0.9),
        edge("f2_west", "room_206", "door", 5, width_m=0.9),
        edge("street", "stage_front", "exterior_vehicle", 19),
        edge("street", "stage_side", "exterior_vehicle", 70),
        edge("street", "stage_dock", "exterior_vehicle", 100),   # the drive around the building
        edge("stage_front", "door_front", "exterior_foot", 6),
        edge("stage_side", "door_side", "exterior_foot", 6),
        edge("stage_dock", "door_dock", "exterior_foot", 6),
    ]
    return fill_costs({"building_id": "toy", "name": "Toy building", "source": "hand-made",
                       "floor_offset": 0, "nodes": nodes, "edges": edges})


if __name__ == "__main__":
    path = config.BUILDINGS / "toy" / "graph.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(toy_graph(), indent=1))
    print(f"wrote {path.relative_to(config.ROOT)}")
