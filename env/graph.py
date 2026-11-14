"""The building graph: load `graph.json`, index it, and answer simple lookups.

Schema (see the plan, section 5). Extra fields are allowed and ignored by routing.

    node: id, floor, xyz, pano_path, room_label, attributes{}, is_entrance, equipment[]
          + kind: "pano" | "entrance" | "staging" | "arrival"   (default "pano")
          + label: short human name used in directions
          + primary: true on the entrance at the street address
    edge: a, b, meters, type, seconds{foot, stretcher}, passable{foot, stretcher},
          confidence, evidence_path
          + floors / rise_m on stairs and elevators, width_m on doors, assumed: true
            when a link was added by hand rather than seen in the scan

Edges are undirected and there is at most one edge between two nodes.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import config
from .costs import fill_costs

EXTERIOR = frozenset(config.EXTERIOR_TYPES)


def edge_key(a: str, b: str) -> tuple[str, str]:
    """Order-independent key for the edge between two nodes."""
    return (a, b) if a <= b else (b, a)


class Graph:
    def __init__(self, data: dict):
        self.data = fill_costs(data)
        self.building_id = str(data.get("building_id", ""))
        self.nodes: dict[str, dict] = {n["id"]: n for n in data["nodes"]}
        if len(self.nodes) != len(data["nodes"]):
            raise ValueError("duplicate node ids in graph")
        self.edges: dict[tuple[str, str], dict] = {}
        self.adj: dict[str, list[tuple[str, dict]]] = {n: [] for n in self.nodes}
        for e in data["edges"]:
            a, b = e["a"], e["b"]
            if a not in self.nodes or b not in self.nodes:
                raise ValueError(f"edge {a} - {b} names a node that does not exist")
            if e.get("type") not in config.EDGE_TYPES:
                raise ValueError(f"edge {a} - {b} has unknown type {e.get('type')!r}")
            k = edge_key(a, b)
            if a == b or k in self.edges:
                raise ValueError(f"self loop or duplicate edge {a} - {b}")
            self.edges[k] = e
            self.adj[a].append((b, e))
            self.adj[b].append((a, e))

    # ------------------------------------------------------------ loading
    @classmethod
    def from_file(cls, path: str | Path) -> "Graph":
        with open(path) as f:
            return cls(json.load(f))

    @classmethod
    def load(cls, building_id: str) -> "Graph":
        """Graph for a building id, cached until its file changes."""
        path = graph_path(building_id)
        mtime = path.stat().st_mtime_ns
        hit = _CACHE.get(str(path))
        if hit and hit[0] == mtime:
            return hit[1]
        g = cls.from_file(path)
        _CACHE[str(path)] = (mtime, g)
        return g

    # ------------------------------------------------------------ lookups
    def edge(self, a: str, b: str) -> dict | None:
        return self.edges.get(edge_key(a, b))

    def cost(self, edge: dict, profile: str) -> float | None:
        """Seconds for this profile, or None when the profile cannot cross the edge."""
        if not edge["passable"].get(profile, False):
            return None
        return edge["seconds"][profile]

    def kind(self, node_id: str) -> str:
        n = self.nodes[node_id]
        return n.get("kind") or ("entrance" if n.get("is_entrance") else "pano")

    def entrances(self) -> list[dict]:
        return [n for n in self.nodes.values() if n.get("is_entrance")]

    def of_kind(self, kind: str) -> list[dict]:
        return [n for n in self.nodes.values() if self.kind(n["id"]) == kind]

    def label(self, node_id: str) -> str | None:
        """A specific name for this spot ("Stair B", "the reception desk"), if it has one."""
        n = self.nodes[node_id]
        return n.get("label") or (n.get("attributes") or {}).get("landmark") or None

    def room(self, node_id: str) -> str | None:
        """The room this node is in ("Hallway", "Suite 210"), if known."""
        return self.nodes[node_id].get("room_label") or None

    def xyz(self, node_id: str):
        p = self.nodes[node_id].get("xyz")
        return tuple(p) if p is not None and len(p) == 3 else None

    def straight_line_m(self, a: str, b: str) -> float | None:
        pa, pb = self.xyz(a), self.xyz(b)
        return math.dist(pa, pb) if pa and pb else None


_CACHE: dict[str, tuple[int, Graph]] = {}


def graph_path(building_id: str) -> Path:
    return config.BUILDINGS / str(building_id) / "graph.json"


def list_buildings() -> list[str]:
    if not config.BUILDINGS.exists():
        return []
    return sorted(p.parent.name for p in config.BUILDINGS.glob("*/graph.json"))
