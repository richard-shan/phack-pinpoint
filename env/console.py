"""Lane A's graph and routes in the form lane D's console reads.

The console (`app/`) draws a route inside the Matterport viewer from a "scan graph" file
(`app/data/<name>-scan.json`) and takes a route as a list of sweep ids on
`POST /api/incidents/<id>/route`. This module writes that file from `graph.json` and
turns a lane A route into that request body.

    python3 -m env.console 5956257                                   # buildings/5956257/console_scan.json
    python3 -m env.console 5956257 --out app/data/los-angeles-scan.json
    python3 -m env.console 5956257 --route <sweep id> --profile stretcher

What changes on the way:

- The console's nodes are scan positions only, because it moves the viewer camera to each
  one. Doors become its `entries`. Any other node that is not a scan position (an
  elevator car, an added stair door) is folded into one edge between the scan positions
  on either side, with the folded points kept in `points` so the line still passes
  through them.
- Positions go from the scan's axes (z up) to the viewer's (y up): (x, z, -y).
- The console's planner only knows "walk", "stairs" and "elevator" and measures distance.
  It does not know door widths, locked doors or travel times. For a route that respects
  those, post `route_update(...)`: it carries lane A's path as `nodeIds`.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from . import config, routing
from .graph import Graph

OUTSIDE_KINDS = ("staging", "arrival")
OUTSIDE_EDGES = ("exterior_foot", "exterior_vehicle")
AERIAL_HALF_M = 70.0          # the console's /api/aerial/<id>.jpg is 140 m square on the property


def _sdk(p) -> dict:
    """Scan coordinates (z up) to Matterport viewer coordinates (y up)."""
    return {"x": round(p[0], 3), "y": round(p[2], 3), "z": round(-p[1], 3)}


def _latlng(p) -> dict | None:
    return {"lat": p[0], "lng": p[1]} if p and len(p) == 2 else None


def _is_sweep(g: Graph, n: str) -> bool:
    return g.kind(n) == "pano"


def _plain(label: str | None, fallback: str) -> str:
    text = (label or fallback).strip()
    if text.lower().startswith("the "):
        text = text[4:]
    return text[:1].upper() + text[1:]


def _folded_edges(g: Graph, sweeps: set[str]) -> list[tuple[str, str, list[str]]]:
    """Connections between two scan positions that run through nodes the console does not
    have (elevator cars, door nodes). -> [(sweep a, sweep b, [a, via..., b])]"""
    found: dict[tuple[str, str], list[str]] = {}
    for start in sweeps:
        stack = [(start, [start])]
        while stack:
            here, path = stack.pop()
            for nxt, e in g.adj[here]:
                if e["type"] in OUTSIDE_EDGES or nxt in path:
                    continue
                if nxt in sweeps:
                    if len(path) > 1:                      # went through something folded
                        key = (start, nxt) if start <= nxt else (nxt, start)
                        chain = path + [nxt]
                        chain = chain if chain[0] == key[0] else chain[::-1]
                        if key not in found or len(chain) < len(found[key]):
                            found[key] = chain
                elif g.kind(nxt) not in OUTSIDE_KINDS and len(path) < 4:
                    stack.append((nxt, path + [nxt]))
    return [(a, b, chain) for (a, b), chain in sorted(found.items())]


def _edge_kind(g: Graph, chain: list[str]) -> str:
    types = {g.edge(a, b)["type"] for a, b in zip(chain, chain[1:])}
    return "elevator" if "elevator" in types else "stairs" if "stairs" in types else "walk"


def _entry_sweep(g: Graph, door: str, sweeps: set[str]) -> str | None:
    """The scan position just inside a door."""
    best = None
    for nxt, e in g.adj[door]:
        if nxt in sweeps and e["type"] not in OUTSIDE_EDGES:
            sec = e["seconds"].get("foot") or 0.0
            if best is None or sec < best[0]:
                best = (sec, nxt)
    return best[1] if best else None


def _staging_for(g: Graph, door: str) -> dict | None:
    spots = [(e["seconds"].get("foot") or 0.0, g.nodes[n]) for n, e in g.adj[door]
             if e["type"] in OUTSIDE_EDGES and g.kind(n) in OUTSIDE_KINDS]
    return min(spots, key=lambda s: s[0])[1] if spots else None


def _hand_placed(building_id: str) -> set[str]:
    """Nodes whose lat/lon was set by hand in overrides.json (not taken from the scan)."""
    path = config.BUILDINGS / str(building_id) / "overrides.json"
    if not path.exists():
        return set()
    o = json.loads(path.read_text())
    ids = {n["id"] for n in o.get("add_nodes") or [] if n.get("latlon") and n.get("id")}
    ids |= {k for k, v in (o.get("nodes") or {}).items() if v.get("latlon")}
    return ids


def _property_latlon(building_id: str):
    try:
        from . import costar
        p = costar.get_property(str(building_id))
        return (p or {}).get("latlon")
    except Exception:
        return None


def _index(g: Graph) -> dict[str, int]:
    return {n: i for i, n in enumerate(n for n in g.nodes if _is_sweep(g, n))}


def _edge_id(index: dict[str, int], a: str, b: str) -> str:
    i, j = sorted((index[a], index[b]))
    return f"{i}-{j}"


def scan_graph(building_id: str, target: str | None = None) -> dict:
    """The building's graph in the console's scan-graph format.

    `target` becomes `defaultDestination`; without it, the scan position that takes
    longest to reach on foot by its best door. `entries[0]` is the best door for that
    destination, because the console opens with a route from its first entry.
    """
    g = Graph.load(str(building_id))
    index = _index(g)
    sweeps = set(index)
    if not sweeps:
        raise ValueError(f"building {building_id} has no scan positions")
    hand = _hand_placed(building_id)
    center = _property_latlon(building_id)

    def floor_label(seq) -> str:
        named = next((g.nodes[n].get("floor_name") for n in index
                      if g.nodes[n].get("floor") == seq and g.nodes[n].get("floor_name")), None)
        listed = next((f.get("label") for f in g.data.get("floors") or [] if f.get("floor") == seq), None)
        return named or listed or f"Level {seq + 1}"

    floors = sorted({g.nodes[n].get("floor") or 0 for n in index})
    nodes = []
    for n, i in index.items():
        d = g.nodes[n]
        seq = d.get("floor") or 0
        where = g.label(n) or g.room(n)
        where = where[:1].upper() + where[1:] if where else where
        nodes.append({"id": n, "index": i, "floor": seq, "position": _sdk(d["xyz"]),
                      "label": " · ".join(x for x in (where, floor_label(seq)) if x) if where
                               else f"{floor_label(seq)} · position {i}"})

    edges = []
    for (a, b), e in g.edges.items():
        if a in sweeps and b in sweeps:
            kind = "stairs" if e["type"] == "stairs" else "elevator" if e["type"] == "elevator" else "walk"
            edges.append({"id": _edge_id(index, a, b), "a": a, "b": b, "kind": kind,
                          "source": f"lane-a:{e['type']}"})
    direct = {e["id"] for e in edges}
    for a, b, chain in _folded_edges(g, sweeps):
        eid = _edge_id(index, a, b)
        if eid in direct:
            continue
        edges.append({"id": eid, "a": a, "b": b, "kind": _edge_kind(g, chain),
                      "source": "lane-a:via " + ", ".join(chain[1:-1]),
                      "points": [_sdk(g.nodes[n]["xyz"]) for n in chain if g.xyz(n)]})

    entries = []
    for d in g.entrances():
        inside = _entry_sweep(g, d["id"], sweeps)
        if inside is None:
            continue
        stage = _staging_for(g, d["id"])
        door_ll = _latlng(d.get("latlon")) or _latlng(center)
        approach = [p for p in (_latlng((stage or {}).get("latlon")), door_ll) if p]
        notes = []
        if not approach:                  # the console's map needs at least one point
            approach = [{"lat": 0.0, "lng": 0.0}]
            notes.append("No map position is known for this door.")
        if stage is None:
            notes.append("No staging spot is marked for this door.")
        else:
            notes.append("Staging spot placed by eye on the aerial; not confirmed on site.")
        if d.get("latlon"):
            notes.append("Door position read off the aerial by hand." if d["id"] in hand else
                         "Door position comes from the scan's own georeference, which can be 20 m or more off.")
        if (d.get("approach") or {}).get("locked_entry"):
            notes.append("Expect this door to be locked from outside.")
        if d.get("approach") and (d["approach"].get("via")):
            notes.append(f"Reached by {d['approach']['via']} through parts of the building that are not scanned.")
        entries.append({
            "id": d["id"], "label": _plain(g.label(d["id"]), d["id"]), "nodeId": inside,
            "position": _sdk(d["xyz"]) if g.xyz(d["id"]) else _sdk(g.nodes[inside]["xyz"]),
            **approach[-1], "approach": approach, "calibration": " ".join(notes),
            "primary": bool(d.get("primary")), "confirmed": d.get("confirmed", False),
            "stagingId": (stage or {}).get("id"), "stagingLabel": (stage or {}).get("label"),
        })
    if not entries:
        raise ValueError(f"building {building_id} has no entrance next to a scan position")

    if target is None:                    # the position that takes longest to reach by its best door
        outside, _ = routing._outside(g, "foot")
        doors = {e["id"]: outside.get(e["id"], 0.0) for e in entries}
        dist, _ = routing.search(g, doors, "foot", edge_filter=routing._interior)
        target = max((n for n in sweeps if n in dist), key=lambda n: (dist[n], n))
    elif target not in sweeps:
        raise ValueError(f"{target} is not a scan position in building {building_id}")
    best = routing.best_entry(g, target, "foot")
    order = [r["entrance_id"] for r in best.get("ranked") or []]
    entries.sort(key=lambda e: order.index(e["id"]) if e["id"] in order else len(order))

    out = {
        "modelId": g.data.get("matterport_model_id"),
        "propertyId": str(building_id),
        "name": g.data.get("name") or str(building_id),
        "faceRoute": True,
        "supportsOverview": len(floors) > 1,
        "georeference": {"lat": center[0], "long": center[1]} if center else None,
        "floors": [{"id": str(s), "sequence": s, "label": floor_label(s)} for s in floors],
        "nodes": nodes, "edges": edges, "entries": entries,
        "defaultDestination": target,
        "scope": g.data.get("scope"),
        "provenance": {
            "source": "lane A graph (env/build_graph.py), exported by env/console.py",
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "coordinates": "Matterport scan positions at floor level, (x, y, z) to viewer (x, z, -y).",
            "routing": "Links are the scan's line-of-sight links with the ones that cross a wall, "
                       "a floor void or more than 7 m of unmapped floor removed. Doors and elevators "
                       "are marked by hand from the panoramas. Nobody has walked these routes.",
            "aerial": "Vexcel. Staging spots are placed by eye.",
        },
    }
    if center:
        dy = AERIAL_HALF_M / 111320.0
        dx = dy / math.cos(math.radians(center[0]))
        out["aerial"] = {"bounds": {"west": center[1] - dx, "south": center[0] - dy,
                                    "east": center[1] + dx, "north": center[0] + dy},
                         "url": f"/api/aerial/{building_id}.jpg"}
    return out


def route_update(building_id: str, target: str, profile: str = "foot",
                 blocked_edges: list | None = None, scan: dict | None = None) -> dict:
    """Lane A's best door and route as the body for the console's
    `POST /api/incidents/<id>/route`. Add `expectedRevision` before sending.

    `blocked_edges` is a list of [node, node] pairs, as in `tools.replan`. When the target
    cannot be reached, returns {"reachable": False, "reason": ...} and nothing to post.
    """
    g = Graph.load(str(building_id))
    if target not in g.nodes and "/" in str(target) and str(target).rsplit("/", 1)[1] in g.nodes:
        target = str(target).rsplit("/", 1)[1]
    index = _index(g)
    sweeps = set(index)
    if target not in sweeps:
        return {"reachable": False, "reason": f"{target} is not a scan position in building {building_id}"}
    blocked = [tuple(p) for p in blocked_edges or []]
    best = routing.best_entry(g, target, profile, blocked=blocked)
    if not best.get("reachable"):
        return {"reachable": False, "reason": best.get("reason"), "carry": best.get("carry")}
    scan = scan or scan_graph(building_id, target)
    entry = next((e for e in scan["entries"] if e["id"] == best["entrance_id"]), None)
    if entry is None:
        return {"reachable": False, "reason": f"door {best['entrance_id']} is not in the console's entries"}
    node_ids = [n for n in best["path"] if n in sweeps]
    if not node_ids or node_ids[0] != entry["nodeId"]:
        node_ids.insert(0, entry["nodeId"])

    who = "a crew on foot" if profile == "foot" else "a stretcher"
    reason = (f"Best door for {who}: {g.label(best['entrance_id']) or best['entrance_id']}, "
              f"{round(best['seconds'])} s to the caller ({round(best['outside_seconds'])} s outside, "
              f"{round(best['inside_seconds'])} s inside).")
    if best.get("staging_id"):
        reason += f" Stage at {g.label(best['staging_id']) or best['staging_id']}."
    ru = best.get("runner_up")
    if ru:
        gap = round(ru["delta_seconds"])
        reason += f" Next best: {ru.get('label') or ru['entrance_id']}, " + \
                  (f"{gap} s slower." if gap >= 1 else "about the same time.")
    front = best.get("front_door")
    if front and not front.get("is_best"):
        reason += f" The front door would take {round(front['seconds_saved'])} s longer."
    body = {"profile": profile, "entryId": entry["id"], "destination": target, "nodeIds": node_ids,
            "updatedBy": "agent", "reason": reason[:500],
            "avoidElevators": bool(routing.ROUTING["avoid_elevators"])}
    ids = []
    for a, b in blocked:
        if a in sweeps and b in sweeps and g.edge(a, b):
            ids.append(_edge_id(index, a, b))
    if ids:
        body["blockedEdges"] = ids
    return body


def check_path(scan: dict, body: dict) -> str | None:
    """The console's own acceptance rules for an agent path, restated: starts at the
    entry's scan position, ends at the destination, every step is an edge the profile may
    use and that is not blocked. -> None when the path is accepted, else the reason."""
    nodes = {n["id"] for n in scan["nodes"]}
    entry = next((e for e in scan["entries"] if e["id"] == body.get("entryId")), None)
    if entry is None or body.get("destination") not in nodes:
        return "unknown entrance or destination"
    path = body.get("nodeIds") or []
    if not path or path[0] != entry["nodeId"] or path[-1] != body["destination"]:
        return "path must start at the entrance scan and end at the destination"
    blocked = set(body.get("blockedEdges") or [])
    usable = {frozenset((e["a"], e["b"])) for e in scan["edges"]
              if e["id"] not in blocked and not (body.get("profile") == "stretcher" and e["kind"] == "stairs")}
    for a, b in zip(path, path[1:]):
        if frozenset((a, b)) not in usable:
            return f"no usable edge {a} - {b}"
    return None


def write_scan(building_id: str, target: str | None = None, out: str | Path | None = None) -> Path:
    """Write the console's scan graph. Keeps the default destination of the file it
    replaces, so a rebuild does not move the console's demo caller."""
    path = Path(out) if out else config.BUILDINGS / str(building_id) / "console_scan.json"
    if target is None and path.exists():
        try:
            kept = json.loads(path.read_text()).get("defaultDestination")
            if kept in Graph.load(str(building_id)).nodes:
                target = kept
        except (ValueError, OSError):
            pass
    scan = scan_graph(building_id, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scan, indent=1) + "\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("building_id")
    ap.add_argument("--target", help="scan position to use as the default destination")
    ap.add_argument("--out", help="where to write the scan graph (default buildings/<id>/console_scan.json)")
    ap.add_argument("--route", metavar="TARGET", help="print the route request body for this target instead")
    ap.add_argument("--profile", default="foot", choices=list(config.PROFILES))
    a = ap.parse_args()
    if a.route:
        print(json.dumps(route_update(a.building_id, a.route, a.profile), indent=1))
        return
    out = write_scan(a.building_id, a.target, a.out)
    scan = json.loads(out.read_text())
    print(f"{out}: {len(scan['nodes'])} scan positions, {len(scan['edges'])} edges, "
          f"{len(scan['entries'])} entries, default destination {scan['defaultDestination']}")


if __name__ == "__main__":
    main()
