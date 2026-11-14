"""The tools the agent lane calls. Signatures follow the contract (plan, sections 4A and 5).

Every tool takes plain values and returns plain dictionaries or lists. A bad argument
comes back as {"error": "..."} instead of an exception, so an agent can read it and retry.
The building id is the CoStar PropertyId as a string; "toy" is the hand-made test building.

    python -m env.tools            # a worked example on the toy building
"""
from __future__ import annotations

import functools
import json
import math
import time

from . import config, costar, routing
from .costs import fill_costs
from .graph import Graph, edge_key, graph_path, list_buildings as _list_buildings


def _tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError:
            return {"error": f"no graph for that building; known buildings: {_list_buildings()}"}
        except (KeyError, ValueError, RuntimeError) as exc:
            return {"error": str(exc).strip("'\"")}
    return wrapper


# ------------------------------------------------------------------ records
@_tool
def get_property(address: str) -> dict:
    """address (or PropertyId, or Matterport model id) -> {building_id, use_type, stories,
    year_built, sqft, tenants[], contact, ...}. Each value keeps its CoStar record id."""
    return costar.get_property(address)


@_tool
def get_tenants(building_id: str, floor: int | None = None, suite: str | None = None) -> list[dict]:
    """Current tenants, optionally narrowed to a floor number or a suite."""
    return costar.get_tenants(building_id, floor, suite)


@_tool
def list_buildings() -> list[dict]:
    """Buildings that have a graph, with what routing can and cannot do in each."""
    out = []
    for b in _list_buildings():
        g = Graph.load(b)
        out.append({"building_id": b, "name": g.data.get("name"), "address": g.data.get("address"),
                    "matterport_model_id": g.data.get("matterport_model_id"),
                    "scope": g.data.get("scope"), "floor_offset": g.data.get("floor_offset", 0),
                    "nodes": len(g.nodes), "floors": len(g.data.get("floors") or []) or None,
                    "entrances": len(g.entrances()),
                    "has_elevator": any(e["type"] == "elevator" for e in g.edges.values()),
                    "has_stairs": any(e["type"] == "stairs" for e in g.edges.values())})
    return out


# ------------------------------------------------------------------ the environment
def _find_graph(node_id: str, building_id: str | None) -> Graph:
    if building_id is not None:
        return Graph.load(str(building_id))
    for b in _list_buildings():
        g = Graph.load(b)
        if node_id in g.nodes:
            return g
    raise KeyError(f"node {node_id} is not in any building")


@_tool
def look(node_id: str, building_id: str | None = None) -> dict:
    """What is at a node: {image_path, attributes, neighbors[]}. `image_path` is a full
    panorama in world axes (center looks along +x), rendered on first use; it is None for
    nodes without a panorama, such as doors and staging spots."""
    g = _find_graph(node_id, building_id)
    if node_id not in g.nodes:
        raise KeyError(f"node {node_id} is not in building {g.building_id}")
    n = g.nodes[node_id]
    image = None
    if n.get("pano_path") and g.kind(node_id) == "pano" and g.data.get("matterport_model_id"):
        from . import views
        image = str(views.pano_file(g.data, n).relative_to(config.ROOT))
    here = g.xyz(node_id)
    neighbors = []
    for other, e in g.adj[node_id]:
        there = g.xyz(other)
        heading = None
        if here and there and math.hypot(there[0] - here[0], there[1] - here[1]) > 1e-6:
            heading = round(math.degrees(math.atan2(there[1] - here[1], there[0] - here[0])) % 360, 1)
        neighbors.append({"node_id": other, "type": e["type"], "meters": e.get("meters"),
                          "heading_deg": heading, "floor": g.nodes[other].get("floor"),
                          "label": g.label(other), "room": g.room(other)})
    return {"node_id": node_id, "building_id": g.building_id, "image_path": image,
            "attributes": dict(n.get("attributes") or {}, floor=n.get("floor"), kind=g.kind(node_id),
                               room=g.room(node_id), label=g.label(node_id), xyz=n.get("xyz"),
                               latlon=n.get("latlon"), is_entrance=bool(n.get("is_entrance")),
                               equipment=n.get("equipment") or []),
            "neighbors": neighbors}


@_tool
def entrances(building_id: str) -> list[dict]:
    """[{entrance_id, node_id, side, latlon, ...}] for every entrance of a building."""
    g = Graph.load(str(building_id))
    return [{"entrance_id": n["id"], "node_id": n["id"], "side": n.get("side"),
             "latlon": n.get("latlon"), "label": g.label(n["id"]), "floor": n.get("floor"),
             "primary": bool(n.get("primary")), "confirmed": n.get("confirmed", True),
             "width_m": (n.get("attributes") or {}).get("door_width_m"),
             "evidence_path": n.get("evidence_path")} for n in g.entrances()]


# ------------------------------------------------------------------ routing (section 4A)
@_tool
def route(building_id: str, from_node: str, to_node: str, profile: str = "foot") -> dict:
    """-> {path[], seconds, meters, uses_stairs, uses_elevator, reachable}"""
    return routing.route(Graph.load(str(building_id)), from_node, to_node, profile)


@_tool
def best_entry(building_id: str, target: str, profile: str = "foot") -> dict:
    """-> {entrance_id, staging_id, seconds, path[], runner_up, ranked[], front_door}.
    For a stretcher that cannot reach the target, `carry` says how close it can get."""
    return routing.best_entry(Graph.load(str(building_id)), target, profile)


@_tool
def best_entry_under_belief(building_id: str, candidates: list[dict], profile: str = "foot") -> dict:
    """candidates: [{node_id, p}] or [{node_id, score}] ->
    {entrance_id, visit_order[], expected_seconds, spread_seconds,
     value_of_information_seconds, ask_another_question, top_guess{...}}"""
    return routing.best_entry_under_belief(Graph.load(str(building_id)), candidates, profile)


@_tool
def backup_route(building_id: str, from_node: str, to_node: str, profile: str = "foot",
                 avoid_path: list[str] | None = None) -> dict:
    """-> {path[], seconds, shared_edges[], is_different}"""
    return routing.backup_route(Graph.load(str(building_id)), from_node, to_node, profile, avoid_path)


@_tool
def replan(building_id: str, current_node: str, to_node: str, profile: str = "foot",
           blocked_edges: list | None = None) -> dict:
    """blocked_edges: [[a, b], ...] -> {path[], seconds, compute_ms, reachable}"""
    return routing.replan(Graph.load(str(building_id)), current_node, to_node, profile,
                          blocked_edges or [])


@_tool
def directions(building_id: str, path: list[str]) -> list[dict]:
    """-> [{text, meters, node_id, thumbnail_path, heading_deg, floor, kind}]"""
    return routing.directions(Graph.load(str(building_id)), path)


@_tool
def exit_route(building_id: str, from_node: str, profile: str = "foot") -> dict:
    """-> {path[], seconds, entrance_id}"""
    return routing.exit_route(Graph.load(str(building_id)), from_node, profile)


@_tool
def hazards_along(building_id: str, path: list[str]) -> list[dict]:
    """-> [{node_id, type, evidence_path}]"""
    return routing.hazards_along(Graph.load(str(building_id)), path)


@_tool
def find_equipment(building_id: str, type: str, near_node: str) -> list[dict]:
    """-> [{node_id, meters, seconds, evidence_path}] nearest first"""
    return routing.find_equipment(Graph.load(str(building_id)), type, near_node)


@_tool
def report_edge(building_id: str, a: str, b: str, status: str = "blocked", note: str | None = None) -> dict:
    """Record what a crew found on the edge a-b so the next incident starts with it.

    status: "blocked" (nobody passes), "locked" (passable with a long delay) or "clear"
    (undo). Written to graph.json now and to overrides.json so a rebuild keeps it.
    """
    if status not in ("blocked", "locked", "clear"):
        raise ValueError('status must be "blocked", "locked" or "clear"')
    path = graph_path(str(building_id))
    data = json.loads(path.read_text())
    k = edge_key(a, b)
    edge = next((e for e in data["edges"] if edge_key(e["a"], e["b"]) == k), None)
    if edge is None:
        raise KeyError(f"no edge between {a} and {b}")
    patch = {"a": k[0], "b": k[1], "reported_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note}
    if status == "blocked":
        patch["blocked"] = True
    elif status == "locked":
        patch.update(blocked=False, type="locked_door")
    else:
        patch.update(blocked=False, type=edge.get("type_before_report", edge["type"]))
    if status == "locked" and edge["type"] != "locked_door":
        patch["type_before_report"] = edge["type"]
    edge.update({kk: v for kk, v in patch.items() if kk not in ("a", "b")})
    fill_costs(data, force=True)
    path.write_text(json.dumps(data, indent=1))
    over_path = path.with_name("overrides.json")
    over = json.loads(over_path.read_text()) if over_path.exists() else {}
    over["edges"] = [p for p in over.get("edges") or [] if edge_key(p["a"], p["b"]) != k] + [patch]
    over_path.write_text(json.dumps(over, indent=1))
    return {"building_id": str(building_id), "edge": list(k), "status": status,
            "type": edge["type"], "passable": edge["passable"]}


if __name__ == "__main__":
    from . import toy
    if "toy" not in _list_buildings():
        graph_path("toy").parent.mkdir(parents=True, exist_ok=True)
        graph_path("toy").write_text(json.dumps(toy.toy_graph(), indent=1))
    r = best_entry("toy", "room_210", "foot")
    print(f"foot crew: {r['label']}, {r['seconds']} s; front door would cost {r['front_door']['seconds_saved']} s more")
    for s in directions("toy", r["path"]):
        print("  ", s["text"])
    s_ = best_entry("toy", "room_210", "stretcher")
    print(f"stretcher: {s_['label']}, {s_['seconds']} s")
    print("stairwell blocked:", replan("toy", "stair1", "room_210", "foot", [["stair1", "stair2"]])["path"])
    print("bad input:", route("toy", "nowhere", "room_210"))
