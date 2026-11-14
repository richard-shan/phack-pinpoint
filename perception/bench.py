"""Benchmark the matcher and collect per-query records for pooling and calibration.

    python -m perception.bench cuts 240517                 # compare index cuts, retrieval only
    python -m perception.bench records 240517 out.json     # full pipeline, one record per query

Scoring (fixed 14 Nov). A query is cut from a scanned position t.
  same spot   t is in the index. Hit: the top position is t.
  held out    t is removed from the index, as if the caller stood between scan
              points. Hit: the top position is NEAR t, meaning in the same scan and
              either a graph neighbour of t or within 3 m. A photo taken by a
              doorway can legitimately match the next room, so position, not room,
              is the main measure. Room accuracy is reported separately and only
              over queries whose room still has an indexed position ("answerable").
Two query sets: "tuned" is the degradation the pipeline was developed against;
"fresh" uses different camera ranges and different damage (colour cast, exposure,
downscaling, vignette) that nothing was tuned on. A change counts only if it helps
on both.
"""
import json
import math
import os
import random
import sys

import cv2
import numpy as np

from .crops import load_faces, view
from .embed import embed
from .matterport import Matterport

NEAR_M = 3.0
CUTS = {
    # name: (fov, yaws, pitches)
    "8x2@75": [(75.0, tuple(range(0, 360, 45)), (0, -25))],
    "12x3@60": [(60.0, tuple(range(0, 360, 30)), (-25, 0, 25))],
    "both": [(60.0, tuple(range(0, 360, 30)), (-25, 0, 25)), (75.0, tuple(range(0, 360, 45)), (0, -25))],
}
SIZE = 448


def building(building_id):
    """Positions of a building: {node: {model, xyz, neighbors, room, faces}}."""
    from .index import models_for
    mp = Matterport()
    nodes = {}
    for mid in models_for(building_id):
        model, rooms = mp.sweeps(mid), mp.rooms(mid)
        for loc in model["locations"]:
            ref = "%s/%s" % (mid, loc["id"])
            paths = mp.pano(ref)
            if not paths:
                continue
            p = loc["position"]
            nodes[ref] = {"model": mid, "xyz": (p["x"], p["y"], p["z"]),
                          "neighbors": ["%s/%s" % (mid, n) for n in loc["neighbors"]],
                          "room": "%s/%s" % (mid, rooms.get(loc["id"])) if rooms.get(loc["id"]) else None,
                          "faces": paths}
    return nodes


def near(nodes, t):
    """Indexed positions that count as a hit when t is held out."""
    a = nodes[t]
    out = set(n for n in a["neighbors"] if n in nodes and math.dist(nodes[n]["xyz"], a["xyz"]) <= 2 * NEAR_M)
    for n, b in nodes.items():
        if n != t and b["model"] == a["model"] and math.dist(a["xyz"], b["xyz"]) <= NEAR_M:
            out.add(n)
    out.discard(t)
    return out


def render_index(nodes, cut):
    """-> (images, rows) with rows [{node, fov, yaw, pitch}]."""
    imgs, rows = [], []
    for n in sorted(nodes):
        faces = load_faces(nodes[n]["faces"])
        for fov, yaws, pitches in CUTS[cut]:
            for pitch in pitches:
                for yaw in yaws:
                    imgs.append(view(faces, yaw, pitch, fov, SIZE))
                    rows.append({"node": n, "fov": fov, "yaw": yaw, "pitch": pitch})
    return imgs, rows


def _damage_tuned(img, rng):
    from .evaluate import degrade
    return degrade(img, rng)


def _damage_fresh(img, rng):
    """Damage the pipeline was never developed against."""
    x = img.astype(np.float32)
    x *= np.array([rng.uniform(0.75, 1.15), rng.uniform(0.9, 1.1), rng.uniform(0.75, 1.2)], np.float32)  # colour cast
    x = x * rng.choice([0.45, 0.6, 1.5, 1.8])                                                         # exposure
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - w / 2, yy - h / 2) / math.hypot(w / 2, h / 2)
    x *= (1 - 0.5 * r ** 2)[..., None]                                                                # vignette
    x = np.clip(x, 0, 255).astype(np.uint8)
    s = rng.choice([96, 128, 160])                                                                    # low resolution
    x = cv2.resize(cv2.resize(x, (s, s), interpolation=cv2.INTER_AREA), (w, h), interpolation=cv2.INTER_LINEAR)
    ok, buf = cv2.imencode(".jpg", x, [cv2.IMWRITE_JPEG_QUALITY, 25])
    return cv2.imdecode(buf, 1)


def make_queries(nodes, which, seed=0, per_node=2):
    """-> (images, truth nodes). which: tuned_clean, tuned, fresh_clean, fresh."""
    rng = random.Random(seed + (0 if which.startswith("tuned") else 7919))
    imgs, truth = [], []
    for n in sorted(nodes):
        faces = load_faces(nodes[n]["faces"])
        for _ in range(per_node):
            if which.startswith("tuned"):
                yaw = rng.choice(range(0, 360, 45)) + rng.uniform(12, 33)
                im = view(faces, yaw, rng.uniform(-18, 6), rng.uniform(48, 62), SIZE, rng.uniform(-14, 14))
                dmg = _damage_tuned
            else:   # wider lens like a phone's main camera, steeper tilt, any heading
                im = view(faces, rng.uniform(0, 360), rng.uniform(-30, 20), rng.uniform(60, 78), SIZE,
                          rng.uniform(-20, 20))
                dmg = _damage_fresh
            imgs.append(im if which.endswith("clean") else dmg(im, rng))
            truth.append(n)
    return imgs, truth


def position_scores(sims, row_node, top=3):
    """Per-position evidence from per-view similarities: max and mean of the top few."""
    out = {}
    for n in set(row_node):
        s = np.sort(sims[row_node == n])[::-1]
        out[n] = (float(s[0]), float(s[:top].mean()))
    return out


def retrieval_metrics(nodes, E, row_node, Q, truth, held_out, pool="max", smooth=0.0):
    """Retrieval-only accuracy. pool: max | top3. smooth: weight of same-scan neighbours."""
    rooms_of = {n: nodes[n]["room"] for n in nodes}
    c = dict(n=0, hit1=0, hit5=0, room_n=0, room1=0, room_vote1=0)
    for v, t in zip(Q, truth):
        good = near(nodes, t) if held_out else {t}
        if held_out and not good:
            continue
        sims = E @ v
        if held_out:
            sims = np.where(row_node == t, -9.0, sims)
        ps = position_scores(sims, row_node)
        if held_out:
            ps.pop(t, None)
        score = {n: (a if pool == "max" else b) for n, (a, b) in ps.items()}
        if smooth:
            base = dict(score)
            for n in score:
                nb = [base[m] for m in nodes[n]["neighbors"] if m in base]
                if nb:
                    score[n] = base[n] + smooth * (sum(nb) / len(nb))
        order = sorted(score, key=score.get, reverse=True)
        c["n"] += 1
        c["hit1"] += order[0] in good
        c["hit5"] += any(o in good for o in order[:5])
        answerable = rooms_of[t] and any(rooms_of[n] == rooms_of[t] for n in score)
        if answerable:
            c["room_n"] += 1
            c["room1"] += rooms_of[order[0]] == rooms_of[t]
            votes = {}
            for n in order[:8]:
                r = rooms_of[n] or n
                votes[r] = votes.get(r, 0) + score[n]
            c["room_vote1"] += max(votes, key=votes.get) == rooms_of[t]
    n = max(c["n"], 1)
    rn = max(c["room_n"], 1)
    return {"n": c["n"], "pos@1": round(c["hit1"] / n, 3), "pos@5": round(c["hit5"] / n, 3),
            "room_n": c["room_n"], "room@1": round(c["room1"] / rn, 3),
            "room_vote@1": round(c["room_vote1"] / rn, 3)}


def compare_cuts(building_id):
    nodes = building(building_id)
    qs = {w: make_queries(nodes, w) for w in ("tuned_clean", "tuned", "fresh_clean", "fresh")}
    Q = {w: embed(imgs) for w, (imgs, _) in qs.items()}
    print("building %s: %d positions; held-out queries with a near position: %d of %d" % (
        building_id, len(nodes), sum(bool(near(nodes, t)) for t in qs["tuned"][1]), len(qs["tuned"][1])))
    for cut in CUTS:
        imgs, rows = render_index(nodes, cut)
        E = embed(imgs)
        row_node = np.array([r["node"] for r in rows])
        print("== index cut %s (%d views)" % (cut, len(rows)))
        for held in (False, True):
            for w in qs:
                for pool, smooth in (("max", 0.0), ("top3", 0.0), ("top3", 0.25)):
                    m = retrieval_metrics(nodes, E, row_node, Q[w], qs[w][1], held, pool, smooth)
                    print("  %-9s %-12s pool=%-4s smooth=%.2f  pos@1 %.2f pos@5 %.2f | room@1 %.2f vote %.2f (n=%d, room n=%d)" % (
                        "held out" if held else "same spot", w, pool, smooth, m["pos@1"], m["pos@5"],
                        m["room@1"], m["room_vote@1"], m["n"], m["room_n"]), flush=True)


def records(building_id, out_path, cut="both", shortlist_positions=12, views_each=2, sets=("tuned", "fresh")):
    """Full pipeline per query; saves what pooling and calibration need."""
    from . import rerank
    nodes = building(building_id)
    imgs, rows = render_index(nodes, cut)
    E = embed(imgs)
    row_node = np.array([r["node"] for r in rows])
    out = []
    for w in sets:
        qimgs, truth = make_queries(nodes, w)
        Q = embed(qimgs)
        for held in (False, True):
            for qi, (v, t) in enumerate(zip(Q, truth)):
                good = near(nodes, t) if held else {t}
                if held and not good:
                    continue
                sims = E @ v
                if held:
                    sims = np.where(row_node == t, -9.0, sims)
                ps = position_scores(sims, row_node)
                if held:
                    ps.pop(t, None)
                order = sorted(ps, key=lambda n: ps[n][1], reverse=True)
                short = order[:shortlist_positions]
                pair_rows = []
                for n in short:
                    idx = np.where(row_node == n)[0]
                    pair_rows += [int(i) for i in idx[np.argsort(-sims[idx])[:views_each]]]
                pts = rerank.match_points(qimgs[qi], [imgs[i] for i in pair_rows])
                cands = []
                for k, n in enumerate(short):
                    ms = pts[k * views_each:(k + 1) * views_each]
                    best = max(ms, key=lambda m: m["inliers"])
                    cands.append({"node": n, "sim_max": round(ps[n][0], 4), "sim_top3": round(ps[n][1], 4),
                                  "inliers": best["inliers"], "matches": best["matches"],
                                  "inliers_sum": sum(m["inliers"] for m in ms)})
                out.append({
                    "building": str(building_id), "set": w, "held_out": held, "truth": t,
                    "good": sorted(good), "room": nodes[t]["room"],
                    "room_answerable": bool(nodes[t]["room"] and any(
                        nodes[n]["room"] == nodes[t]["room"] for n in ps)),
                    "cands": cands,
                    "more": [[n, round(ps[n][1], 4)] for n in order[shortlist_positions:30]],
                })
            print("  %s %s done (%d records)" % (building_id, w, len(out)), flush=True)
    meta = {n: {"model": d["model"], "xyz": d["xyz"], "room": d["room"],
                "neighbors": [m for m in d["neighbors"] if m in nodes]} for n, d in nodes.items()}
    json.dump({"building": str(building_id), "cut": cut, "nodes": meta, "records": out}, open(out_path, "w"))
    print("wrote %s: %d records" % (out_path, len(out)))


if __name__ == "__main__":
    if sys.argv[1] == "cuts":
        compare_cuts(sys.argv[2])
    elif sys.argv[1] == "records":
        records(sys.argv[2], sys.argv[3], *(sys.argv[4:5]))
