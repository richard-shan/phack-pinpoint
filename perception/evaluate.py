"""How often does the matcher find the right place?

    .venv/bin/python -m perception.evaluate 240517

Queries are camera views cut from the building's own panoramas at headings, tilt
and zoom that are NOT in the index, optionally degraded like a phone photo taken
in a bad moment. Two tests:

  same spot     the query's panorama is in the index. Right answer: that sweep.
  held out      the query's panorama is removed from the index first, as if the
                caller stood between scan points. Right answer: a sweep in the
                same scan within 3 m. Sweeps with no such neighbor are skipped.

Baseline "random" is the chance of a uniformly random sweep being right.
"""
import json
import math
import random
import sys

import cv2
import numpy as np

from .crops import load_faces, view
from .embed import embed
from .index import load
from .match import CONFIRMED, rank, rank_reranked
from .matterport import Matterport

NEAR_M = 3.0


def degrade(img, rng):
    """Blur, dim, noise, a hand in the corner, JPEG mush."""
    k = rng.choice([5, 7, 9])
    img = cv2.GaussianBlur(img, (k, k), 0)
    if rng.random() < 0.5:                                    # motion blur
        n = rng.choice([9, 13])
        kern = np.zeros((n, n), np.float32)
        kern[n // 2, :] = 1.0 / n
        img = cv2.filter2D(img, -1, kern)
    img = (255 * (img / 255.0) ** rng.uniform(1.4, 2.2)).astype(np.float32)   # dim
    img += np.random.default_rng(rng.randrange(1 << 30)).normal(0, 6, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    h, w = img.shape[:2]
    if rng.random() < 0.5:                                    # partial occlusion
        ow, oh = int(w * rng.uniform(0.2, 0.35)), int(h * rng.uniform(0.2, 0.35))
        x, y = rng.choice([0, w - ow]), rng.choice([0, h - oh])
        img[y:y + oh, x:x + ow] = (40, 50, 70)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 35])
    return cv2.imdecode(buf, 1)


def make_queries(meta, seed=0, per_node=2):
    rng = random.Random(seed)
    mp = Matterport()
    nodes = sorted({r["node_id"] for r in meta["rows"]})
    clean, hard, truth = [], [], []
    for n in nodes:
        faces = load_faces(mp.pano(n))
        for _ in range(per_node):
            yaw = rng.choice(meta["yaws"]) + rng.uniform(12, 33)       # between indexed headings
            base = dict(yaw_deg=yaw, pitch_deg=rng.uniform(-18, 6), size=448)
            clean.append(view(faces, fov_deg=62, **base))
            hard.append(degrade(view(faces, fov_deg=rng.uniform(48, 58),           # tighter crop
                                     roll_deg=rng.uniform(-14, 14), **base), rng))  # tilted phone
            truth.append(n)
    return clean, hard, truth


def room_of(meta):
    mp = Matterport()
    out = {}
    for mid in {r["model_id"] for r in meta["rows"]}:
        for sweep, room in mp.rooms(mid).items():
            out["%s/%s" % (mid, sweep)] = "%s/%s" % (mid, room) if room else None
    return out


def score(emb, meta, queries, truth, held_out, rerank=False, limit=None, seed=0):
    rows = meta["rows"]
    room = room_of(meta)
    pos = {r["node_id"]: (r["model_id"], r["xyz"]) for r in rows}

    def near(a, b):
        return pos[a][0] == pos[b][0] and math.dist(pos[a][1], pos[b][1]) <= NEAR_M

    q = embed(queries)
    top1 = top5 = n = room1 = room5 = conf = conf_right = 0
    errs, chance = [], []
    todo = list(range(len(truth)))
    if limit and limit < len(todo):
        todo = sorted(random.Random(seed).sample(todo, limit))
    for i in todo:
        v, t = q[i], truth[i]
        right = {x for x in pos if x != t and near(x, t)} if held_out else {t}
        if not right:
            continue
        n += 1
        ex = (t,) if held_out else ()
        if rerank:
            cs = rank_reranked(emb, rows, v, queries[i], 5, exclude_nodes=ex)
            if cs[0]["inliers"] >= CONFIRMED:
                conf += 1
                conf_right += room[t] is not None and room[cs[0]["node_id"]] == room[t]
        else:
            cs = rank(emb, rows, v, 5, exclude_nodes=ex)
        got = [c["node_id"] for c in cs]
        top1 += got[0] in right
        top5 += any(g in right for g in got)
        room1 += room[t] is not None and room[got[0]] == room[t]
        room5 += room[t] is not None and any(room[g] == room[t] for g in got)
        errs.append(math.dist(pos[got[0]][1], pos[t][1]) if pos[got[0]][0] == pos[t][0] else None)
        chance.append(len(right) / (len(pos) - (1 if held_out else 0)))
    same_scan = [e for e in errs if e is not None]
    extra = ({"confirmed_share": round(conf / n, 3),
              "room_top1_when_confirmed": round(conf_right / conf, 3) if conf else None} if rerank else {})
    return {**extra, "queries": n, "room_top1": round(room1 / n, 3), "room_top5": round(room5 / n, 3),
            "rooms": len({v for v in room.values() if v}),
            "top1": round(top1 / n, 3), "top5": round(top5 / n, 3),
            "random_top1": round(sum(chance) / n, 3),
            "right_scan": round(len(same_scan) / n, 3),
            "median_m_when_right_scan": round(float(np.median(same_scan)), 2) if same_scan else None}


def run(building_id, seed=0, rerank=False, limit=None):
    """limit: score a random subset of this many queries per test (re-ranking is slow)."""
    emb, meta = load(building_id)
    clean, hard, truth = make_queries(meta, seed)
    kw = dict(rerank=rerank, limit=limit, seed=seed)
    return {
        "building_id": str(building_id), "sweeps": len(set(truth)), "near_m": NEAR_M,
        "method": "embedding + point matching" if rerank else "embedding only",
        "same_spot_clean": score(emb, meta, clean, truth, False, **kw),
        "same_spot_degraded": score(emb, meta, hard, truth, False, **kw),
        "held_out_clean": score(emb, meta, clean, truth, True, **kw),
        "held_out_degraded": score(emb, meta, hard, truth, True, **kw),
    }, hard


def table(res):
    lines = ["%s, building %s (%s)" % (res["method"], res["building_id"], "%d sweeps" % res["sweeps"])]
    for k, v in res.items():
        if isinstance(v, dict):
            line = "%-20s n=%3d  room@1 %.2f  room@5 %.2f  sweep@1 %.2f  sweep@5 %.2f" % (
                k, v["queries"], v["room_top1"], v["room_top5"], v["top1"], v["top5"])
            if "confirmed_share" in v:
                line += "  | confirmed %.2f, right when confirmed %s" % (
                    v["confirmed_share"], v["room_top1_when_confirmed"])
            lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    # python -m perception.evaluate <building> [--rerank] [--limit N] [--json out.json]
    args = sys.argv[2:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    res, hard = run(sys.argv[1], rerank="--rerank" in args, limit=limit)
    print(table(res))
    if "--json" in args:
        json.dump(res, open(args[args.index("--json") + 1], "w"), indent=1)
