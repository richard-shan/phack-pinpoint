"""Node attributes: one vision-model pass per panorama, cached.

    .venv/bin/python -m perception.attributes 240517

Each panorama is shown to Claude as four level views (ahead, right, behind, left).
The answer is fixed-schema JSON saved to cache/attributes/<building>/<node>.json.
Feeds best_question, find_equipment, hazards_along and directions.

Needs ANTHROPIC_API_KEY in .env and the photo index (perception.index) built first.
Model: PINPOINT_VISION_MODEL, default claude-haiku-5-5 (cheapest; move up if the
hand check finds it missing signage or equipment).
"""
import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel

from .index import load
from .matterport import REPO, load_dotenv

DEFAULT_MODEL = "claude-haiku-5-5"
VIEW_NAMES = {0: "ahead", 90: "right", 180: "behind", 270: "left"}
View = Literal["ahead", "right", "behind", "left"]


class Seen(BaseModel):
    type: Literal["aed", "fire_extinguisher", "fire_alarm_pull_station", "alarm_panel",
                  "exit_sign", "first_aid_kit", "fire_hose_cabinet", "electrical_panel"]
    view: View           # which of the four images shows it
    note: str            # a few words: where in the view


class Hazard(BaseModel):
    type: Literal["step_or_level_change", "stairs", "narrow_passage", "obstruction_in_path"]
    view: View
    note: str


class Attributes(BaseModel):
    room_type: Literal["lobby", "corridor", "office", "open_office", "reception", "waiting_room",
                       "exam_room", "conference_room", "kitchen_break_room", "restroom", "storage",
                       "utility", "stairwell", "elevator_lobby", "other"]
    furnished: bool                      # furniture or equipment present, as opposed to an empty room
    has_window: bool                     # a window to the outdoors is visible
    floor_surface: Literal["carpet", "tile", "wood", "vinyl", "concrete", "other"]
    signage_text: List[str]              # words readable on signs, door plates, labels; empty if none
    door_count: int                      # doors visible, open or closed
    stairs_visible: bool
    elevator_visible: bool
    equipment: List[Seen]                # safety equipment only; empty if none is visible
    hazards: List[Hazard]                # things that would slow a crew or a stretcher; empty if none
    landmark: str                        # short phrase a responder could recognise this spot by
    confidence: Literal["high", "medium", "low"]


PROMPT = """These four images are one spot inside a commercial building, seen from a 3D scan: looking \
ahead, right, behind and left, in that order. Describe the spot for emergency responders who need to \
find a caller and get a stretcher through.

Report only what is visible in the images. If you cannot read a sign, leave it out rather than guess. \
Count a window only if it shows the outdoors. Equipment means emergency and safety equipment only, not furniture, fixtures or \
appliances; most spots have none. A hazard is something that would stop or clearly slow a crew wheeling a stretcher: steps, stairs, a \
gap visibly narrower than a normal doorway, or objects standing in the walking path. An ordinary \
corridor, doorway, glass wall or furniture against a wall is not a hazard; most spots have none. \
For equipment and hazards, name the image it appears in. \
The landmark is a phrase of under ten words that would distinguish this spot from others in the same \
building, such as "glass double doors with two potted plants"."""


def _b64(path):
    return base64.standard_b64encode(open(path, "rb").read()).decode()


def attributes_dir(building_id):
    return os.path.join(REPO, "cache", "attributes", str(building_id))


def _ensure_views(rows, node_id, paths):
    """Bulk indexes do not save view images; render the four this pass needs."""
    missing = [y for y, p in paths.items() if not os.path.exists(p)]
    if not missing:
        return
    import cv2
    from .match import _view_image
    for y in missing:
        row = next(r for r in rows if r["node_id"] == node_id and r["yaw"] == y and r["pitch"] == 0
                   and r.get("fov", 75.0) == 75.0)
        os.makedirs(os.path.dirname(paths[y]), exist_ok=True)
        cv2.imwrite(paths[y], _view_image(row), [cv2.IMWRITE_JPEG_QUALITY, 88])


def describe_node(client, model, node_id, views):
    """views: {yaw: crop_path} for yaws 0, 90, 180, 270."""
    content = []
    for yaw in (0, 90, 180, 270):
        content.append({"type": "text", "text": "Looking %s:" % VIEW_NAMES[yaw]})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": _b64(views[yaw])}})
    content.append({"type": "text", "text": PROMPT})
    r = client.messages.parse(model=model, max_tokens=16000, output_config={"effort": "low"},
                              messages=[{"role": "user", "content": content}],
                              output_format=Attributes)
    if r.stop_reason != "end_turn" or r.parsed_output is None:
        raise RuntimeError("no attributes for %s (stop_reason=%s)" % (node_id, r.stop_reason))
    out = r.parsed_output.model_dump()
    path_of = {VIEW_NAMES[y]: p for y, p in views.items()}
    for item in out["equipment"] + out["hazards"]:
        item["evidence_path"] = path_of[item["view"]]
    out.update(node_id=node_id, model=model, evidence_paths=[views[y] for y in (0, 90, 180, 270)],
               usage={"input": r.usage.input_tokens, "output": r.usage.output_tokens})
    return out


def run(building_id, model=None, workers=6, refresh=False):
    load_dotenv()
    model = model or os.environ.get("PINPOINT_VISION_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic()
    _, meta = load(building_id)
    views = {}
    for r in meta["rows"]:
        if r["pitch"] == 0 and r["yaw"] in VIEW_NAMES and r.get("fov", 75.0) == 75.0:
            views.setdefault(r["node_id"], {})[r["yaw"]] = r["crop_path"]
    out_dir = attributes_dir(building_id)
    os.makedirs(out_dir, exist_ok=True)

    def one(node_id):
        path = os.path.join(out_dir, node_id.replace("/", "_") + ".json")
        if os.path.exists(path) and not refresh:
            return json.load(open(path)), True
        try:
            _ensure_views(meta["rows"], node_id, views[node_id])
            a = describe_node(client, model, node_id, views[node_id])
        except (anthropic.APIError, RuntimeError) as e:
            return {"node_id": node_id, "error": str(e)[:200]}, False
        json.dump(a, open(path, "w"), indent=1)
        return a, False

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(one, sorted(views)))
    done = [a for a, _ in results if "error" not in a]
    failed = [a for a, _ in results if "error" in a]
    json.dump({a["node_id"]: a for a in done}, open(os.path.join(out_dir, "_all.json"), "w"), indent=1)
    return {"building_id": str(building_id), "model": model, "nodes": len(done),
            "from_cache": sum(1 for _, c in results if c), "failed": failed,
            "tokens_in": sum(a["usage"]["input"] for a in done),
            "tokens_out": sum(a["usage"]["output"] for a in done)}


def node_attributes(building_id, node_id):
    """The `attributes` part of look(node_id). None if the pass has not covered it."""
    path = os.path.join(attributes_dir(building_id), node_id.replace("/", "_") + ".json")
    return json.load(open(path)) if os.path.exists(path) else None


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], refresh="--refresh" in sys.argv), indent=1))
