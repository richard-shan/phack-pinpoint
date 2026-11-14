"""Routing over a building graph. Deterministic; the agent calls it and never does graph math.

Every function takes a `Graph` and returns plain dictionaries. `tools.py` wraps them with
the contract signatures (building_id in, dict out).

Cost is seconds per profile ("foot" or "stretcher"), so fastest and shortest can differ.
An entrance is scored as outside time (arrival point, staging spot, walk to the door) plus
inside time (door to the caller).
"""
from __future__ import annotations

import heapq
import itertools
import math
import time

from .config import PROFILES, ROUTING
from .costs import approach_seconds
from .graph import EXTERIOR, Graph, edge_key

INF = math.inf


# =============================================================== search
def _blocked_keys(blocked) -> frozenset:
    """Accept edge pairs as [a, b], (a, b) or "a|b" and return order-independent keys."""
    out = set()
    for item in blocked or ():
        a, b = item.split("|") if isinstance(item, str) else item
        out.add(edge_key(a, b))
    return frozenset(out)


def _top_speed(profile: str) -> float:
    return max(ROUTING["walk_mps"][profile], ROUTING["vehicle_mps"])


def search(g: Graph, sources: dict[str, float], profile: str, *, goal: str | None = None,
           edge_filter=None, blocked=frozenset(), multiplier: dict | None = None,
           risk: bool = False):
    """Dijkstra from one or more sources; A* when `goal` is given.

    Returns (dist, prev). With a goal the search stops as soon as the goal is settled, and
    the heuristic is straight-line distance over the top speed, which never overestimates.
    `multiplier` scales chosen edges (backup routes). `risk` adds the expected cost of an
    edge turning out blocked: (1 - confidence) * blocked_detour_s.
    """
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}")
    speed = _top_speed(profile)
    goal_xyz = g.xyz(goal) if goal is not None else None

    def h(n: str) -> float:
        if goal_xyz is None:
            return 0.0
        p = g.xyz(n)
        return math.dist(p, goal_xyz) / speed if p else 0.0

    dist = {s: float(c) for s, c in sources.items()}
    prev: dict[str, str] = {}
    done: set[str] = set()
    tie = itertools.count()
    heap = [(c + h(s), next(tie), s) for s, c in dist.items()]
    heapq.heapify(heap)
    while heap:
        _, _, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u == goal:
            break
        for v, e in g.adj[u]:
            if v in done or (edge_filter is not None and not edge_filter(e)):
                continue
            k = edge_key(u, v)
            if k in blocked:
                continue
            c = g.cost(e, profile)
            if c is None:
                continue
            if risk:
                c += (1.0 - float(e.get("confidence", 1.0))) * ROUTING["blocked_detour_s"]
            if multiplier:
                c *= multiplier.get(k, 1.0)
            nd = dist[u] + c
            if nd < dist.get(v, INF):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd + h(v), next(tie), v))
    return dist, prev


def _walk_back(prev: dict[str, str], node: str) -> list[str]:
    """Path from a search source to `node`, following `prev`."""
    path = [node]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def _interior(e: dict) -> bool:
    return e["type"] not in EXTERIOR


def _exterior(e: dict) -> bool:
    return e["type"] in EXTERIOR


def _no_vehicle(e: dict) -> bool:
    return e["type"] != "exterior_vehicle"


def _filter_from(g: Graph, start: str):
    """Edges usable from `start`. Driving is only possible before the crew leaves the
    vehicle, so vehicle edges are open only when the route starts at the arrival point or
    a staging spot. From inside, walking out one door and around to another is allowed."""
    return None if g.kind(start) in ("arrival", "staging") else _no_vehicle


def summarize(g: Graph, path: list[str], profile: str) -> dict:
    """Seconds, meters and edge mix of a node path, using the true (unpenalized) costs."""
    seconds = meters = 0.0
    types = []
    for a, b in zip(path, path[1:]):
        e = g.edge(a, b)
        if e is None:
            raise ValueError(f"path uses a missing edge {a} - {b}")
        c = g.cost(e, profile)
        if c is None:
            raise ValueError(f"path uses edge {a} - {b}, which {profile} cannot cross")
        seconds += c
        meters += float(e.get("meters") or 0.0)
        types.append(e["type"])
    return {"path": list(path), "seconds": round(seconds, 1), "meters": round(meters, 1),
            "uses_stairs": "stairs" in types, "uses_elevator": "elevator" in types,
            "min_confidence": min((float(g.edge(a, b).get("confidence", 1.0))
                                   for a, b in zip(path, path[1:])), default=1.0)}


def _unreachable(from_node: str, to_node: str, profile: str) -> dict:
    return {"path": [], "seconds": None, "meters": None, "uses_stairs": False,
            "uses_elevator": False, "reachable": False,
            "reason": f"no {profile} route from {from_node} to {to_node}"}


def _need(g: Graph, *node_ids: str) -> None:
    for n in node_ids:
        if n not in g.nodes:
            raise KeyError(f"node {n!r} is not in building {g.building_id}")


# =============================================================== single pair
def route(g: Graph, from_node: str, to_node: str, profile: str = "foot", *,
          blocked=(), risk: bool = False) -> dict:
    """Fastest route between two nodes for a profile (A*)."""
    _need(g, from_node, to_node)
    dist, prev = search(g, {from_node: 0.0}, profile, goal=to_node,
                        edge_filter=_filter_from(g, from_node),
                        blocked=_blocked_keys(blocked), risk=risk)
    if to_node not in dist:
        return _unreachable(from_node, to_node, profile)
    out = summarize(g, _walk_back(prev, to_node), profile)
    out["reachable"] = True
    return out


def shortest_by_meters(g: Graph, from_node: str, to_node: str, profile: str = "foot") -> dict:
    """Baseline for the 'fastest against shortest' number: least meters over passable edges."""
    _need(g, from_node, to_node)
    allowed = _filter_from(g, from_node)
    dist = {from_node: 0.0}
    prev: dict[str, str] = {}
    heap = [(0.0, from_node)]
    done = set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        for v, e in g.adj[u]:
            if g.cost(e, profile) is None or (allowed is not None and not allowed(e)):
                continue
            nd = d + float(e.get("meters") or 0.0)
            if nd < dist.get(v, INF):
                dist[v], prev[v] = nd, u
                heapq.heappush(heap, (nd, v))
    if to_node not in dist:
        return _unreachable(from_node, to_node, profile)
    out = summarize(g, _walk_back(prev, to_node), profile)
    out["reachable"] = True
    return out


def backup_route(g: Graph, from_node: str, to_node: str, profile: str = "foot",
                 avoid_path: list[str] | None = None) -> dict:
    """A second route sharing as few edges as possible with `avoid_path`.

    Edges on the avoided route cost `backup_penalty` times more during the search. If that
    still returns the same route, the penalty is raised (x10, then x100) until the route
    changes, so the only shared edges left are ones with no way around them. The reported
    seconds are the real ones. `avoid_path` defaults to the fastest route.
    """
    _need(g, from_node, to_node)
    if avoid_path is None:
        avoid_path = route(g, from_node, to_node, profile)["path"]
    avoid = {edge_key(a, b) for a, b in zip(avoid_path, avoid_path[1:])}
    base = ROUTING["backup_penalty"]
    path, penalty = None, base
    for penalty in (base, base * 10, base * 100):
        dist, prev = search(g, {from_node: 0.0}, profile, goal=to_node,
                            multiplier={k: penalty for k in avoid},
                            edge_filter=_filter_from(g, from_node))
        if to_node not in dist:
            return _unreachable(from_node, to_node, profile)
        path = _walk_back(prev, to_node)
        if path != list(avoid_path):
            break
    out = summarize(g, path, profile)
    shared = [list(k) for k in (edge_key(a, b) for a, b in zip(path, path[1:])) if k in avoid]
    out.update(reachable=True, shared_edges=shared, is_different=path != list(avoid_path),
               penalty_used=penalty)
    return out


def replan(g: Graph, current_node: str, to_node: str, profile: str = "foot",
           blocked_edges=()) -> dict:
    """New route from where the crew is now, with the reported edges treated as impassable."""
    t0 = time.perf_counter()
    out = route(g, current_node, to_node, profile, blocked=blocked_edges)
    out["blocked_edges"] = [list(k) for k in sorted(_blocked_keys(blocked_edges))]
    out["compute_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return out


# =============================================================== entrances
def _outside(g: Graph, profile: str, blocked=frozenset()):
    """Outside time from the arrival point to every node over exterior edges only.

    Sources are the nodes of kind "arrival". Without one, every staging spot is a
    zero-cost source. Without staging spots either, every entrance costs zero outside.
    """
    arrivals = [n["id"] for n in g.of_kind("arrival")]
    sources = arrivals or [n["id"] for n in g.of_kind("staging")]
    if not sources:
        dist, prev = {n["id"]: 0.0 for n in g.entrances()}, {}
    else:
        dist, prev = search(g, {s: 0.0 for s in sources}, profile, edge_filter=_exterior,
                            blocked=blocked)
        for n in g.entrances():          # an entrance reached by an unscanned approach has
            if n.get("approach"):        # no outside link of its own
                dist.setdefault(n["id"], 0.0)
    for n in g.entrances():
        if n["id"] in dist:
            extra = approach_seconds(n, profile)
            if extra is None:
                del dist[n["id"]]        # this profile cannot make the approach
            else:
                dist[n["id"]] += extra
    return dist, prev


def _staging_on(g: Graph, outside_path: list[str]) -> str | None:
    """The last staging spot on an outside path: where the vehicle stops."""
    for n in reversed(outside_path):
        if g.kind(n) == "staging":
            return n
    return None


def _entry_table(g: Graph, targets: list[str], profile: str, blocked=frozenset()):
    """outside[e], inside[t][e] and the search trees behind them, for every entrance e."""
    out_dist, out_prev = _outside(g, profile, blocked)
    inside, inside_prev = {}, {}
    for t in targets:
        inside[t], inside_prev[t] = search(g, {t: 0.0}, profile, edge_filter=_interior,
                                           blocked=blocked)
    ents = [n["id"] for n in g.entrances() if n["id"] in out_dist]
    return ents, out_dist, out_prev, inside, inside_prev


def _entry_path(g, e, target, out_prev, inside_prev) -> list[str]:
    """Arrival point (or staging spot) to the door `e`, then inside to the target."""
    outside_path = _walk_back(out_prev, e)                    # arrival ... e
    inside_path = _walk_back(inside_prev[target], e)[::-1]    # e ... target
    return outside_path + inside_path[1:]


def best_entry(g: Graph, target: str, profile: str = "foot", *, blocked=()) -> dict:
    """The entrance with the lowest outside plus inside time to the target.

    One search outward from the target covers every door at once. `ranked` lists every
    usable entrance, and `front_door` compares against the entrance marked `primary`
    (the one at the street address).
    """
    _need(g, target)
    blk = _blocked_keys(blocked)
    ents, out_dist, out_prev, inside, inside_prev = _entry_table(g, [target], profile, blk)
    ranked = []
    for e in ents:
        if e not in inside[target]:
            continue
        o, i = out_dist[e], inside[target][e]
        ranked.append({"entrance_id": e, "label": g.label(e), "seconds": round(o + i, 1),
                       "outside_seconds": round(o, 1), "inside_seconds": round(i, 1),
                       "staging_id": _staging_on(g, _walk_back(out_prev, e)),
                       "approach": g.nodes[e].get("approach")})
    ranked.sort(key=lambda r: (r["seconds"], r["entrance_id"]))
    if not ranked:
        out = {"entrance_id": None, "staging_id": None, "seconds": None, "path": [],
               "runner_up": None, "ranked": [], "reachable": False, "target": target,
               "profile": profile, "reason": f"no entrance reaches {target} for {profile}"}
        if profile != "foot":
            out["carry"] = _carry_point(g, target, profile, blk)
        return out
    best = ranked[0]
    path = _entry_path(g, best["entrance_id"], target, out_prev, inside_prev)
    out = summarize(g, path, profile)
    out["scanned_seconds"] = out["seconds"]      # the part of the time that is in the scan
    runner_up = dict(ranked[1], delta_seconds=round(ranked[1]["seconds"] - best["seconds"], 1)) \
        if len(ranked) > 1 else None
    out.update(best, reachable=True, target=target, profile=profile,
               scope=g.data.get("scope"), runner_up=runner_up, ranked=ranked,
               unusable_entrances=sorted(n["id"] for n in g.entrances()
                                         if n["id"] not in {r["entrance_id"] for r in ranked}))
    front = next((r for r in ranked if g.nodes[r["entrance_id"]].get("primary")), None)
    if front is not None:
        out["front_door"] = dict(front, seconds_saved=round(front["seconds"] - best["seconds"], 1),
                                 is_best=front["entrance_id"] == best["entrance_id"])
    return out


def _carry_point(g: Graph, target: str, profile: str, blocked=frozenset()) -> dict | None:
    """How close a wheeled profile can get when it cannot reach the target.

    Of all the points the profile can reach from an entrance, pick the one with the least
    remaining time on foot to the target. The crew leaves the stretcher there and carries.
    """
    ents, out_dist, out_prev, _, _ = _entry_table(g, [], profile, blocked)
    if not ents:
        return None
    reach, reach_prev = search(g, {e: out_dist[e] for e in ents}, profile,
                               edge_filter=_interior, blocked=blocked)
    on_foot, foot_prev = search(g, {target: 0.0}, "foot", edge_filter=_interior, blocked=blocked)
    both = [n for n in reach if n in on_foot]
    if not both:
        return None
    # The crew walks the carry twice, in without the patient and out with them, so a second
    # of carry counts double against a second of wheeling the stretcher closer.
    node = min(both, key=lambda n: (reach[n] + 2.0 * on_foot[n], on_foot[n], n))
    wheeled = _walk_back(reach_prev, node)                 # entrance ... carry point
    carried = _walk_back(foot_prev, node)[::-1]            # carry point ... target
    # What stops the stretcher: the first link on the carry that this profile cannot cross.
    links = [g.edge(a, b) for a, b in zip(carried, carried[1:])]
    first = next((e for e in links if e is not None and g.cost(e, profile) is None), links[0] if links else None)
    rise = (g.xyz(target) or (0, 0, 0))[2] - (g.xyz(node) or (0, 0, 0))[2]
    return {"node_id": node, "label": g.label(node), "room": g.room(node),
            "floor": g.nodes[node].get("floor"), "entrance_id": wheeled[0],
            "seconds_to_carry_point": round(reach[node], 1),
            "carry_seconds_on_foot": round(on_foot[node], 1),
            "blocked_by": first["type"] if first else None,
            "blocked_width_m": first.get("width_m") if first else None,
            "carry_direction": "up" if rise > 1.0 else "down" if rise < -1.0 else "level",
            "carry_uses_stairs": any((g.edge(a, b) or {}).get("type") == "stairs"
                                     for a, b in zip(carried, carried[1:])),
            "path_to_carry_point": _walk_back(out_prev, wheeled[0]) + wheeled[1:],
            "carry_path": carried}


def exit_route(g: Graph, from_node: str, profile: str = "foot", *, blocked=()) -> dict:
    """Fastest way out: the entrance with the least inside time from `from_node`."""
    _need(g, from_node)
    dist, prev = search(g, {from_node: 0.0}, profile, edge_filter=_interior,
                        blocked=_blocked_keys(blocked))
    exits = [(dist[n["id"]], n["id"]) for n in g.entrances() if n["id"] in dist]
    if not exits:
        return {"path": [], "seconds": None, "entrance_id": None, "reachable": False,
                "reason": f"no {profile} exit from {from_node}"}
    _, e = min(exits)
    out = summarize(g, _walk_back(prev, e), profile)
    # Every exit, nearest first, with how it leaves the scanned area. On an upper floor the
    # nearest exit may be the elevators; in a fire the caller should be sent to a stair.
    ranked = [{"entrance_id": n, "label": g.label(n), "seconds": round(d, 1),
               "leads_to": (g.nodes[n].get("approach") or {}).get("via") or "outside"}
              for d, n in sorted(exits)]
    out.update(entrance_id=e, label=g.label(e), reachable=True, ranked=ranked,
               nearest_stair_or_outside=next((r for r in ranked if r["leads_to"] != "elevator"), None))
    return out


# =============================================================== uncertainty
def _normalize(candidates: list[dict]) -> list[tuple[str, float]]:
    """[(node_id, probability)] from candidates carrying `belief` (lane C's updated
    belief), `p`, or a raw match `score`, in that order of preference."""
    pairs = [(c["node_id"], float(next((c[k] for k in ("belief", "p", "score") if c.get(k) is not None), 0.0)))
             for c in candidates]
    total = sum(max(p, 0.0) for _, p in pairs)
    if not pairs or total <= 0:
        raise ValueError("candidates need positive p or score values")
    merged: dict[str, float] = {}
    for n, p in pairs:
        merged[n] = merged.get(n, 0.0) + max(p, 0.0) / total
    return sorted(merged.items(), key=lambda kv: -kv[1])


def _tour_expected(order, probs, first_leg, hop) -> float:
    """Expected seconds to reach the caller when candidates are checked in `order`."""
    t, expected = first_leg[order[0]], 0.0
    for i, c in enumerate(order):
        if i:
            t += hop[order[i - 1]][c]
        expected += probs[c] * t
    return expected


def best_entry_under_belief(g: Graph, candidates: list[dict], profile: str = "foot", *,
                            max_candidates: int = 5) -> dict:
    """Which door, and which candidate room first, when the location is still uncertain.

    The crew enters one door and checks candidates in some order until it finds the
    caller. For every entrance and every order we compute the expected time to reach the
    caller under the belief, and return the best. `top_guess` is the baseline policy:
    the best door for the most likely candidate, then the rest by falling probability.
    `value_of_information_seconds` is how much faster the crew would be if the room were
    known for certain; above `ask_threshold_s` it is worth asking the caller again.
    """
    belief = _normalize(candidates)
    dropped = belief[max_candidates:]
    belief = belief[:max_candidates]
    mass = sum(p for _, p in belief)
    probs = {n: p / mass for n, p in belief}
    nodes = list(probs)
    _need(g, *nodes)
    ents, out_dist, out_prev, inside, inside_prev = _entry_table(g, nodes, profile)
    total = {e: {c: out_dist[e] + inside[c][e] for c in nodes if e in inside[c]} for e in ents}
    usable = [e for e in ents if len(total[e]) == len(nodes)]
    hop = {a: {b: inside[a].get(b, INF) for b in nodes} for a in nodes}
    if not usable or any(hop[a][b] == INF for a in nodes for b in nodes):
        return {"entrance_id": None, "expected_seconds": None, "spread_seconds": None,
                "reachable": False,
                "reason": f"no single entrance reaches every candidate for {profile}"}

    plans = []
    for e in usable:
        order = min(itertools.permutations(nodes),
                    key=lambda o: (_tour_expected(o, probs, total[e], hop), o))
        plans.append((_tour_expected(order, probs, total[e], hop), e, list(order)))
    plans.sort()
    expected, entrance, order = plans[0]

    known = sum(probs[c] * min(total[e][c] for e in usable) for c in nodes)
    top = nodes[0]
    top_door = min(usable, key=lambda e: (total[e][top], e))
    top_order = [top] + [c for c in nodes if c != top]      # nodes are sorted by probability
    top_expected = _tour_expected(top_order, probs, total[top_door], hop)
    direct = [total[entrance][c] for c in nodes]
    voi = expected - known
    path = _entry_path(g, entrance, order[0], out_prev, inside_prev)
    return {
        "entrance_id": entrance, "label": g.label(entrance),
        "staging_id": _staging_on(g, _walk_back(out_prev, entrance)),
        "visit_order": order,
        "expected_seconds": round(expected, 1),
        "spread_seconds": round(max(direct) - min(direct), 1),
        "value_of_information_seconds": round(voi, 1),
        "ask_another_question": voi > ROUTING["ask_threshold_s"],
        "question_worth_asking": voi > ROUTING["ask_threshold_s"],       # the name lane C reads
        "all_candidates_same_door": len({min(usable, key=lambda e: (total[e][c], e)) for c in nodes}) == 1,
        "per_candidate_best_door": {c: min(usable, key=lambda e: (total[e][c], e)) for c in nodes},
        "top_guess": {"node_id": top, "entrance_id": top_door, "visit_order": top_order,
                      "expected_seconds": round(top_expected, 1),
                      "seconds_lost_vs_belief": round(top_expected - expected, 1)},
        "per_candidate": [{"node_id": c, "p": round(probs[c], 4),
                           "seconds_via_entrance": round(total[entrance][c], 1),
                           "best_entrance_id": min(usable, key=lambda e: (total[e][c], e)),
                           "best_seconds": round(min(total[e][c] for e in usable), 1)}
                          for c in nodes],
        "ranked": [{"entrance_id": e, "expected_seconds": round(x, 1), "visit_order": o}
                   for x, e, o in plans],
        "first_leg": summarize(g, path, profile),
        "dropped_candidates": [n for n, _ in dropped],
        "reachable": True, "profile": profile,
    }


# =============================================================== directions
TURN_MIN_DEG = 40.0        # smaller changes of heading are part of the same run
SHORT_LEAD_M = 1.5         # a shorter run than this is folded into the turn that follows


def _heading(g: Graph, a: str, b: str):
    """Unit (x, y) direction of travel from a to b, or None without coordinates."""
    pa, pb = g.xyz(a), g.xyz(b)
    if not pa or not pb:
        return None
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-6 else None


def _turn_deg(h1, h2) -> float:
    """Signed turn from heading h1 to h2: positive is left. Axes are x east, y north, z up."""
    cross = h1[0] * h2[1] - h1[1] * h2[0]
    dot = h1[0] * h2[0] + h1[1] * h2[1]
    return math.degrees(math.atan2(cross, dot))


def _turn_word(deg: float) -> str | None:
    if abs(deg) < TURN_MIN_DEG:
        return None
    side = "left" if deg > 0 else "right"
    return f"turn sharply {side}" if abs(deg) >= 135 else f"turn {side}"


def _deg(heading) -> float | None:
    return None if heading is None else round(math.degrees(math.atan2(heading[1], heading[0])) % 360, 1)


def _floor_name(g: Graph, node_id: str) -> str:
    n = g.nodes[node_id]
    if n.get("floor_label"):
        return str(n["floor_label"])
    offset = int(g.data.get("floor_offset", 0) or 0)
    seq = int(n.get("floor", 0))
    floors = g.data.get("floors") or []
    if not offset:              # a suite scan with an offset has "Floor 1" for, say, floor 14
        for f in floors:
            if f.get("floor") == seq and f.get("label"):
                label = str(f["label"])
                return label[:1].lower() + label[1:] if label.lower().startswith("floor") else label
    # Graphs built here count scan floors from 0. A graph from another lane that numbers
    # its indoor floors from 1 (outside nodes at 0) already holds building floor numbers.
    indoor = [int(m.get("floor", 0)) for m in g.nodes.values() if g.kind(m["id"]) == "pano"]
    one_based = not floors and bool(indoor) and min(indoor) >= 1
    return f"floor {seq + offset + (0 if one_based else 1)}"


def _meters_text(m: float) -> str:
    return f"{max(1, round(m))} m"


def _room_phrase(room: str | None) -> str | None:
    """"Hallway" -> "the hallway"; names with a number ("Suite 210") are kept as they are."""
    if not room:
        return None
    room = room.strip()
    if any(ch.isdigit() for ch in room) or room.lower().startswith("the "):
        return room
    return "the " + room.lower()


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def directions(g: Graph, path: list[str]) -> list[dict]:
    """Turn a node path into steps a crew can follow.

    Runs that keep roughly one heading merge into a single step with a distance. Left and
    right come from geometry (the change of heading against the run so far), never from a
    language model. Floor changes are stated explicitly. Named places come from node
    labels and room names. Each step names the node where it starts and the heading of
    travel there (degrees counterclockwise from +x), so a panorama crop can face that way.
    """
    _need(g, *path)
    if not path:
        return []
    legs = []
    for a, b in zip(path, path[1:]):
        e = g.edge(a, b)
        if e is None:
            raise ValueError(f"path uses a missing edge {a} - {b}")
        legs.append({"a": a, "b": b, "type": e["type"], "meters": float(e.get("meters") or 0.0),
                     "heading": _heading(g, a, b)})

    steps: list[dict] = []
    ap = g.nodes[path[0]].get("approach") if g.nodes[path[0]].get("is_entrance") else None
    if ap and ap.get("via") in ("elevator", "stairs"):
        how = {"elevator": "Take the building elevator", "stairs": "Climb the building stairs"}.get(
            ap.get("via"), "Make your way")
        steps.append({"kind": "approach", "node_id": path[0], "to_node_id": path[0],
                      "text": f"{how} to {_floor_name(g, path[0])}"
                              f"{' (' + ap['note'] + ')' if ap.get('note') else ''}. "
                              "This leg is not in the scan.",
                      "meters": float(ap.get("walk_m") or 0.0), "heading_deg": None,
                      "assumed": True})
    cur = None                 # the walking step being extended
    carry_heading = None       # heading to compare the next walking leg against

    def flush():
        nonlocal cur
        if cur is not None:
            steps.append(cur)
            cur = None

    def run_heading():
        """Direction of the current run: its start to where we are now, once it is long
        enough to be stable; before that, the heading of the last leg."""
        pa, pb = g.xyz(cur["run_start"]), g.xyz(cur["to_node_id"])
        if pa and pb and math.hypot(pb[0] - pa[0], pb[1] - pa[1]) >= 1.0:
            n = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
            return ((pb[0] - pa[0]) / n, (pb[1] - pa[1]) / n)
        return cur["last_heading"]

    i = 0
    while i < len(legs):
        leg = legs[i]
        kind = leg["type"]
        if kind in ("stairs", "elevator"):
            flush()
            j = i
            while j + 1 < len(legs) and legs[j + 1]["type"] == kind:
                j += 1
            start, end = leg["a"], legs[j]["b"]
            up = (g.nodes[end].get("floor", 0), (g.xyz(end) or (0, 0, 0))[2]) >= \
                 (g.nodes[start].get("floor", 0), (g.xyz(start) or (0, 0, 0))[2])
            name = g.label(start) or ("the stairs" if kind == "stairs" else "the elevator")
            same_floor = g.nodes[end].get("floor", 0) == g.nodes[start].get("floor", 0)
            where = "" if (kind == "stairs" and same_floor) else f" to {_floor_name(g, end)}"
            if kind == "stairs" and same_floor and name == "the stairs":
                name = "the steps"                      # a flight or a few steps within one floor
            steps.append({"kind": kind, "node_id": start, "to_node_id": end, "up": up, "name": name,
                          "text": f"Take {name} {'up' if up else 'down'}{where}.",
                          "meters": sum(l["meters"] for l in legs[i:j + 1]),
                          "heading_deg": _deg(leg["heading"])})
            # An elevator ride has no direction of travel to turn from: the stops sit a few
            # centimetres apart on the plan, and that noise must not become "turn sharply left".
            carry_heading = None if kind == "elevator" else legs[j]["heading"]
            i = j + 1
            continue
        if kind == "exterior_vehicle":
            flush()
            steps.append({"kind": "drive", "node_id": leg["a"], "to_node_id": leg["b"],
                          "text": f"Drive {_meters_text(leg['meters'])} and stage at "
                                  f"{g.label(leg['b']) or 'the staging spot'}.",
                          "meters": leg["meters"], "heading_deg": _deg(leg["heading"])})
            carry_heading = None
            i += 1
            continue

        outside = kind == "exterior_foot"
        base = run_heading() if cur is not None else carry_heading
        turn = _turn_word(_turn_deg(base, leg["heading"])) if base and leg["heading"] else None
        if cur is not None and cur["outside"] != outside:
            flush()
            turn = None
        if cur is not None and turn and cur["meters"] < SHORT_LEAD_M and not cur["turn"]:
            cur["turn"] = turn                     # fold a very short lead-in into this turn
            cur["turn_node"] = leg["a"]
            cur["run_start"] = leg["a"]
        elif cur is None or turn:
            flush()
            prev = steps[-1] if steps else None
            cur = {"kind": "go", "node_id": leg["a"], "to_node_id": leg["b"], "turn": turn,
                   "turn_node": leg["a"], "run_start": leg["a"], "meters": 0.0, "doors": 0,
                   "locked": 0, "passed": [], "outside": outside,
                   "heading_deg": _deg(leg["heading"]), "prev": prev, "last_heading": None}
        else:
            mid = g.label(leg["a"])                # a named place passed without turning
            if mid and mid not in cur["passed"]:
                cur["passed"].append(mid)
        cur["meters"] += leg["meters"]
        cur["to_node_id"] = leg["b"]
        cur["doors"] += kind == "door"
        cur["locked"] += kind == "locked_door"
        if leg["heading"] is not None:
            cur["last_heading"] = leg["heading"]
        i += 1
    flush()

    last_walk = max((n for n, s in enumerate(steps) if s["kind"] == "go"), default=-1)
    for n, s in enumerate(steps):              # texts first: each one reads the step before it
        if s["kind"] == "go":
            s["text"] = _go_text(g, s, first=n == 0 or steps[n - 1]["kind"] == "approach",
                                 last=n == last_walk and n == len(steps) - 1)
    for s in steps:
        if s["kind"] == "go" and s["turn"]:
            s["kind"] = "turn"
        for k in ("turn", "turn_node", "run_start", "doors", "locked", "passed", "outside",
                  "prev", "last_heading", "up", "name"):
            s.pop(k, None)
        s["meters"] = round(s["meters"], 1)
        s["floor"] = g.nodes[s["node_id"]].get("floor")
        s["thumbnail_path"] = g.nodes[s["node_id"]].get("pano_path")
    end = path[-1]
    where = g.label(end) or _room_phrase(g.room(end))
    seen = g.landmark(end) if not g.label(end) else None
    arrive = f"Arrive at {where}" if where else "Arrive at the caller's position"
    steps.append({"kind": "arrive", "node_id": end, "to_node_id": end,
                  "text": arrive + (f". Look for {seen}." if seen else "."),
                  "meters": 0, "heading_deg": None, "floor": g.nodes[end].get("floor"),
                  "thumbnail_path": g.nodes[end].get("pano_path")})
    return steps


def _go_text(g: Graph, s: dict, first: bool, last: bool) -> str:
    start, dest = s["node_id"], s["to_node_id"]
    here = g.label(start)
    prev = s["prev"]
    if s["outside"]:
        return f"Walk {_meters_text(s['meters'])} to {g.label(dest) or 'the entrance'}."

    came_in = bool(g.nodes[start].get("is_entrance")) and (
        first or prev["kind"] in ("drive", "approach") or bool(prev.get("outside")))
    if came_in:
        if g.kind(start) == "elevator":                    # the way in to an upper-floor scan
            lead = f"Leave {here}" if here else "Leave the elevator"
        else:
            lead = f"Enter through {here}" if here else "Enter"
        if (g.nodes[start].get("approach") or {}).get("locked_entry"):
            lead += " (expect it locked from this side)"
    elif prev is not None and prev["kind"] == "stairs":
        named = prev["name"] != "the stairs"
        lead = f"Leave {prev['name']}" if named else \
            f"At the {'top' if prev['up'] else 'bottom'} of the stairs"
    elif prev is not None and prev["kind"] == "elevator":
        lead = f"Leave {prev['name']}"
    elif first:
        lead = f"Start at {here}" if here else "Start"
    else:
        lead = None

    turn = s["turn"]
    if turn:
        spot = g.label(s["turn_node"]) or g.landmark(s["turn_node"])
        turn_text = f"{turn} at {spot}" if spot and lead is None else turn
        lead = f"{lead}, {turn_text}" if lead else _cap(turn_text)
    elif lead is None:
        lead = "Continue"

    text = f"{lead}, go {_meters_text(s['meters'])}"
    doors = s["doors"] - (1 if came_in else 0)          # the entrance itself is the door
    if s["locked"]:
        text += " through a locked door"
    elif doors > 0:
        text += " through the door" if doors == 1 else f" through {doors} doors"
    if s["passed"]:
        text += f" past {s['passed'][-1]}"
    named_dest = g.label(dest)
    room_here, room_there = g.room(start), g.room(dest)
    if last:
        pass                                            # the arrive step names the place
    elif named_dest:
        text += f" to {named_dest}"
    elif room_there and room_there != room_here:
        text += f" into {_room_phrase(room_there)}"
    return text + "."


# =============================================================== node lookups
# Lane B's vocabulary is the canonical one; these are the other spellings in use.
EQUIPMENT_ALIASES = {"extinguisher": "fire_extinguisher", "fire_alarm_pull": "fire_alarm_pull_station",
                     "alarm_pull_station": "fire_alarm_pull_station", "pull_station": "fire_alarm_pull_station",
                     "fire_alarm_panel": "alarm_panel"}


def _equipment_name(name: str) -> str:
    key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    return EQUIPMENT_ALIASES.get(key, key)


def find_equipment(g: Graph, kind: str, near_node: str, profile: str = "foot",
                   limit: int = 5) -> list[dict]:
    """Nodes whose `equipment` lists `kind` (for example "aed"), nearest by travel time."""
    _need(g, near_node)
    dist, prev = search(g, {near_node: 0.0}, profile, edge_filter=_filter_from(g, near_node))
    want = _equipment_name(kind)
    hits = []
    for n in g.nodes.values():
        if n["id"] not in dist:
            continue
        # One hit per node: the same item can be recorded twice (by hand as a bare name,
        # by lane B with a crop). Prefer the record that carries evidence.
        items = [i for i in n.get("equipment") or []
                 if _equipment_name(i if isinstance(i, str) else i.get("type", "")) == want]
        if not items:
            continue
        item = max(items, key=lambda i: isinstance(i, dict) and bool(i.get("evidence_path")))
        s = summarize(g, _walk_back(prev, n["id"]), profile)
        hits.append({"node_id": n["id"], "meters": s["meters"], "seconds": s["seconds"],
                     "floor": n.get("floor"), "label": g.label(n["id"]),
                     "note": item.get("note") if isinstance(item, dict) else None,
                     "evidence_path": (item.get("evidence_path") if isinstance(item, dict)
                                       else None) or n.get("pano_path")})
    return sorted(hits, key=lambda h_: h_["seconds"])[:limit]


def hazards_along(g: Graph, path: list[str]) -> list[dict]:
    """Hazards recorded on the nodes of a path, plus locked doors and low-confidence edges."""
    _need(g, *path)
    out = []
    for n in path:
        node = g.nodes[n]
        for hz in (node.get("attributes") or {}).get("hazards") or []:
            item = {"type": hz} if isinstance(hz, str) else dict(hz)
            out.append({"node_id": n, "type": item.get("type"), "note": item.get("note"),
                        "seen_in_scan": True,              # something in view here, not a property of the route
                        "evidence_path": item.get("evidence_path") or node.get("pano_path")})
    for a, b in zip(path, path[1:]):
        e = g.edge(a, b)
        if e is None:
            continue
        if e["type"] == "locked_door":
            out.append({"node_id": a, "type": "locked_door", "evidence_path": e.get("evidence_path")})
        elif float(e.get("confidence", 1.0)) < 0.7 or e.get("assumed"):
            if e["type"] == "door" and any(g.nodes[n].get("is_entrance") and g.nodes[n].get("confirmed")
                                           for n in (a, b)):
                continue                                   # a marked door that somebody checked
            out.append({"node_id": a, "type": f"unconfirmed_{e['type']}",
                        "evidence_path": e.get("evidence_path")})
    return out
