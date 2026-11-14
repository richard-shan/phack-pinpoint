"""Edge time costs per profile. Cost is seconds, not meters.

    python -m env.costs buildings/<id>/graph.json     # recompute seconds/passable after tuning
"""
from __future__ import annotations

import json
import sys

from .config import PROFILES, ROUTING


def edge_seconds(edge: dict, profile: str, cfg: dict = ROUTING) -> float | None:
    """Seconds to cross `edge` for `profile`, from its type and length. None: cannot pass."""
    if edge.get("blocked"):
        return None                     # reported impassable by a crew or a dispatcher
    kind = edge.get("type", "open")
    meters = float(edge.get("meters") or 0.0)
    walk = meters / cfg["walk_mps"][profile]
    if kind == "open" or kind == "exterior_foot":
        return walk
    if kind in ("door", "locked_door"):
        width = edge.get("width_m")
        if width is not None and width < cfg["min_door_width_m"][profile]:
            return None
        delay = cfg["door_delay_s"] if kind == "door" else cfg["locked_delay_s"]
        return walk + delay[profile]
    if kind == "stairs":
        per_floor = cfg["stairs_s_per_floor"][profile]
        if per_floor is None:
            return None
        return per_floor * _floors(edge, cfg)
    if kind == "elevator":
        return (cfg["elevator_wait_s"] + cfg["elevator_s_per_floor"] * _floors(edge, cfg)
                + cfg["elevator_load_s"][profile])
    if kind == "exterior_vehicle":
        return meters / cfg["vehicle_mps"]
    raise ValueError(f"unknown edge type {kind!r}")


def approach_seconds(node: dict, profile: str, cfg: dict = ROUTING) -> float | None:
    """Seconds to reach an entrance from the street when that leg is not in the scan.

    A scan of an upper floor or a single suite starts at its own door. The entrance node
    then carries `approach`: {"via": "elevator" | "stairs", "floors": n, "walk_m": m}, or
    explicit {"seconds": {"foot": s, "stretcher": s}}. Returns 0 without one, and None
    when the profile cannot make the approach (a stretcher up the stairs).

    `locked_entry: true` adds the locked-door delay on the way in only: a stair door
    signed NO RE-ENTRY, an exit door with a panic bar, a door kept bolted from inside.
    Leaving through the same door costs nothing extra, which an edge type cannot express.
    """
    ap = node.get("approach")
    if not ap:
        return 0.0
    if "seconds" in ap:
        return ap["seconds"].get(profile)
    floors = abs(float(ap.get("floors") or 0))
    walk = float(ap.get("walk_m") or 0.0) / cfg["walk_mps"][profile]
    if ap.get("locked_entry"):
        walk += cfg["locked_delay_s"][profile]
    if ap.get("via") == "stairs":
        per_floor = cfg["stairs_s_per_floor"][profile]
        return None if per_floor is None else walk + per_floor * floors
    if ap.get("via") == "elevator":
        return (walk + cfg["elevator_wait_s"] + cfg["elevator_s_per_floor"] * floors
                + cfg["elevator_load_s"][profile])
    return walk


def _floors(edge: dict, cfg: dict) -> float:
    """Floors climbed on a stairs or elevator edge: explicit `floors`, else from `rise_m`, else 1."""
    if edge.get("floors") is not None:
        return abs(float(edge["floors"]))
    if edge.get("rise_m") is not None:
        return abs(float(edge["rise_m"])) / cfg["floor_height_m"]
    return 1.0


def fill_costs(graph: dict, force: bool = False, cfg: dict = ROUTING) -> dict:
    """Write `seconds{profile}` and `passable{profile}` on every edge that lacks them."""
    for e in graph["edges"]:
        if force or "seconds" not in e or "passable" not in e:
            secs = {p: edge_seconds(e, p, cfg) for p in PROFILES}
            e["seconds"] = {p: (None if s is None else round(s, 2)) for p, s in secs.items()}
            e["passable"] = {p: s is not None for p, s in secs.items()}
    return graph


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path) as f:
            g = json.load(f)
        fill_costs(g, force=True)
        with open(path, "w") as f:
            json.dump(g, f, indent=1)
        print(f"recomputed {len(g['edges'])} edges in {path}")
