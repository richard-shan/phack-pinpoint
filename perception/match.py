"""match_photo: a caller's photo to ranked positions in the building.

    .venv/bin/python -m perception.match 240517 path/to/photo.jpg
"""
import hashlib
import json
import os
import sys

import cv2
import numpy as np

from . import rerank as _rerank
from .embed import embed
from .head import encoder
from .index import index_dir, load

SHORTLIST = 40      # views re-ranked by point matching (about 0.1 s each)
CONFIRMED = 60      # matched points at or above this: the same place, not a look-alike


def rank(emb, rows, q, k=10, exclude_nodes=()):
    """Best view per node, highest similarity first."""
    sims = emb @ q
    best = {}
    for i in np.argsort(-sims):
        n = rows[i]["node_id"]
        if n in exclude_nodes or n in best:
            continue
        best[n] = (float(sims[i]), int(i))
        if len(best) >= k:
            break
    return [{"node_id": n, "score": round(s, 4), "row": i} for n, (s, i) in best.items()]


def rank_reranked(emb, rows, q_vec, q_img, k=10, exclude_nodes=(), shortlist=SHORTLIST):
    """Shortlist views by embedding, then order nodes by matched points. Nodes with
    no matched points keep their embedding order, after the ones that have some.
    Returns dicts with node_id, score, inliers, embedding_score, row, pts_query, pts_cand."""
    sims = emb @ q_vec
    order = [int(i) for i in np.argsort(-sims) if rows[i]["node_id"] not in exclude_nodes]
    short = order[:shortlist]
    pts = _rerank.match_points(q_img, [cv2.imread(rows[i]["crop_path"]) for i in short])
    best = {}
    for i, m in zip(short, pts):
        n = rows[i]["node_id"]
        if n not in best or m["inliers"] > best[n]["inliers"]:
            best[n] = dict(m, row=i, embedding_score=float(sims[i]))
    for i in order:                                   # fill up with embedding-only nodes
        if len(best) >= max(k, 10):
            break
        best.setdefault(rows[i]["node_id"], {"inliers": 0, "row": i, "embedding_score": float(sims[i]),
                                             "pts_query": np.zeros((0, 2)), "pts_cand": np.zeros((0, 2))})
    out = []
    for n, m in best.items():
        # 0..1, rising with matched points; the embedding only breaks ties.
        score = (1 - float(np.exp(-m["inliers"] / 80.0))) * 0.98 + 0.02 * max(m["embedding_score"], 0)
        out.append(dict(m, node_id=n, score=round(score, 4)))
    out.sort(key=lambda c: -c["score"])
    return out[:k]


def _evidence(query_img, crop_path, out_path, label):
    """Caller's photo beside the matched view. Replaced by a keypoint overlay once
    re-ranking is in."""
    h = 448
    a = cv2.resize(query_img, (int(query_img.shape[1] * h / query_img.shape[0]), h))
    b = cv2.resize(cv2.imread(crop_path), (h, h))
    pad = np.full((h, 12, 3), 255, np.uint8)
    img = np.hstack([a, pad, b])
    cv2.putText(img, label, (a.shape[1] + 20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
    cv2.putText(img, label, (a.shape[1] + 20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.imwrite(out_path, img)
    return out_path


def _read_photo(image_path, work_dir, max_side=1600):
    """Read a caller's photo. iPhones send HEIC, which OpenCV cannot read, so those
    are converted with macOS `sips` first. Large photos are shrunk."""
    img = cv2.imread(image_path)
    if img is None and image_path.lower().endswith((".heic", ".heif")):
        import subprocess
        jpg = os.path.join(work_dir, "query.jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", image_path, "--out", jpg],
                       check=True, capture_output=True)
        img = cv2.imread(jpg)
    if img is None:
        raise ValueError("cannot read image: %s" % image_path)
    scale = max_side / max(img.shape[:2])
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return img


def match_photo(building_id, image_path, k=10, rerank=True):
    """Contract: -> [{node_id, score, evidence_path}], best first.

    score is 0..1 and rises with the number of matched points; `confirmed` is true
    when there are enough of them that this is the same place and not a look-alike.
    If nothing is confirmed, treat the list as "similar-looking places" and ask the
    caller a question. Extra keys: inliers, embedding_score, model_name, yaw, pitch,
    crop_path. evidence_path is the photo beside the matched view with lines joining
    matched points."""
    emb, meta = load(building_id)
    rows = meta["rows"]
    encode = encoder(building_id)          # trained per-building layer, if there is one
    emb = encode(emb)
    tag = hashlib.sha1(open(image_path, "rb").read()).hexdigest()[:10]
    ev_dir = os.path.join(index_dir(building_id), "evidence", tag)
    os.makedirs(ev_dir, exist_ok=True)
    img = _read_photo(image_path, ev_dir)
    q = encode(embed([img]))[0]
    if rerank:
        cands = rank_reranked(emb, rows, q, img, k)
    else:
        cands = [dict(c, inliers=None, embedding_score=c["score"]) for c in rank(emb, rows, q, k)]
    out = []
    for j, c in enumerate(cands):
        r = rows[c["row"]]
        path = os.path.join(ev_dir, "%02d.jpg" % (j + 1))
        if rerank:
            label = "#%d  %s  %d matched points" % (j + 1, r["model_name"], c["inliers"])
            _rerank.overlay(img, cv2.imread(r["crop_path"]), c["pts_query"], c["pts_cand"], path, label)
        else:
            _evidence(img, r["crop_path"], path, "#%d  %s  %.2f" % (j + 1, r["model_name"], c["score"]))
        out.append({
            "node_id": c["node_id"], "score": c["score"], "evidence_path": path,
            "inliers": c["inliers"], "confirmed": bool(rerank and c["inliers"] >= CONFIRMED),
            "embedding_score": round(c["embedding_score"], 4),
            "model_name": r["model_name"], "yaw": r["yaw"], "pitch": r["pitch"],
            "crop_path": r["crop_path"],
        })
    return out


if __name__ == "__main__":
    print(json.dumps(match_photo(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5), indent=1))
