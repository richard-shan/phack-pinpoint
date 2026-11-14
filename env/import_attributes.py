"""Bring lane B's per-panorama attributes into a building graph.

    python3 -m env.import_attributes <building_id> [--from cache/attributes/<building_id>/_all.json]
    python3 -m env.build_graph --building <building_id>

Lane B (`perception.attributes`) writes one record per panorama, keyed
"<matterport model id>/<sweep id>". This converts those records to
`buildings/<building_id>/attributes.json`, keyed by the sweep ids the graph uses, which
the builder merges on the next rebuild. Records for scans the graph does not cover (a
property can have several scans; a graph is built from one) are counted and skipped.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config


def convert(records: dict, graph: dict) -> tuple[dict, dict]:
    """Lane B records -> ({sweep id: node patch}, counts)."""
    model = graph.get("matterport_model_id")
    known = {n["id"] for n in graph["nodes"]}
    out, counts = {}, {"imported": 0, "other_scan": 0, "not_in_graph": 0}
    for key, a in records.items():
        prefix, _, sweep = key.rpartition("/")
        if prefix and model and prefix != model:
            counts["other_scan"] += 1
            continue
        if sweep not in known:
            counts["not_in_graph"] += 1
            continue
        attrs = {k: a[k] for k in ("room_type", "furnished", "has_window", "floor_surface",
                                   "signage_text", "door_count", "stairs_visible",
                                   "elevator_visible") if k in a}
        if a.get("hazards"):
            attrs["hazards"] = [{"type": h.get("type"), "note": h.get("note"),
                                 "evidence_path": h.get("evidence_path")} for h in a["hazards"]]
        if a.get("confidence") is not None:
            attrs["perception_confidence"] = a["confidence"]
        patch = {"attributes": attrs,
                 "equipment": [{"type": e.get("type"), "note": e.get("note"),
                                "evidence_path": e.get("evidence_path")} for e in a.get("equipment") or []]}
        if a.get("room_type") and a["room_type"] != "other":
            patch["room_label"] = a["room_type"].replace("_", " ").capitalize()
        if a.get("landmark"):
            # A description of what is in view ("directory board between stone pillars"), written
            # for every position. Directions use it at turns only; `label` stays for named places.
            attrs["landmark_text"] = a["landmark"]
        out[sweep] = patch
        counts["imported"] += 1
    return out, counts


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("building_id")
    ap.add_argument("--from", dest="src", help="lane B's _all.json (default: cache/attributes/<id>/_all.json)")
    a = ap.parse_args(argv)
    src = Path(a.src) if a.src else config.ROOT / "cache" / "attributes" / a.building_id / "_all.json"
    if not src.exists():
        raise SystemExit(f"{src} not found: run `python -m perception.attributes {a.building_id}` first, "
                         "or pass --from")
    folder = config.BUILDINGS / a.building_id
    graph = json.loads((folder / "graph.json").read_text())
    patches, counts = convert(json.loads(src.read_text()), graph)
    (folder / "attributes.json").write_text(json.dumps(patches, indent=1))
    print(f"wrote {folder.relative_to(config.ROOT)}/attributes.json: {counts}")
    print(f"now run: python3 -m env.build_graph --building {a.building_id}")


if __name__ == "__main__":
    main()
