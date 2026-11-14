"""Build `buildings/<building_id>/graph.json` from a Matterport scan.

    python -m env.build_graph --model mZ4TqsMaSk8 --building 10352309 [--no-geo] [--render]

What the scan gives and what we do with it:

  * Sweeps (panorama positions) become nodes.
  * Matterport's `neighbors` are line-of-sight links, not walkable steps: they can pass
    through glass, across an atrium, or between floors. So each link is tested:
      - level links must not cross a wall. Crossing at a doorway makes a `door` edge that
        carries the doorway width; crossing solid wall or a window drops the link.
        (Only scans with wall data can be tested; the others get a length cap instead.)
      - links that climb are kept as `stairs` only when rise and run look like a stair.
      - level links must stay over scanned floor. The scan's top-down floor image is
        transparent where there is no floor (an atrium, the open well of a stair), and a
        link that crosses more than VOID_MAX_M of that is a view across a void.
  * What survives is thinned to a spanner: a link is kept only if the links already kept
    do not connect its ends within SPANNER_T times its length. Long shortcuts across a
    room go; the short hops between adjacent captures stay.
  * Doorways in walls that face nothing but outside become entrance candidates.

Elevators are never linked in a scan. Add them, and anything else the scan cannot see, in
`buildings/<building_id>/overrides.json` (see `apply_overrides`).
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import time
from collections import defaultdict
from pathlib import Path

from . import config
from . import matterport as mp
from .costs import fill_costs

FLAT_DZ_M = 0.25             # a smaller rise than this is level walking (thresholds, one step)
STAIR_SLOPE = (0.20, 1.30)   # rise over run of a believable stair link
STAIR_MAX_RUN_M = 5.0
STAIR_MAX_RISE_M = 2.6
SPANNER_T = 1.25
MAX_LINK_NO_WALLS_M = 7.0    # without wall data, longer level links are not trusted
DOOR_TOL_M = 0.20            # slack on a doorway's extent along its wall
VOID_MAX_M = 1.2             # a level link may cross at most this much unscanned floor
OUTSIDE_RAY_M = 40.0
PASSABLE_OPENINGS = ("doorway", "opening")


# ------------------------------------------------------------------ geometry
def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def seg_hit(p, q, a, b):
    """Where segment p-q crosses segment a-b: (t along p-q, u along a-b), or None."""
    rx, ry = q[0] - p[0], q[1] - p[1]
    sx, sy = b[0] - a[0], b[1] - a[1]
    den = _cross(rx, ry, sx, sy)
    if abs(den) < 1e-12:
        return None
    t = _cross(a[0] - p[0], a[1] - p[1], sx, sy) / den
    u = _cross(a[0] - p[0], a[1] - p[1], rx, ry) / den
    return (t, u) if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0 else None


def wall_crossings(p, q, walls) -> list[tuple[dict, dict | None]]:
    """Physical walls crossed by p-q, each with the opening it passes through (or None)."""
    out = []
    for w in walls:
        if w["type"] != "wall":
            continue
        hit = seg_hit(p, q, w["a"], w["b"])
        if hit is None:
            continue
        along = hit[1] * w["length"]
        through = next((o for o in w["openings"]
                        if abs(along - o["center_m"]) <= o["width"] / 2 + DOOR_TOL_M), None)
        out.append((w, through))
    return out


# ------------------------------------------------------------------ floor mask
def floor_mask(model_id: str, seq: int, sweeps: dict) -> dict | None:
    """Where floor `seq` has scanned floor: the alpha channel of the scan's top-down image.

    Returns {"alpha": 2-D array, "ppm", "ox", "oy", "h"} or None when the scan has no such
    image, or when the image does not line up with the sweeps (fewer than 80% of the
    floor's sweeps stand on scanned floor), in which case the check is skipped.
    """
    import numpy as np
    from PIL import Image
    try:
        fp = mp.get_floorplan(model_id, seq, flags=("photogramy", "alpha"), fmt="png")
    except mp.MatterportError:
        fp = None
    if not fp or not fp.get("resolution") or fp["origin"][0] is None:
        return None
    img = Image.open(fp["path"])
    if "A" not in img.getbands():
        return None
    mask = {"alpha": np.asarray(img.getchannel("A")) > 16, "ppm": float(fp["resolution"]),
            "ox": float(fp["origin"][0]), "oy": float(fp["origin"][1]), "h": img.height}
    here = [s["floor_xyz"] for s in sweeps.values() if s["floor_seq"] == seq]
    on = sum(_on_floor(mask, p[0], p[1]) for p in here)
    return mask if here and on / len(here) >= 0.8 else None


def _on_floor(mask: dict, x: float, y: float, reach_px: int = 2) -> bool:
    col = int(round((x - mask["ox"]) * mask["ppm"]))
    row = int(round(mask["h"] - (y - mask["oy"]) * mask["ppm"]))
    a = mask["alpha"]
    if not (0 <= row < a.shape[0] and 0 <= col < a.shape[1]):
        return False
    return bool(a[max(0, row - reach_px):row + reach_px + 1, max(0, col - reach_px):col + reach_px + 1].any())


def off_floor_run_m(mask: dict, p, q, step_m: float = 0.05) -> float:
    """Longest unbroken stretch of the segment p-q, in meters, that is not over scanned floor."""
    length = math.dist(p[:2], q[:2])
    n = max(1, int(length / step_m))
    worst = run = 0
    for i in range(n + 1):
        t = i / n
        if _on_floor(mask, p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t):
            run = 0
        else:
            run += 1
            worst = max(worst, run)
    return worst * length / n


# ------------------------------------------------------------------ edges
def classify_link(a: dict, b: dict, walls_by_floor: dict) -> dict | None:
    """Edge for the line-of-sight link between sweeps a and b, or None to drop it."""
    pa, pb = a["floor_xyz"], b["floor_xyz"]
    run = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
    dz = pb[2] - pa[2]
    length = math.dist(pa, pb)
    same_floor = a["floor_seq"] == b["floor_seq"]
    edge = {"meters": round(length, 2), "confidence": 0.9, "evidence_path": None}

    if abs(dz) >= FLAT_DZ_M:
        slope = abs(dz) / max(run, 0.05)
        if not (STAIR_SLOPE[0] <= slope <= STAIR_SLOPE[1]) or run > STAIR_MAX_RUN_M \
                or abs(dz) > STAIR_MAX_RISE_M:
            return None                      # a view between levels, not a way between them
        if same_floor and any(t is None or t["type"] not in PASSABLE_OPENINGS for _, t in
                              wall_crossings(pa, pb, walls_by_floor.get(a["floor_seq"]) or [])):
            return None
        edge.update(type="stairs", rise_m=round(abs(dz), 2), confidence=0.8)
        return edge

    if not same_floor:
        # Level link between sweeps filed under different floors (a landing): keep, unchecked.
        edge.update(type="open", confidence=0.7)
        return edge
    walls = walls_by_floor.get(a["floor_seq"]) or []
    if not walls:
        if length > MAX_LINK_NO_WALLS_M:
            return None
        edge.update(type="open", confidence=0.7, wall_checked=False)
        return edge
    crossed = wall_crossings(pa, pb, walls)
    if any(t is None or t["type"] not in PASSABLE_OPENINGS for _, t in crossed):
        return None                          # through a wall or a window
    doors = [t for _, t in crossed if t["type"] == "doorway"]
    widths = [t["width"] for _, t in crossed if t["width"]]
    edge["type"] = "door" if doors else "open"
    edge["wall_checked"] = True
    if widths:
        edge["width_m"] = round(min(widths), 2)
    if len(doors) > 1:
        edge["doors"] = len(doors)
    return edge


def _bounded_dist(adj: dict, src: str, dst: str, limit: float) -> float:
    """Shortest kept-graph distance from src to dst, giving up beyond `limit`."""
    dist = {src: 0.0}
    heap = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            return d
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd <= limit and nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return math.inf


def spanner(node_ids, candidates: list[dict], t: float = SPANNER_T) -> list[dict]:
    """Greedy t-spanner: shortest links first, skipping any link the kept ones already cover."""
    adj = {n: [] for n in node_ids}
    kept = []
    for e in sorted(candidates, key=lambda e: (e["meters"], e["a"], e["b"])):
        if _bounded_dist(adj, e["a"], e["b"], t * e["meters"]) <= t * e["meters"]:
            continue
        kept.append(e)
        adj[e["a"]].append((e["b"], e["meters"]))
        adj[e["b"]].append((e["a"], e["meters"]))
    return kept


def components(node_ids, edges) -> list[set]:
    adj = defaultdict(list)
    for e in edges:
        adj[e["a"]].append(e["b"])
        adj[e["b"]].append(e["a"])
    seen, out = set(), []
    for n in node_ids:
        if n in seen:
            continue
        comp, stack = {n}, [n]
        while stack:
            for v in adj[stack.pop()]:
                if v not in comp:
                    comp.add(v)
                    stack.append(v)
        seen |= comp
        out.append(comp)
    return sorted(out, key=len, reverse=True)


def reconnect(node_ids, kept: list[dict], dropped_links: list[dict]) -> list[dict]:
    """Join leftover components with the shortest dropped links, marked low confidence."""
    comps = components(node_ids, kept)
    where = {n: i for i, c in enumerate(comps) for n in c}
    added = []
    for e in sorted(dropped_links, key=lambda e: e["meters"]):
        if where[e["a"]] != where[e["b"]]:
            old, new = where[e["b"]], where[e["a"]]
            for n in [n for n, c in where.items() if c == old]:
                where[n] = new
            added.append(dict(e, confidence=0.4, long_link=True))
    return added


def force_links(sweeps: dict, edges: list[dict]) -> list[dict]:
    """Tie each stranded group of sweeps to the nearest sweep of the main group on the
    same floor. These links are guesses (a wall may be in the way), marked `assumed`
    with low confidence, so that every scanned position can at least be routed to.
    Floors with no surviving link between them stay apart: we do not invent a way up."""
    added = []
    ids = list(sweeps)
    while True:
        comps = components(ids, edges + added)
        if len(comps) < 2:
            break
        main, best = comps[0], None
        for comp in comps[1:]:
            for n in comp:
                for m in main:
                    if sweeps[n]["floor_seq"] != sweeps[m]["floor_seq"]:
                        continue
                    d = math.dist(sweeps[n]["floor_xyz"], sweeps[m]["floor_xyz"])
                    if best is None or d < best[0]:
                        best = (d, n, m)
        if best is None:
            break
        d, n, m = best
        a, b = (n, m) if n < m else (m, n)
        added.append({"a": a, "b": b, "type": "open", "meters": round(d, 2), "confidence": 0.3,
                      "evidence_path": None, "assumed": True, "forced": True})
    return added


GRADE_LABEL = ("floor 1", "level 1", "first floor", "ground", "ground floor", "lobby", "1")


def grade_floor(floors: list[dict]) -> int | None:
    """Sequence of the floor at street level: the one labeled "Floor 1" (or similar),
    otherwise the lowest. Scans of an upper suite have no street-level floor, but their
    doors to the unscanned corridor are still the way in, so the lowest floor serves."""
    for f in floors:
        if (f.get("label") or "").strip().lower() in GRADE_LABEL:
            return f["sequence"]
    return min((f["sequence"] for f in floors), default=None)


# ------------------------------------------------------------------ entrances
def exterior_doorways(walls_by_floor: dict, wall_rooms: dict) -> list[dict]:
    """Doorways whose wall has a room on one side only and nothing but open air beyond.

    For each such doorway a ray leaves the wall on both sides. A side whose ray meets no
    other wall within OUTSIDE_RAY_M is taken to be outside. Unscanned rooms on the
    perimeter look the same, so these are candidates to confirm, not facts.
    """
    out = []
    for seq, walls in walls_by_floor.items():
        solid = [w for w in walls if w["type"] == "wall"]
        for w in solid:
            if wall_rooms.get(w["id"], 0) > 1 or w["length"] < 1e-6:
                continue
            ux, uy = (w["b"][0] - w["a"][0]) / w["length"], (w["b"][1] - w["a"][1]) / w["length"]
            for k, o in enumerate(w["openings"]):
                if o["type"] != "doorway":
                    continue
                cx, cy = w["a"][0] + ux * o["center_m"], w["a"][1] + uy * o["center_m"]
                clear = []
                for sx, sy in ((-uy, ux), (uy, -ux)):
                    p = (cx + sx * 0.15, cy + sy * 0.15)
                    q = (cx + sx * OUTSIDE_RAY_M, cy + sy * OUTSIDE_RAY_M)
                    if not any(seg_hit(p, q, v["a"], v["b"]) for v in solid if v is not w):
                        clear.append((sx, sy))
                if len(clear) == 1:
                    out.append({"floor_seq": seq, "wall_id": w["id"], "index": k,
                                "center": (cx, cy), "outward": clear[0],
                                "width": o["width"], "height": o.get("height")})
    return out


def _entrance_nodes(doorways, sweeps, walls_by_floor) -> tuple[list[dict], list[dict]]:
    """A node just outside each exterior doorway, joined by a door edge to the nearest
    sweep that can see the doorway without another wall in the way."""
    nodes, edges = [], []
    by_floor = defaultdict(list)
    for s in sweeps.values():
        by_floor[s["floor_seq"]].append(s)
    for d in doorways:
        cx, cy = d["center"]
        ox, oy = d["outward"]
        inside_pt = (cx - ox * 0.3, cy - oy * 0.3)
        walls = [w for w in walls_by_floor.get(d["floor_seq"]) or [] if w["id"] != d["wall_id"]]
        best = None
        for s in sorted(by_floor.get(d["floor_seq"], []),
                        key=lambda s: math.dist(s["floor_xyz"][:2], inside_pt)):
            p = s["floor_xyz"]
            if (p[0] - cx) * ox + (p[1] - cy) * oy > 0:
                continue                      # a sweep on the outside of this door
            if math.dist(p[:2], inside_pt) > 12.0:
                break
            if any(t is None or t["type"] not in PASSABLE_OPENINGS
                   for _, t in wall_crossings(p[:2], inside_pt, walls)):
                continue
            best = s
            break
        if best is None:
            continue
        nid = f"door_{d['wall_id'][:8]}_{d['index']}"
        out_pt = (cx + ox * 0.6, cy + oy * 0.6)
        nodes.append({"id": nid, "floor": d["floor_seq"],
                      "xyz": [round(out_pt[0], 3), round(out_pt[1], 3), round(best["floor_xyz"][2], 3)],
                      "pano_path": None, "room_label": None,
                      "attributes": {"door_width_m": round(d["width"], 2),
                                     "outward_xy": [round(ox, 3), round(oy, 3)],
                                     "seen_from": best["id"]},
                      "is_entrance": True, "equipment": [], "kind": "entrance",
                      "label": None, "source": "auto: doorway facing outside", "confirmed": False})
        edges.append({"a": best["id"], "b": nid, "type": "door",
                      "meters": round(math.dist(best["floor_xyz"][:2], out_pt), 2),
                      "width_m": round(d["width"], 2), "confidence": 0.6, "evidence_path": None,
                      "wall_checked": True})
    return nodes, edges


COMPASS = ("east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast")


def _name_entrances(nodes: list[dict]) -> None:
    """Label unnamed entrances by where they sit around the building: "the southwest door".

    Uses lat/lon when the scan is georeferenced, so the names match a map. Without it the
    doors are numbered, because the scan's own axes do not point north.
    """
    for n in nodes:
        if n.get("kind") == "exterior_door" and not n.get("label"):
            n["label"] = "an exterior door"
    doors = [n for n in nodes if n.get("is_entrance") and not n.get("label")]
    panos = [n for n in nodes if n.get("kind") == "pano" and n.get("latlon")]
    if not doors:
        return
    if panos and all(d.get("latlon") for d in doors):
        lat0 = sum(n["latlon"][0] for n in panos) / len(panos)
        lon0 = sum(n["latlon"][1] for n in panos) / len(panos)
        k = math.cos(math.radians(lat0))
        for d in doors:
            east, north = (d["latlon"][1] - lon0) * k, d["latlon"][0] - lat0
            d["side"] = COMPASS[round(math.degrees(math.atan2(north, east)) / 45) % 8]
            d["label"] = f"the {d['side']} door"
        seen = defaultdict(list)
        for d in doors:
            seen[d["label"]].append(d)
        for label, group in seen.items():
            if len(group) > 1:
                for i, d in enumerate(sorted(group, key=lambda d: d["id"]), 1):
                    d["label"] = f"{label} ({i} of {len(group)})"
    else:
        for i, d in enumerate(sorted(doors, key=lambda d: d["id"]), 1):
            d["label"] = f"entrance {i}"


# ------------------------------------------------------------------ build
def build(model_id: str, building_id: str | None = None, *, geo: bool = True,
          refresh: bool = False, overrides: dict | None = None) -> dict:
    building_id = str(building_id or model_id)
    if overrides is None:
        over_path = config.BUILDINGS / building_id / "overrides.json"
        overrides = json.loads(over_path.read_text()) if over_path.exists() else {}
    meta = mp.get_model_meta(model_id, refresh)
    all_sweeps = mp.get_sweeps(model_id)
    sweeps = {k: s for k, s in all_sweeps.items()
              if s["floor_seq"] is not None and s["floor_xyz"] is not None}
    for s in sweeps.values():
        s["neighbors"] = [n for n in s["neighbors"] if n in sweeps]
    walls_by_floor = mp.get_walls(model_id)
    rooms = {r["id"]: r for r in meta["rooms"]}
    wall_rooms: dict[str, int] = defaultdict(int)
    for r in meta["rooms"]:
        for e in (r.get("boundary") or {}).get("edges") or []:
            wall_rooms[e["id"]] += 1

    nodes = []
    for s in sweeps.values():
        room = rooms.get(s["room_id"]) or {}
        classes = [c["label"] for c in room.get("classifications") or [] if c.get("label")]
        named = (room.get("label") or "").strip()
        room_class = next((c for c in classes if c not in ("Void", "Other")), None)
        attrs = {"room_id": s["room_id"], "room_class": room_class}
        if room_class == "Staircase":
            attrs["stairs"] = True
        nodes.append({"id": s["id"], "floor": s["floor_seq"],
                      "xyz": [round(v, 3) for v in s["floor_xyz"]],
                      "pano_xyz": [round(v, 3) for v in s["pano_xyz"]], "quat": s["quat"],
                      "pano_path": f"buildings/{building_id}/panos/{s['id']}.jpg",
                      "room_label": named or room_class, "attributes": attrs,
                      "is_entrance": False, "equipment": [], "kind": "pano", "label": None})

    candidates, dropped = [], []
    stats = defaultdict(int)
    seen = set()
    masks = {f["sequence"]: floor_mask(model_id, f["sequence"], sweeps) for f in meta["floors"]}
    stats["floors_with_floor_mask"] = sum(m is not None for m in masks.values())
    for a in sweeps.values():
        for b_id in a["neighbors"]:
            k = (a["id"], b_id) if a["id"] < b_id else (b_id, a["id"])
            if k in seen:
                continue
            seen.add(k)
            b = sweeps[b_id]
            e = classify_link(a, b, walls_by_floor)
            stats["links"] += 1
            if e is None:
                stats["dropped_wall_or_level"] += 1
                dropped.append({"a": k[0], "b": k[1], "type": "open",
                                "meters": round(math.dist(a["floor_xyz"], b["floor_xyz"]), 2),
                                "evidence_path": None})
                continue
            mask = masks.get(a["floor_seq"]) if a["floor_seq"] == b["floor_seq"] else None
            if mask is not None and e["type"] in ("open", "door") and \
                    off_floor_run_m(mask, a["floor_xyz"], b["floor_xyz"]) > VOID_MAX_M:
                stats["dropped_over_void"] += 1      # not offered back by `reconnect` either
                continue
            candidates.append(dict(e, a=k[0], b=k[1]))
    kept = spanner(list(sweeps), candidates)
    stats["dropped_spanner"] = len(candidates) - len(kept)
    # Only level links on one floor are safe to bring back; a dropped link between floors
    # was a view, and bringing it back would invent a way up.
    same_level = [e for e in dropped
                  if sweeps[e["a"]]["floor_seq"] == sweeps[e["b"]]["floor_seq"]
                  and abs(sweeps[e["a"]]["floor_xyz"][2] - sweeps[e["b"]]["floor_xyz"][2]) < FLAT_DZ_M]
    bridges = reconnect(list(sweeps), kept, same_level)
    stats["reconnected"] = len(bridges)
    forced = force_links(sweeps, kept + bridges)
    stats["forced_links"] = len(forced)
    stats["sweeps_without_floor"] = len(all_sweeps) - len(sweeps)
    edges = kept + bridges + forced

    grade = grade_floor(meta["floors"])
    doorways = exterior_doorways(walls_by_floor, wall_rooms)
    door_nodes, door_edges = _entrance_nodes(doorways, sweeps, walls_by_floor)
    for n in door_nodes:
        if n["floor"] != grade:
            # A door to the outside above or below street level: a balcony, a roof, an
            # exterior stair or a garage exit. Kept on the map, not used as a way in
            # until someone confirms it in overrides.json.
            n["is_entrance"] = False
            n["kind"] = "exterior_door"
            n["attributes"]["exterior_door_candidate"] = True
    nodes += door_nodes
    edges += door_edges
    stats["auto_entrances"] = sum(n["is_entrance"] for n in door_nodes)
    stats["other_exterior_doors"] = sum(not n["is_entrance"] for n in door_nodes)
    facts_path = config.BUILDINGS / building_id / "attributes.json"
    if facts_path.exists():
        unknown = apply_attributes(nodes, json.loads(facts_path.read_text()))
        stats["attribute_ids_not_in_graph"] = len(unknown)
    if overrides:
        by_latlon = {n["id"]: n["latlon"] for n in overrides.get("add_nodes") or []
                     if n.get("latlon") and not n.get("xyz")}
        if by_latlon:
            xy = mp.get_model_xy(model_id, by_latlon)
            grade_z = sorted(s["floor_xyz"][2] for s in sweeps.values() if s["floor_seq"] == grade)
            z = grade_z[len(grade_z) // 2] if grade_z else 0.0
            for n in overrides["add_nodes"]:
                if n["id"] in by_latlon:
                    if xy.get(n["id"]) is None:
                        raise RuntimeError(f"cannot place {n['id']}: the scan has no georeference")
                    n["xyz"] = [round(xy[n["id"]][0], 3), round(xy[n["id"]][1], 3), round(z, 3)]
                    n.setdefault("floor", grade)
        patched = apply_overrides({"nodes": nodes, "edges": edges}, overrides)
        nodes, edges = patched["nodes"], patched["edges"]

    geo_meta = meta.get("geocoordinates") or {}
    if geo and geo_meta.get("source"):
        latlon = mp.get_latlon(model_id, {n["id"]: n["xyz"] for n in nodes if n.get("xyz")})
        for n in nodes:
            n["latlon"] = n.get("latlon") or latlon.get(n["id"])
    _name_entrances(nodes)
    evidence_dir = config.BUILDINGS / building_id / "evidence"
    for n in nodes:                        # crops written by `python -m env.views evidence`
        shot = evidence_dir / f"{n['id']}.jpg"
        if shot.exists() and not n.get("evidence_path"):
            n["evidence_path"] = str(shot.relative_to(config.ROOT))

    floorplans = {}
    for f in meta["floors"]:
        try:
            fp = mp.get_floorplan(model_id, f["sequence"])
        except mp.MatterportError:
            fp = None
        if fp:
            floorplans[str(f["sequence"])] = {
                "path": str(Path(fp["path"]).relative_to(config.ROOT)),
                "pixels_per_meter": fp["resolution"], "origin_xy": fp["origin"],
                "width": fp["width"], "height": fp["height"]}

    comps = components([n["id"] for n in nodes], edges)
    graph = {
        "building_id": building_id, "matterport_model_id": model_id, "name": meta.get("name"),
        "address": (meta.get("address") or {}).get("address"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "matterport sweeps and wall plan",
        "floor_offset": int(overrides.get("floor_offset", 0) or 0),
        "grade_floor": grade,
        "floors": [{"floor": f["sequence"], "label": f.get("label"),
                    "has_walls": bool(walls_by_floor.get(f["sequence"]))}
                   for f in sorted(meta["floors"], key=lambda f: f["sequence"])],
        "floorplans": floorplans,
        "georeference": {"source": geo_meta.get("source"), "latitude": geo_meta.get("latitude"),
                         "longitude": geo_meta.get("longitude"),
                         "note": "scan placement is good to a few meters, not to a door"},
        "stats": dict(stats, nodes=len(nodes), edges=len(edges), components=len(comps),
                      largest_component=len(comps[0]) if comps else 0,
                      entrances=sum(bool(n.get("is_entrance")) for n in nodes),
                      elevator_edges=sum(e["type"] == "elevator" for e in edges),
                      stairs_edges=sum(e["type"] == "stairs" for e in edges),
                      door_edges=sum(e["type"] == "door" for e in edges)),
        "nodes": nodes, "edges": edges,
    }
    for k in ("name", "address", "scope"):
        if overrides.get(k):
            graph[k] = overrides[k]
    labels = {f["floor"]: f.get("label") for f in graph["floors"]}
    for n in nodes:            # what to call this node's floor in a packet
        seq = n.get("floor")
        if seq is None:
            continue
        if graph["floor_offset"] or not labels.get(seq):
            n["floor_name"] = f"Floor {seq + 1 + graph['floor_offset']}"
        else:
            n["floor_name"] = labels[seq]
    return fill_costs(graph, force=True)


# ------------------------------------------------------------------ merging facts onto nodes
def merge_node(node: dict, patch: dict) -> dict:
    """Apply a patch to a node without losing what is already there: `attributes` is
    merged key by key, `equipment` is extended without duplicates, the rest is set."""
    for k, v in patch.items():
        if k == "attributes" and isinstance(v, dict):
            node["attributes"] = {**(node.get("attributes") or {}), **v}
        elif k == "equipment" and isinstance(v, list):
            have = list(node.get("equipment") or [])
            for item in v:
                if item not in have:
                    have.append(item)
            node["equipment"] = have
        else:
            node[k] = v
    return node


def apply_attributes(nodes: list[dict], facts: dict) -> list[str]:
    """Merge lane B's per-node facts (`buildings/<id>/attributes.json`) onto the nodes.

        {"<node id>": {"room_label": "Exam room", "label": "the nurses' station",
                       "attributes": {"window": true, "floor_surface": "carpet",
                                      "signage": ["Suite 1425"], "hazards": ["oxygen cylinders"]},
                       "equipment": [{"type": "aed", "evidence_path": "..."}]}}

    Returns the ids in the file that are not in the graph (a stale file, or a typo).
    """
    by_id = {n["id"]: n for n in nodes}
    unknown = []
    for nid, patch in facts.items():
        if nid in by_id:
            merge_node(by_id[nid], patch)
        else:
            unknown.append(nid)
    return unknown


# ------------------------------------------------------------------ overrides
def apply_overrides(graph: dict, overrides: dict) -> dict:
    """Hand edits on top of what the scan gave. All keys are optional.

        {"floor_offset": 13, "name": "...", "scope": "what the scan covers and what it does not",
         "nodes": {"<id>": {"label": "Stair B", "is_entrance": true, "primary": true, ...}},
         "add_nodes": [{"id": "door_front", "near": "<sweep id>", "heading_deg": 103,
                        "distance_m": 5.5, "edge_type": "door", "is_entrance": true,
                        "label": "the front doors", "primary": true,
                        "approach": {"via": "elevator", "floors": 13, "walk_m": 10}},
                       {"id": "stage_west", "kind": "staging", "latlon": [lat, lon], "label": "..."}],
         "add_edges": [{"a": "...", "b": "...", "type": "door"}],
         "elevators": [{"id": "elev", "label": "the elevator", "offset_xy": [2.2, 0],
                        "stops": {"<floor>": "<lobby sweep id>", ...}}],
         "edges": [{"a": "...", "b": "...", "type": "locked_door", "note": "..."}],
         "remove_edges": [["a", "b"]], "remove_nodes": ["door_..."]}

    An elevator becomes one node per stop, placed `offset_xy` from the lobby sweep that
    sees its doors, with an elevator edge between every pair of stops, so a ride of
    several floors pays the wait once. Costs are recomputed afterwards.
    """
    gone = set(overrides.get("remove_nodes") or [])
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in gone]
    base = {"pano_path": None, "room_label": None, "attributes": {}, "is_entrance": False,
            "equipment": [], "kind": "pano", "source": "hand"}
    by_id = {n["id"]: n for n in graph["nodes"]}
    new_edges = [dict(e) for e in overrides.get("add_edges") or []]
    for n in overrides.get("add_nodes") or []:
        n = dict(n)
        if n.get("near"):
            # "from sweep S, it is D meters away at heading H": the usual way to place a
            # door, an elevator or a stair door seen in a panorama.
            if n["near"] not in by_id:
                raise KeyError(f"node {n['id']} is placed near unknown sweep {n['near']}")
            src = by_id[n["near"]]
            h, dist = math.radians(float(n.pop("heading_deg"))), float(n.pop("distance_m"))
            n["xyz"] = [round(src["xyz"][0] + dist * math.cos(h), 3),
                        round(src["xyz"][1] + dist * math.sin(h), 3), src["xyz"][2]]
            n.setdefault("floor", src["floor"])
            n["attributes"] = {**(n.get("attributes") or {}), "seen_from": n["near"]}
            new_edges.append({"a": n.pop("near"), "b": n["id"], "type": n.pop("edge_type", "open"),
                              "meters": round(dist, 2), "confidence": n.get("confidence", 0.8),
                              "assumed": False,
                              **({"width_m": n.pop("width_m")} if n.get("width_m") else {})})
        node = {**base, **n}
        if node["is_entrance"] and n.get("kind") is None:
            node["kind"] = "entrance"
        node.setdefault("confirmed", True)
        graph["nodes"].append(node)
        by_id[node["id"]] = node
    for el in overrides.get("elevators") or []:
        dx, dy = el.get("offset_xy") or (0.0, 0.0)
        stops = []
        for floor, lobby in sorted(el["stops"].items(), key=lambda kv: int(kv[0])):
            if lobby not in by_id:
                raise KeyError(f"elevator {el['id']} names unknown lobby sweep {lobby}")
            x, y, z = by_id[lobby]["xyz"]
            nid = f"{el['id']}_f{floor}"
            node = {**base, "id": nid, "floor": int(floor), "kind": "elevator",
                    "xyz": [round(x + dx, 3), round(y + dy, 3), z],
                    "label": el.get("label") or "the elevator",
                    "attributes": {"elevator": True, "seen_from": lobby},
                    "source": el.get("source") or "hand: elevator doors seen in the scan"}
            graph["nodes"].append(node)
            by_id[nid] = node
            stops.append(node)
            new_edges.append({"a": lobby, "b": nid, "type": "open",
                              "meters": round(math.hypot(dx, dy), 2), "confidence": 0.9,
                              "assumed": False})
        for i, a in enumerate(stops):
            for b in stops[i + 1:]:
                new_edges.append({"a": a["id"], "b": b["id"], "type": "elevator",
                                  "floors": abs(b["floor"] - a["floor"]),
                                  "meters": round(abs(b["xyz"][2] - a["xyz"][2]), 2),
                                  "confidence": el.get("confidence", 0.9), "assumed": False})
    for nid, patch in (overrides.get("nodes") or {}).items():
        if nid not in by_id:
            raise KeyError(f"override names unknown node {nid}")
        merge_node(by_id[nid], patch)
        if patch.get("is_entrance") and by_id[nid].get("kind") in ("pano", "exterior_door"):
            by_id[nid]["kind"] = "entrance" if by_id[nid]["kind"] == "exterior_door" else "pano"
    key = lambda a, b: (a, b) if a <= b else (b, a)
    drop = {key(*p) for p in overrides.get("remove_edges") or []}
    graph["edges"] = [e for e in graph["edges"] if key(e["a"], e["b"]) not in drop
                      and e["a"] not in gone and e["b"] not in gone]
    for e in new_edges:
        for end in (e["a"], e["b"]):
            if end not in by_id:
                raise KeyError(f"added edge names unknown node {end}")
        if "meters" not in e:
            e["meters"] = round(math.dist(by_id[e["a"]]["xyz"], by_id[e["b"]]["xyz"]), 2)
        graph["edges"].append({"confidence": 0.5, "evidence_path": None, "assumed": True, **e})
    index = {key(e["a"], e["b"]): e for e in graph["edges"]}
    for patch in overrides.get("edges") or []:
        k = key(patch["a"], patch["b"])
        if k not in index:
            raise KeyError(f"override names unknown edge {patch['a']} - {patch['b']}")
        index[k].update({kk: v for kk, v in patch.items() if kk not in ("a", "b")})
    return fill_costs(graph, force=True)


def write(graph: dict) -> Path:
    path = config.BUILDINGS / graph["building_id"] / "graph.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=1))
    return path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Build graph.json for one building from its scan.")
    ap.add_argument("--model", help="Matterport model id (not needed when the building is already built)")
    ap.add_argument("--building", help="building id (CoStar PropertyId); defaults to the model id")
    ap.add_argument("--all", action="store_true", help="rebuild every building that has a scan-based graph")
    ap.add_argument("--no-geo", action="store_true", help="skip the lat/lon lookup")
    ap.add_argument("--refresh", action="store_true", help="re-fetch the scan structure")
    ap.add_argument("--render", action="store_true", help="also draw the graph over the floor plan")
    a = ap.parse_args(argv)
    todo = []
    if a.all:
        for p in sorted(config.BUILDINGS.glob("*/graph.json")):
            model = json.loads(p.read_text()).get("matterport_model_id")
            if model:
                todo.append((model, p.parent.name))
    else:
        model = a.model
        if not model and a.building:
            existing = config.BUILDINGS / a.building / "graph.json"
            if existing.exists():
                model = json.loads(existing.read_text()).get("matterport_model_id")
        if not model:
            ap.error("give --model, or --building for a building that is already built, or --all")
        todo.append((model, a.building))
    for model, building in todo:
        graph = build(model, building, geo=not a.no_geo, refresh=a.refresh)
        path = write(graph)
        print(f"{graph['name']} -> {path.relative_to(config.ROOT)}")
        print("  " + ", ".join(f"{k}={v}" for k, v in graph["stats"].items()))
        if a.render:
            from .render import render_building
            for p in render_building(graph["building_id"]):
                print(f"  drew {p.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
