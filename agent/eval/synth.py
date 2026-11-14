#!/usr/bin/env python3
"""Semi-synthetic incident generator for the Pinpoint eval.

Given a building's graph.json + site.json, pick N interior nodes as ground truth and emit
labeled-incident JSON files. match_photo's synth branch (stubs._synth_candidates) will produce
an ambiguous candidate set around each ground-truth node when the pipeline is run.

    python3 agent/eval/synth.py --n 40 --out agent/eval/fixtures/synth_bayou

Each file:
  {
    "incident_id": "...",
    "address": "...",
    "building_id": "...",
    "caller_photo_path": "fixtures/building_bayou/synth/node_<id>.jpg",
    "caller_text": "...",
    "ground_truth_node_id": "...",
    "ground_truth_room_id": "...",
    "ground_truth_best_entry_id": "..."
  }
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(ROOT))
from pinpoint import routing
from pinpoint.config import FIXTURES

BUILDING = "bayou-medical-plaza"
BLDG_DIR = FIXTURES / "building_bayou"

CALLER_TEXTS = [
    "my dad collapsed, we're at the dialysis place upstairs, there's a window next to us",
    "someone's unconscious here, I think it's the second floor",
    "she fell and hit her head, we're near the elevators",
    "chest pain, breathing weird, we're in a waiting area",
    "my coworker just passed out at reception",
    "I need help, we're in a hallway and I can see outside",
    "diabetic emergency, we're inside the clinic suite",
    "please hurry, she's on the floor by the dock side",
    "severe allergic reaction, we're on the ground level reception",
    "my mom is confused and can't stand, we're near a stairwell",
]


def room_id_of(node):
    """Group nodes into 'rooms' by their room_label. Falls back to node_id if label missing."""
    return node.get("room_label") or node["id"]


def closest_entrance_by_graph(node_id):
    """Shortest walking-time entrance from this node. Uses the routing module directly."""
    g = routing.load_graph(BUILDING)
    dist, _ = routing._dijkstra(g, node_id, "foot")
    ents = [nid for nid, n in g["nodes"].items() if n.get("is_entrance")]
    ents = [e for e in ents if e in dist]
    if not ents:
        return None
    return min(ents, key=lambda e: dist[e])


def eligible_truth_nodes():
    """Interior candidates for ground truth: not entrances, not exterior staging/parking.
    A 911 caller is inside the building. Filters: is_entrance false, floor >= 1, no
    staging=True attribute, has an attributes dict so update_belief has something to work with."""
    g = json.loads((BLDG_DIR / "graph.json").read_text())
    out = []
    for n in g["nodes"]:
        if n.get("is_entrance"):
            continue
        attrs = n.get("attributes") or {}
        if attrs.get("staging"):
            continue
        rt = attrs.get("room_type", "")
        if rt in ("exterior", "parking"):
            continue
        if not n.get("floor") or n.get("floor") < 1:
            continue
        if not attrs:
            continue
        out.append(n)
    return out


def generate(n, outdir, seed):
    rng = random.Random(seed)
    nodes = eligible_truth_nodes()
    assert nodes, "no eligible truth nodes in graph"
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    address = json.loads((BLDG_DIR / "property.json").read_text())["address"]

    picks = []
    for i in range(n):
        picks.append(rng.choice(nodes))

    written = []
    for i, truth in enumerate(picks):
        inc_id = f"synth-{i:03d}-{truth['id']}"
        room_id = room_id_of(truth)
        best_entry = closest_entrance_by_graph(truth["id"])
        incident = {
            "_source": "synth generator — bayou-medical-plaza interior node sampled uniformly.",
            "incident_id": inc_id,
            "address": address,
            "building_id": BUILDING,
            "caller_photo_path": f"fixtures/building_bayou/synth/node_{truth['id']}.jpg",
            "caller_text": rng.choice(CALLER_TEXTS),
            "ground_truth_node_id": truth["id"],
            "ground_truth_room_id": room_id,
            "ground_truth_best_entry_id": best_entry,
        }
        path = outdir / f"{inc_id}.json"
        path.write_text(json.dumps(incident, indent=2))
        written.append(path)
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", default=str(ROOT / "eval" / "fixtures" / "synth_bayou"))
    ap.add_argument("--seed", type=int, default=20261114)
    args = ap.parse_args()
    files = generate(args.n, args.out, args.seed)
    print(f"wrote {len(files)} incidents to {args.out}")


if __name__ == "__main__":
    main()
