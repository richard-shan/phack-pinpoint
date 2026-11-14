"""Build the photo index for a building: every panorama cut into camera views and embedded.

    .venv/bin/python -m perception.index 240517

A building is every Matterport scan CoStar lists for the property. Node ids are
"<model_id>/<sweep_id>". Output goes to cache/index/<building_id>/:
    emb.npy      one row per view (52 views per position)
    meta.json    node_id, fov, yaw, pitch, crop_path, xyz for each row
    crops/       the views themselves, for thumbnails and evidence
"""
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from .crops import load_faces, view
from .embed import DESCRIPTOR, MODEL_NAME, RES, embed, weights_name
from .matterport import REPO, Matterport, load_dotenv

# Two cuts per position. 60 degree views every 30 degrees at three tilts match a
# zoomed or tilted phone photo; 75 degree views every 45 degrees match a phone's
# wide main camera. Using both was the best all-round in perception.bench.
CUTS = (
    (60.0, tuple(range(0, 360, 30)), (-25, 0, 25)),
    (75.0, tuple(range(0, 360, 45)), (0, -25)),
)
SIZE = 448
RENDER_THREADS = min(16, os.cpu_count() or 4)


def index_dir(building_id):
    return os.path.join(REPO, "cache", "index", str(building_id))


def models_for(building_id):
    """Matterport scans that make up a building: the ones named in
    perception/buildings.json if the building is listed there, otherwise every scan
    CoStar lists for the property, in file order."""
    pinned = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buildings.json")
    if os.path.exists(pinned):
        listed = json.load(open(pinned)).get(str(building_id))
        if listed:
            return list(listed)
    load_dotenv()
    kit = os.environ.get("COSTAR_KIT_DIR")
    if not kit:
        raise RuntimeError("COSTAR_KIT_DIR is not set (see .env.example)")
    seen = []
    for r in csv.DictReader(open(os.path.join(kit, "commercial-properties.csv"))):
        if r["PropertyId"] == str(building_id) and r["MatterportModelId"] and r["MatterportModelId"] not in seen:
            seen.append(r["MatterportModelId"])
    return seen


def build(building_id, model_ids=None, mp=None, save_crops=True, quiet=False):
    """save_crops=False skips writing the view images (bulk indexing); match_photo then
    re-renders the views it needs from the panoramas."""
    mp = mp or Matterport()
    model_ids = model_ids or models_for(building_id)
    if not model_ids:
        raise ValueError("no Matterport models for building %s" % building_id)
    out = index_dir(building_id)
    os.makedirs(os.path.join(out, "crops"), exist_ok=True)
    meta, images, skipped = [], [], []
    for mid in model_ids:
        try:
            model = mp.sweeps(mid)
        except Exception as e:                       # not shared with our key, or not a scan
            skipped.append({"model_id": mid, "why": str(e)[:120]})
            continue
        def render(loc, mid=mid, model=model):
            """All views of one position. Runs in a thread: OpenCV releases the GIL."""
            ref = "%s/%s" % (mid, loc["id"])
            paths = mp.pano(ref)
            if not paths:
                return ref, None, None
            faces = load_faces(paths)
            p = loc["position"]
            imgs, rows = [], []
            for fov, yaws, pitches in CUTS:
                for pitch in pitches:
                    for yaw in yaws:
                        img = view(faces, yaw, pitch, fov, SIZE)
                        # 75 degree views keep their original names: cached attributes point at them.
                        tag = "" if fov == 75.0 else "_f%02d" % fov
                        rel = os.path.join("crops", "%s_%s_y%03d_p%+03d%s.jpg" % (mid, loc["id"], yaw, pitch, tag))
                        if save_crops:
                            cv2.imwrite(os.path.join(out, rel), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
                        imgs.append(img)
                        rows.append({"node_id": ref, "model_id": mid, "model_name": model["name"],
                                     "fov": fov, "yaw": yaw, "pitch": pitch,
                                     "crop_path": os.path.join(out, rel),
                                     "xyz": [p["x"], p["y"], p["z"]], "neighbors": loc["neighbors"]})
            return ref, imgs, rows

        with ThreadPoolExecutor(RENDER_THREADS) as pool:
            for ref, imgs, rows in pool.map(render, sorted(model["locations"], key=lambda l: l["id"])):
                if imgs is None:
                    skipped.append({"node_id": ref, "why": "no panorama"})
                    continue
                images += imgs
                meta += rows
        if not quiet:
            print("  %s %-28s %d sweeps" % (mid, model["name"][:28], len(model["locations"])), flush=True)
    emb = embed(images)
    np.save(os.path.join(out, "emb.npy"), emb.astype(np.float16))     # half the disk, same ranking
    for stale in ("head.npz", "train_views.npz"):                     # belonged to the old descriptor
        if os.path.exists(os.path.join(out, stale)):
            os.remove(os.path.join(out, stale))
    json.dump({"building_id": str(building_id), "descriptor": DESCRIPTOR, "weights": weights_name(),
               "input_res": RES, "cuts": CUTS, "rows": meta, "skipped": skipped},
              open(os.path.join(out, "meta.json"), "w"))
    return {"building_id": str(building_id), "views": len(meta),
            "nodes": len({m["node_id"] for m in meta}), "skipped": skipped}


def load(building_id):
    d = index_dir(building_id)
    if not os.path.exists(os.path.join(d, "emb.npy")):
        raise FileNotFoundError("no index for building %s; run: python -m perception.index %s"
                                % (building_id, building_id))
    meta = json.load(open(os.path.join(d, "meta.json")))
    if meta.get("descriptor", "dinov2") != DESCRIPTOR:
        raise RuntimeError("index for building %s was built with descriptor %r but %r is active; "
                           "rebuild it: python -m perception.index %s"
                           % (building_id, meta.get("descriptor", "dinov2"), DESCRIPTOR, building_id))
    return np.load(os.path.join(d, "emb.npy")).astype(np.float32), meta


if __name__ == "__main__":
    for b in sys.argv[1:]:
        print(json.dumps(build(b), indent=1))
