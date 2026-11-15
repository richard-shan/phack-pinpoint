# Graph routing over the toy building (A swaps load_graph for real data later).
import json
import heapq
import math
from functools import lru_cache
from .config import (FIXTURES, WALK_SPEED, VEHICLE_SPEED, DOOR_DELAY, LOCKED_DOOR_DELAY,
                     STAIR_SECONDS_PER_FLIGHT, ELEVATOR_WAIT, ELEVATOR_SECONDS_PER_FLOOR)
from .errors import MatterportUnavailable

IMPASSABLE = float("inf")

# Semantic building_id -> on-disk fixture folder.
_FIXTURE_ALIASES = {
    "bayou-medical-plaza": "building_bayou",
}


def _resolve_fixture_dir(building_id):
    # Try <id>/, then alias, then building_<id>/, else raise.
    candidates = [
        FIXTURES / building_id,
        FIXTURES / _FIXTURE_ALIASES.get(building_id, building_id),
        FIXTURES / f"building_{building_id}",
    ]
    for c in candidates:
        if (c / "graph.json").exists():
            return c
    raise MatterportUnavailable(f"no graph for building {building_id}")


@lru_cache(maxsize=8)
def load_graph(building_id):
    fdir = _resolve_fixture_dir(building_id)
    data = json.loads((fdir / "graph.json").read_text())
    nodes = {n["id"]: n for n in data["nodes"]}
    adj = {nid: [] for nid in nodes}
    for e in data["edges"]:
        adj[e["a"]].append(e)
        adj[e["b"]].append(e)
    return {"nodes": nodes, "adj": adj, "edges": data["edges"]}


def edge_seconds(graph, edge, profile, blocked=None):
    if blocked and _eid(edge) in blocked:
        return IMPASSABLE
    if not edge["passable"].get(profile, False):
        return IMPASSABLE
    t = edge["type"]
    m = edge["meters"]
    if t in ("open", "exterior_foot"):
        return m / WALK_SPEED[profile]
    if t == "exterior_vehicle":
        return m / VEHICLE_SPEED
    if t == "door":
        return m / WALK_SPEED[profile] + DOOR_DELAY[profile]
    if t == "locked_door":
        return m / WALK_SPEED[profile] + LOCKED_DOOR_DELAY[profile]
    if t == "stairs":
        per = STAIR_SECONDS_PER_FLIGHT[profile]
        return IMPASSABLE if per is None else per
    if t == "elevator":
        fa = graph["nodes"][edge["a"]]["floor"]
        fb = graph["nodes"][edge["b"]]["floor"]
        return ELEVATOR_WAIT + ELEVATOR_SECONDS_PER_FLOOR * abs(fa - fb)
    return m / WALK_SPEED[profile]


def _dijkstra(graph, source, profile, blocked=None):
    # Shortest time from source (one pass → every entrance).
    dist = {source: 0.0}
    prev = {}
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, IMPASSABLE):
            continue
        for e in graph["adj"][u]:
            v = e["b"] if e["a"] == u else e["a"]
            w = edge_seconds(graph, e, profile, blocked)
            if w == IMPASSABLE:
                continue
            nd = d + w
            if nd < dist.get(v, IMPASSABLE):
                dist[v] = nd
                prev[v] = (u, e)
                heapq.heappush(pq, (nd, v))
    return dist, prev


def _path(prev, source, target):
    if target != source and target not in prev:
        return None
    seq, cur = [], target
    while cur != source:
        p, e = prev[cur]
        seq.append((cur, e))
        cur = p
    seq.append((source, None))
    seq.reverse()
    return seq


def _meters(graph, seq):
    return sum(e["meters"] for _, e in seq if e)


def _uses(graph, seq, etype):
    return any(e and e["type"] == etype for _, e in seq)


def route(building_id, from_node, to_node, profile, blocked_edges=None):
    """Compute the shortest path from from_node to to_node on the building graph under a stretcher or foot profile. Returns path_nodes, meters, and seconds."""
    g = load_graph(building_id)
    blocked = set(blocked_edges or [])
    dist, prev = _dijkstra(g, from_node, profile, blocked)
    seq = _path(prev, from_node, to_node)
    if seq is None:
        return {"path": [], "seconds": None, "meters": None, "reachable": False}
    return {"path": [n for n, _ in seq], "seconds": round(dist[to_node], 1),
            "meters": round(_meters(g, seq), 1), "reachable": True,
            "uses_stairs": _uses(g, seq, "stairs"), "uses_elevator": _uses(g, seq, "elevator")}


def best_entry(building_id, target, profile):
    # Best door = lowest time from target to any entrance (reverse Dijkstra).
    g = load_graph(building_id)
    dist, prev = _dijkstra(g, target, profile)
    entrances = [nid for nid, n in g["nodes"].items() if n.get("is_entrance")]
    ranked = sorted((e for e in entrances if e in dist), key=lambda e: dist[e])
    if not ranked:
        return {"entrance_id": None, "reachable": False}
    best = ranked[0]
    staging = [nid for nid, n in g["nodes"].items() if n.get("attributes", {}).get("staging")]
    staging_id = min((s for s in staging if s in dist), key=lambda s: dist.get(s, IMPASSABLE),
                     default=None)
    seq = _path(prev, target, best)  # reversed (target->entrance); flip for crew direction
    crew_path = [n for n, _ in seq][::-1] if seq else []
    out = {"entrance_id": best, "staging_id": staging_id,
           "seconds": round(dist[best], 1), "path": crew_path,
           "reachable": True, "runner_up": None}
    if len(ranked) > 1:
        out["runner_up"] = {"entrance_id": ranked[1], "seconds": round(dist[ranked[1]], 1),
                            "delta_seconds": round(dist[ranked[1]] - dist[best], 1)}
    return out


def best_entry_under_belief(building_id, candidates, profile):
    """Pick the responder-staging entrance that minimizes expected graph distance under weighted candidate rooms. Accepts candidates as list of {node_id, belief} dicts; tolerates 'node', 'id', 'room_id' as aliases and defaults a missing belief to uniform. Returns best_entry_id, expected meters, and alternatives."""
    g = load_graph(building_id)
    from .localization import normalize_belief
    # Normalize candidate shapes: the LLM may not match our exact schema.
    _norm = []
    for c in (candidates or []):
        if isinstance(c, str):
            _norm.append({"node_id": c, "belief": 1.0})
            continue
        if not isinstance(c, dict):
            continue
        nid = c.get("node_id") or c.get("node") or c.get("id") or c.get("room_id")
        if not nid or nid not in g["nodes"]:
            continue
        belief = c.get("belief", c.get("score", 1.0))
        try:
            belief = float(belief)
        except (TypeError, ValueError):
            belief = 1.0
        _norm.append({"node_id": nid, "belief": belief})
    if not _norm:
        return {"entrance_id": None, "expected_seconds": None, "spread_seconds": 0.0,
                "all_candidates_same_door": False, "question_worth_asking": True,
                "per_candidate_best_door": {},
                "error": "no valid candidates (need at least node_id matching a graph node)"}
    candidates = _norm
    normalize_belief(candidates)
    entrances = [nid for nid, n in g["nodes"].items() if n.get("is_entrance")]
    exp = {e: 0.0 for e in entrances}
    per_candidate_best = {}
    for c in candidates:
        dist, _ = _dijkstra(g, c["node_id"], profile)
        for e in entrances:
            exp[e] += c["belief"] * dist.get(e, IMPASSABLE)
        reach = {e: dist.get(e, IMPASSABLE) for e in entrances}
        per_candidate_best[c["node_id"]] = min(reach, key=reach.get)
    ranked = sorted((e for e in entrances if exp[e] < IMPASSABLE), key=lambda e: exp[e])
    best = ranked[0]
    spread = (round(exp[ranked[1]] - exp[best], 1) if len(ranked) > 1 else 0.0)
    same_door = len(set(per_candidate_best.values())) == 1
    return {"entrance_id": best, "expected_seconds": round(exp[best], 1),
            "spread_seconds": spread, "all_candidates_same_door": same_door,
            "question_worth_asking": not same_door,
            "per_candidate_best_door": per_candidate_best}


def backup_route(building_id, from_node, to_node, profile, avoid_path):
    # Alternate path: 3x cost on edges from the first route.
    """Compute an alternative path from from_node to to_node that avoids the given nodes. Returns the alternative or None if no path exists."""
    g = load_graph(building_id)
    avoid = set()
    ap = avoid_path or []
    for a, b in zip(ap, ap[1:]):
        avoid.add(frozenset((a, b)))
    dist, prev = _dijkstra_penalized(g, from_node, profile, avoid, penalty=3.0)
    seq = _path(prev, from_node, to_node)
    if seq is None:
        return {"path": [], "seconds": None, "meters": None, "reachable": False}
    return {"path": [n for n, _ in seq], "seconds": round(dist[to_node], 1),
            "meters": round(_meters(g, seq), 1), "reachable": True}


def _dijkstra_penalized(graph, source, profile, avoid_pairs, penalty):
    dist = {source: 0.0}
    prev = {}
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, IMPASSABLE):
            continue
        for e in graph["adj"][u]:
            v = e["b"] if e["a"] == u else e["a"]
            w = edge_seconds(graph, e, profile)
            if w == IMPASSABLE:
                continue
            if frozenset((e["a"], e["b"])) in avoid_pairs:
                w *= penalty
            nd = d + w
            if nd < dist.get(v, IMPASSABLE):
                dist[v] = nd
                prev[v] = (u, e)
                heapq.heappush(pq, (nd, v))
    return dist, prev


def replan(building_id, current_node, to_node, profile, blocked_edges):
    # Door locked mid-run → search again from current_node.
    r = route(building_id, current_node, to_node, profile, blocked_edges=blocked_edges)
    r["replanned_from"] = current_node
    r["blocked_edges"] = list(blocked_edges)
    return r


def exit_route(building_id, from_node, profile):
    # Nearest exit from from_node.
    g = load_graph(building_id)
    dist, prev = _dijkstra(g, from_node, profile)
    entrances = [nid for nid, n in g["nodes"].items() if n.get("is_entrance")]
    reach = {e: dist[e] for e in entrances if e in dist}
    if not reach:
        return {"path": [], "seconds": None, "reachable": False}
    exit_node = min(reach, key=reach.get)
    seq = _path(prev, from_node, exit_node)
    return {"path": [n for n, _ in seq], "seconds": round(dist[exit_node], 1),
            "entrance_id": exit_node, "reachable": True}


def directions(building_id, path):
    # Node ids → turn-by-turn steps with landmarks.
    g = load_graph(building_id)
    nodes = g["nodes"]
    if not path or len(path) < 2:
        return []
    steps, run_m, run_start = [], 0.0, path[0]
    prev_heading = None
    for i in range(len(path) - 1):
        a, b = nodes[path[i]], nodes[path[i + 1]]
        seg = _find_edge(g, path[i], path[i + 1])
        # floor change -> emit its own step
        if a["floor"] != b["floor"] and a["floor"] and b["floor"]:
            steps.append(_flush_run(run_start, nodes[path[i]], run_m))
            via = "elevator" if seg and seg["type"] == "elevator" else "stairs"
            steps.append({"text": f"Take the {via} to floor {b['floor']}.",
                          "meters": 0, "node_id": path[i + 1],
                          "thumbnail_path": b.get("pano_path")})
            run_m, run_start, prev_heading = 0.0, path[i + 1], None
            continue
        heading = _heading(a["xyz"], b["xyz"])
        turn = _turn(prev_heading, heading)
        if turn and run_m > 0:
            steps.append(_flush_run(run_start, nodes[path[i]], run_m, turn))
            run_m, run_start = 0.0, path[i]
        run_m += seg["meters"] if seg else 0.0
        prev_heading = heading
    steps.append(_flush_run(run_start, nodes[path[-1]], run_m, arrive=True))
    return [s for s in steps if s]


def _flush_run(start_node, end_node, meters, turn=None, arrive=False):
    if meters <= 0 and not arrive:
        return None
    landmark = end_node.get("room_label") or end_node.get("attributes", {}).get("room_type", "")
    if arrive:
        text = f"Arrive at {landmark}."
    elif turn:
        text = f"Turn {turn} at {landmark} and continue {round(meters)} m."
    else:
        text = f"Go {round(meters)} m to {landmark}."
    return {"text": text, "meters": round(meters, 1), "node_id": end_node["id"],
            "thumbnail_path": end_node.get("pano_path")}


def _find_edge(g, a, b):
    for e in g["adj"][a]:
        if {e["a"], e["b"]} == {a, b}:
            return e
    return None


def _heading(p, q):
    return math.atan2(q[1] - p[1], q[0] - p[0])


def _turn(prev, cur):
    if prev is None:
        return None
    d = math.degrees((cur - prev + math.pi) % (2 * math.pi) - math.pi)
    if d > 35:
        return "left"
    if d < -35:
        return "right"
    return None


def _eid(edge):
    return f"{edge['a']}--{edge['b']}"
