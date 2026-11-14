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


POSITIONS = 12      # positions sent to point matching
VIEWS_EACH = 2      # best views of each (so 24 pairs, about 2.5 s on the laptop)


def match_photo(building_id, image_path, k=10):
    """Contract: -> [{node_id, score, evidence_path}], best first.

    How it works: MegaLoc turns the photo into a vector; every scanned position is
    scored by its three most similar views; the best dozen positions are checked by
    point matching (LoFTR); positions are ordered by similarity plus a bonus for
    matched points; and a calibrator fitted on photos taken between scanned spots
    turns the evidence about the top position into a probability.

    score      0..1. For the top position it is the calibrated probability that it
               is right (the caller is at or next to it). The rest share what is
               left, so the list can be used directly as a belief.
    confirmed  true only for the top position, and only when its probability is at
               least 0.9. If nothing is confirmed, ask the caller a question or
               show the top thumbnails.
    Extra keys: inliers, similarity, model_name, fov, yaw, pitch, crop_path.
    evidence_path is the photo beside the matched view with lines joining matched
    points."""
    from . import calibrate
    emb, meta = load(building_id)
    rows = meta["rows"]
    tag = hashlib.sha1(open(image_path, "rb").read()).hexdigest()[:10]
    ev_dir = os.path.join(index_dir(building_id), "evidence", tag)
    os.makedirs(ev_dir, exist_ok=True)
    img = _read_photo(image_path, ev_dir)
    sims = emb @ embed([img])[0]

    by_node = {}
    for i, r in enumerate(rows):
        by_node.setdefault(r["node_id"], []).append(i)
    pooled = {}
    for n, idx in by_node.items():
        idx = np.array(idx)
        best = idx[np.argsort(-sims[idx])]
        pooled[n] = (float(sims[best[:3]].mean()), best)
    order = sorted(pooled, key=lambda n: pooled[n][0], reverse=True)
    short = order[:POSITIONS]
    pair_rows = [int(i) for n in short for i in pooled[n][1][:VIEWS_EACH]]
    pts = _rerank.match_points(img, [cv2.imread(rows[i]["crop_path"]) for i in pair_rows])
    cands = []
    for j, n in enumerate(short):
        ms = pts[j * VIEWS_EACH:(j + 1) * VIEWS_EACH]
        b = int(np.argmax([m["inliers"] for m in ms]))
        cands.append({"node": n, "sim_top3": pooled[n][0], "inliers": ms[b]["inliers"],
                      "matches": ms[b]["matches"], "row": pair_rows[j * VIEWS_EACH + b], "pts": ms[b]})

    model = calibrate.load_saved()
    ranked = calibrate.rank(cands, model["alpha"] if model else calibrate.ALPHA)
    graph = {n: {"model": rows[pooled[n][1][0]]["model_id"],
                 "neighbors": ["%s/%s" % (rows[pooled[n][1][0]]["model_id"], x)
                               for x in rows[pooled[n][1][0]]["neighbors"]]} for n in pooled}
    if model:
        p_top = calibrate.probability(model, calibrate.features(ranked, graph))
        confirm_at = model["confirm_at"]
    else:                                   # no calibrator fitted yet: the old fixed rule
        p_top = 1 - float(np.exp(-ranked[0]["inliers"] / 80.0))
        confirm_at = 1 - float(np.exp(-CONFIRMED / 80.0))
    alpha = model["alpha"] if model else calibrate.ALPHA
    key = lambda c: c["sim_top3"] + alpha * np.log1p(c["inliers"])
    rest = np.array([np.exp((key(c) - key(ranked[0])) / 0.05) for c in ranked[1:]])
    rest = (1 - p_top) * rest / rest.sum() if len(rest) and rest.sum() > 0 else rest

    out = []
    for j, c in enumerate(ranked[:k]):
        r = rows[c["row"]]
        path = os.path.join(ev_dir, "%02d.jpg" % (j + 1))
        _rerank.overlay(img, cv2.imread(r["crop_path"]), c["pts"]["pts_query"], c["pts"]["pts_cand"], path,
                        "#%d  %s  %d matched points" % (j + 1, r["model_name"], c["inliers"]))
        score = p_top if j == 0 else float(rest[j - 1])
        out.append({
            "node_id": c["node"], "score": round(score, 4), "evidence_path": path,
            "confirmed": bool(j == 0 and p_top >= confirm_at),
            "inliers": c["inliers"], "similarity": round(c["sim_top3"], 4),
            "model_name": r["model_name"], "fov": r.get("fov"), "yaw": r["yaw"], "pitch": r["pitch"],
            "crop_path": r["crop_path"],
        })
    return out


if __name__ == "__main__":
    print(json.dumps(match_photo(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5), indent=1))
