"""Routing numbers for the README table (plan, section 9), from real graphs.

    python -m env.metrics [building_id ...]

Every scanned position is used as a target, so the sample size is the number of
panorama nodes in the building. Prints one row per building and writes
buildings/routing_metrics.json.
"""
from __future__ import annotations

import json
import statistics
import sys
import time

from . import config, routing
from .graph import Graph, list_buildings


def building_metrics(building_id: str) -> dict:
    g = Graph.load(building_id)
    targets = [n["id"] for n in g.nodes.values() if g.kind(n["id"]) == "pano"]
    ents = [n["id"] for n in g.entrances()]
    front = next((n["id"] for n in g.entrances() if n.get("primary")), None)
    out = {"building_id": building_id, "name": g.data.get("name"), "targets": len(targets),
           "entrances": len(ents), "front_door_marked": front is not None}
    if not ents or not targets:
        out["note"] = "no entrance marked yet"
        return out
    saved, door_differs, fast_vs_short, gain, stretcher_diff, stretcher_none, ms = [], 0, 0, [], 0, 0, []
    reach = 0
    for t in targets:
        t0 = time.perf_counter()
        foot = routing.best_entry(g, t, "foot")
        ms.append((time.perf_counter() - t0) * 1000)
        if not foot["reachable"]:
            continue
        reach += 1
        if front and "front_door" in foot:
            saved.append(foot["front_door"]["seconds_saved"])
            door_differs += not foot["front_door"]["is_best"]
        inside_from = foot["entrance_id"]
        short = routing.shortest_by_meters(g, inside_from, t, "foot")
        fast = routing.route(g, inside_from, t, "foot")
        if short["reachable"] and short["path"] != fast["path"] and short["seconds"] - fast["seconds"] > 0.5:
            fast_vs_short += 1
            gain.append(short["seconds"] - fast["seconds"])
        st = routing.best_entry(g, t, "stretcher")
        if not st["reachable"]:
            stretcher_none += 1
        elif st["entrance_id"] != foot["entrance_id"] or st["path"] != foot["path"]:
            stretcher_diff += 1
    out.update(
        reachable_on_foot=reach,
        best_door_is_not_front_door=door_differs if front else None,
        median_seconds_saved_vs_front_door=round(statistics.median(saved), 1) if saved else None,
        max_seconds_saved_vs_front_door=round(max(saved), 1) if saved else None,
        fastest_differs_from_shortest=fast_vs_short,
        median_seconds_gained_over_shortest=round(statistics.median(gain), 1) if gain else None,
        stretcher_route_differs=stretcher_diff, stretcher_cannot_reach=stretcher_none,
        best_entry_ms_median=round(statistics.median(ms), 3))
    # replanning: block the middle edge of each fastest route and time the new search
    times, lost = [], []
    for t in targets[:: max(1, len(targets) // 40)]:
        foot = routing.best_entry(g, t, "foot")
        path = foot.get("path") or []
        if len(path) < 4:
            continue
        i = len(path) // 2
        r = routing.replan(g, path[i], t, "foot", [[path[i], path[i + 1]]])
        times.append(r["compute_ms"])
        if r["reachable"]:
            lost.append(r["seconds"] - routing.route(g, path[i], t, "foot")["seconds"])
    out.update(replan_ms_median=round(statistics.median(times), 3) if times else None,
               replans_tried=len(times), replans_with_a_way_around=len(lost),
               median_seconds_lost_to_a_block=round(statistics.median(lost), 1) if lost else None)
    return out


if __name__ == "__main__":
    ids = sys.argv[1:] or list_buildings()
    rows = [building_metrics(b) for b in ids]
    for r in rows:
        print(json.dumps(r))
    (config.BUILDINGS / "routing_metrics.json").write_text(json.dumps(rows, indent=1))
