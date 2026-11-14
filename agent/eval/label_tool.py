#!/usr/bin/env python3
"""Hand-labeling CLI for Pinpoint eval incidents.

Walks a human through labeling one incident per invocation:
  1. Pick the room the caller is in (grouped by floor)
  2. Pick which node_id within that room (skipped when only one)
  3. Pick the ground-truth best_entry_id (default: closest entrance by foot-profile graph distance)
  4. Pick the caller_text (template or freeform)

Writes labeled_<incident_id>.json matching the schema in DEPENDENCIES.md:
  {incident_id, address, building_id, caller_photo_path, caller_text,
   ground_truth_node_id, ground_truth_room_id, ground_truth_best_entry_id}

Usage:
  python3 agent/eval/label_tool.py --building building_bayou --out agent/eval/fixtures/hand/
  python3 agent/eval/label_tool.py --building building_bayou --out /tmp/out \
      --pano agent/pinpoint/fixtures/building_bayou/pano/suite210_interior.jpg
  python3 agent/eval/label_tool.py --building building_bayou --out /tmp/out \
      --from-synth agent/eval/fixtures/synth_bayou/synth-000-f1_reception.json
"""
import argparse
import json
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(ROOT))

FIXTURES_DIR = ROOT / "pinpoint" / "fixtures"

CALLER_TEMPLATES = [
    "my dad collapsed in the waiting area, he's not responding",
    "someone fainted by the elevator",
    "chest pain, breathing is weird, we're sitting in a waiting room",
    "i think she's having a stroke, we're upstairs in the clinic",
    "patient down at the suite entry, bring the stretcher",
]


def _gen_incident_id():
    return f"labeled-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _load_fixtures(building_dir):
    graph = json.loads((building_dir / "graph.json").read_text())
    site = json.loads((building_dir / "site.json").read_text())
    prop = json.loads((building_dir / "property.json").read_text())
    return graph, site, prop


def _interior_nodes_by_floor(graph):
    """Return {floor: [node, ...]} for nodes a caller could be at.
    Excludes entrances (is_entrance=true), staging (attributes.staging=true),
    and floor-0 exterior nodes.
    """
    out = {}
    for n in graph["nodes"]:
        if n.get("is_entrance"):
            continue
        if n.get("attributes", {}).get("staging"):
            continue
        if n.get("floor", 0) == 0:
            continue
        out.setdefault(n["floor"], []).append(n)
    return out


def _group_rooms(nodes_by_floor):
    """Return a flat list of (floor, room_label, [node, ...]) tuples."""
    rows = []
    for floor in sorted(nodes_by_floor.keys()):
        rooms = {}
        for n in nodes_by_floor[floor]:
            rooms.setdefault(n["room_label"], []).append(n)
        for label in sorted(rooms.keys()):
            rows.append((floor, label, rooms[label]))
    return rows


def _default_best_entry(building_id, node_id):
    """routing.best_entry() closest entrance by foot profile. Returns entrance_id or None."""
    try:
        from pinpoint import routing
        res = routing.best_entry(building_id, node_id, "foot")
        return res.get("entrance_id")
    except Exception:
        return None


def _prompt_choice(prompt, options, default_idx, input_fn, print_fn):
    for i, opt in enumerate(options, 1):
        marker = " <-- default" if i - 1 == default_idx else ""
        print_fn(f"  {i}. {opt}{marker}")
    while True:
        raw = input_fn(f"{prompt} [{default_idx + 1}]: ").strip()
        if not raw:
            return default_idx
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass
        print_fn(f"  (not valid, enter a number 1 to {len(options)})")


def _write(label, out_dir, suffix, print_fn):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"labeled_{label['incident_id']}{suffix}.json"
    out_path = out_dir / name
    out_path.write_text(json.dumps(label, indent=2) + "\n")
    print_fn(f"\nWrote {out_path}")
    return out_path


def label_one(args, input_fn=input, print_fn=print):
    building_dir = FIXTURES_DIR / args.building
    if not building_dir.exists():
        print_fn(f"Building fixture not found: {building_dir}")
        return 2
    graph, site, prop = _load_fixtures(building_dir)

    draft = {}
    if args.from_synth:
        draft = json.loads(Path(args.from_synth).read_text())

    if args.pano:
        try:
            subprocess.run(["open", str(args.pano)], check=False)
        except Exception:
            pass

    building_id = graph.get("building_id") or prop.get("building_id") or args.building
    address = prop.get("address", "")

    label = {
        "incident_id": _gen_incident_id(),
        "address": address,
        "building_id": building_id,
        "caller_photo_path": draft.get("caller_photo_path", ""),
        "caller_text": draft.get("caller_text", ""),
        "ground_truth_node_id": draft.get("ground_truth_node_id", ""),
        "ground_truth_room_id": draft.get("ground_truth_room_id", ""),
        "ground_truth_best_entry_id": draft.get("ground_truth_best_entry_id", ""),
    }

    try:
        # Step 1: pick room
        nodes_by_floor = _interior_nodes_by_floor(graph)
        rooms = _group_rooms(nodes_by_floor)
        if not rooms:
            print_fn("No interior rooms found in graph. Nothing to label.")
            return 2
        room_lines = [f"F{f} | {label_} | {len(ns)} node(s)" for f, label_, ns in rooms]
        default_room_idx = 0
        if draft.get("ground_truth_room_id"):
            for i, (_, lbl, _) in enumerate(rooms):
                if lbl == draft["ground_truth_room_id"]:
                    default_room_idx = i
                    break
        print_fn("")
        print_fn("Rooms available (floor | room | node count):")
        chosen_idx = _prompt_choice("Which room is the caller in?", room_lines,
                                    default_room_idx, input_fn, print_fn)
        floor, room_label, room_nodes = rooms[chosen_idx]
        label["ground_truth_room_id"] = room_label

        # Step 2: pick node within room
        if len(room_nodes) == 1:
            chosen_node = room_nodes[0]
            print_fn(f"\nOnly one node in '{room_label}': {chosen_node['id']} "
                     f"(auto-selected)")
        else:
            default_node_idx = 0
            if draft.get("ground_truth_node_id"):
                for i, n in enumerate(room_nodes):
                    if n["id"] == draft["ground_truth_node_id"]:
                        default_node_idx = i
                        break
            node_options = [f"{n['id']}  (pano: {n.get('pano_path') or 'none'})"
                            for n in room_nodes]
            print_fn("")
            print_fn(f"Nodes in '{room_label}':")
            n_idx = _prompt_choice("Which node?", node_options, default_node_idx,
                                   input_fn, print_fn)
            chosen_node = room_nodes[n_idx]
        label["ground_truth_node_id"] = chosen_node["id"]
        if not label["caller_photo_path"]:
            label["caller_photo_path"] = chosen_node.get("pano_path") or ""

        # Step 3: best_entry (default = closest by graph dist)
        entrances = site.get("entrances", [])
        if not entrances:
            print_fn("No entrances in site.json. Leaving best_entry_id blank.")
        else:
            computed_default = _default_best_entry(building_id, chosen_node["id"])
            default_entry_idx = 0
            draft_entry = draft.get("ground_truth_best_entry_id") or computed_default
            if draft_entry:
                for i, e in enumerate(entrances):
                    if e["entrance_id"] == draft_entry:
                        default_entry_idx = i
                        break
            entry_options = [f"{e['entrance_id']}  ({e.get('label', '')})"
                             for e in entrances]
            print_fn("")
            print_fn("Entrances (default = closest by foot-profile graph distance):")
            e_idx = _prompt_choice("Which best_entry_id for responders?", entry_options,
                                   default_entry_idx, input_fn, print_fn)
            label["ground_truth_best_entry_id"] = entrances[e_idx]["entrance_id"]

        # Step 4: caller text
        print_fn("")
        print_fn("Caller text templates:")
        for i, tmpl in enumerate(CALLER_TEMPLATES, 1):
            print_fn(f"  {i}. {tmpl}")
        default_text = draft.get("caller_text") or CALLER_TEMPLATES[0]
        raw = input_fn(
            f"Caller text? [number 1-{len(CALLER_TEMPLATES)}, freeform, "
            f"or enter for default]: "
        ).strip()
        if not raw:
            label["caller_text"] = default_text
        else:
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(CALLER_TEMPLATES):
                    label["caller_text"] = CALLER_TEMPLATES[idx]
                else:
                    label["caller_text"] = raw
            except ValueError:
                label["caller_text"] = raw

    except KeyboardInterrupt:
        print_fn("")
        _write(label, args.out, ".partial", print_fn)
        return 130

    _write(label, args.out, "", print_fn)
    return 0


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Hand-label one Pinpoint eval incident.")
    p.add_argument("--building", required=True,
                   help="Fixture folder name under agent/pinpoint/fixtures/")
    p.add_argument("--out", required=True, help="Output directory for labeled JSON")
    p.add_argument("--pano", default=None,
                   help="Optional path to a pano image to open (macOS 'open')")
    p.add_argument("--from-synth", default=None,
                   help="Optional path to a synth incident JSON to use as a draft")
    return p.parse_args(argv)


def main(argv=None, input_fn=input, print_fn=print):
    args = _parse_args(argv)
    return label_one(args, input_fn=input_fn, print_fn=print_fn)


if __name__ == "__main__":
    sys.exit(main())
