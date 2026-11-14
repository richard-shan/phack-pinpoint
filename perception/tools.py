"""The perception lane's tools, in the shapes of section 5 of pinpoint_plan.md.

    from perception.tools import (match_photo, node_attributes, question_attributes,
                                  find_equipment, hazards_along, landmark,
                                  detect_site_features)

Node ids are "<matterport_model_id>/<sweep_id>". Everything reads from cache/, which
perception.index and perception.attributes fill; nothing here calls a model except
match_photo (local) and, on a cache miss, detect_site_features.
"""
import json
import math
import os

from .attributes import attributes_dir
from .index import load
from .match import match_photo  # noqa: F401  (re-exported)

try:
    from .site import detect_site_features  # noqa: F401
except ImportError:                                       # site.py not merged yet
    from .stubs import detect_site_features  # noqa: F401

# Attributes a caller can answer a yes/no or one-word question about.
QUESTION_KEYS = ("room_type", "floor_surface", "has_window", "furnished",
                 "stairs_visible", "elevator_visible")


def _all(building_id):
    path = os.path.join(attributes_dir(building_id), "_all.json")
    if not os.path.exists(path):
        raise FileNotFoundError("no attributes for building %s; run: python -m perception.attributes %s"
                                % (building_id, building_id))
    return json.load(open(path))


def _positions(building_id):
    _, meta = load(building_id)
    return {r["node_id"]: (r["model_id"], r["xyz"]) for r in meta["rows"]}


def node_attributes(building_id, node_id):
    """The `attributes` part of look(node_id), or None if the node is unknown."""
    return _all(building_id).get(node_id)


def question_attributes(building_id, node_ids=None):
    """{node_id: {attribute: value}} for best_question / update_belief (Person C).
    Adds has_exit_sign and has_fire_extinguisher, which callers can usually see."""
    out = {}
    for n, a in _all(building_id).items():
        if node_ids is not None and n not in node_ids:
            continue
        row = {k: a[k] for k in QUESTION_KEYS}
        kinds = {e["type"] for e in a["equipment"]}
        row["has_exit_sign"] = "exit_sign" in kinds
        row["has_fire_extinguisher"] = "fire_extinguisher" in kinds
        out[n] = row
    return out


def find_equipment(building_id, type, near_node=None, limit=5):
    """-> [{node_id, meters, evidence_path, note}], nearest first.

    meters is straight-line distance and only exists between positions in the same
    scan (each scan has its own coordinates); otherwise it is None and those items
    sort last. Person A's graph distance should replace it when routing matters.
    type: aed, fire_extinguisher, fire_alarm_pull_station, alarm_panel, exit_sign,
    first_aid_kit, fire_hose_cabinet, electrical_panel."""
    pos = _positions(building_id)
    out = []
    for n, a in _all(building_id).items():
        for e in a["equipment"]:
            if e["type"] != type:
                continue
            meters = None
            if near_node in pos and n in pos and pos[n][0] == pos[near_node][0]:
                meters = round(math.dist(pos[n][1], pos[near_node][1]), 1)
            out.append({"node_id": n, "meters": meters, "evidence_path": e["evidence_path"],
                        "note": e["note"]})
    out.sort(key=lambda x: (x["meters"] is None, x["meters"] or 0))
    return out[:limit]


def hazards_along(building_id, path):
    """path: list of node ids -> [{node_id, type, evidence_path, note}] in path order."""
    attrs = _all(building_id)
    out = []
    for n in path:
        for h in (attrs.get(n) or {}).get("hazards", []):
            out.append({"node_id": n, "type": h["type"], "evidence_path": h["evidence_path"],
                        "note": h["note"]})
    return out


def landmark(building_id, node_id):
    """Short phrase for directions ("left at ..."), with a thumbnail. The thumbnail
    is the view the phrase was written from, not yet the direction of travel."""
    a = _all(building_id).get(node_id)
    if not a:
        return None
    return {"node_id": node_id, "text": a["landmark"], "room_type": a["room_type"],
            "signage_text": a["signage_text"], "thumbnail_path": a["evidence_paths"][0]}
