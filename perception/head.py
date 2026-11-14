"""A small trained layer on top of the frozen embedding, one per building.

    .venv/bin/python -m perception.head 240517          # train and save for use
    .venv/bin/python -m perception.head 240517 --cv     # honest accuracy, 5 folds

The embedding model knows what places look like in general; it does not know that
two views are the same room of THIS building. The scans do: Matterport gives every
position a room id. So for each position we cut random camera views (random
heading, tilt, zoom and roll; half blurred and dimmed like a phone photo) and train
a linear layer that pulls views of the same room together and pushes other rooms
apart. At match time the layer's output is blended with the original embedding.

--cv holds out a fifth of the positions at a time, trains without any view from
them, and tests on them, so the numbers are not flattered by the model having seen
the test positions. The saved layer is trained on every position.
"""
import math
import os
import random
import sys

import numpy as np
import torch

from .crops import load_faces, view
from .embed import embed
from .index import index_dir, load
from .matterport import Matterport

PER_SWEEP = 24
BLEND = 0.3          # weight of the trained layer against the original embedding
DIM = 512


def training_views(building_id, refresh=False):
    """(features, node ids) for PER_SWEEP random views of every position. Cached."""
    path = os.path.join(index_dir(building_id), "train_views.npz")
    if os.path.exists(path) and not refresh:
        d = np.load(path, allow_pickle=True)
        return d["X"], d["nodes"]
    from .evaluate import degrade
    _, meta = load(building_id)
    mp, rng = Matterport(), random.Random(int(building_id) % 100003)
    imgs, ids = [], []
    for n in sorted({r["node_id"] for r in meta["rows"]}):
        faces = load_faces(mp.pano(n))
        for i in range(PER_SWEEP):
            im = view(faces, rng.uniform(0, 360), rng.uniform(-25, 10), rng.uniform(50, 80), 448,
                      rng.uniform(-12, 12))
            imgs.append(degrade(im, rng) if i % 2 else im)
            ids.append(n)
    X = embed(imgs)
    np.savez(path, X=X, nodes=np.array(ids))
    return X, np.array(ids)


def room_labels(building_id, node_ids):
    """Integer room label per view. A position with no room id is its own room."""
    from .evaluate import room_of
    _, meta = load(building_id)
    room = room_of(meta)
    names = [room.get(n) or n for n in node_ids]
    lut = {v: i for i, v in enumerate(sorted(set(names)))}
    return np.array([lut[v] for v in names])


def train(X, y, epochs=40, seed=0, temp=0.07):
    """Supervised contrastive training of one linear layer. Returns (W, b)."""
    torch.manual_seed(seed)
    X, y = torch.from_numpy(X), torch.from_numpy(y)
    layer = torch.nn.Linear(X.shape[1], DIM)
    opt = torch.optim.AdamW(layer.parameters(), lr=1e-3, weight_decay=1e-2)
    for _ in range(epochs):
        perm = torch.randperm(len(X))
        for k in range(0, len(X), 512):
            b = perm[k:k + 512]
            z = torch.nn.functional.normalize(layer(X[b]), dim=-1)
            sim = z @ z.T / temp
            eye = torch.eye(len(b))
            same = (y[b][:, None] == y[b][None, :]).float() - eye
            logp = sim - torch.logsumexp(sim.masked_fill(eye.bool(), -1e9), dim=1, keepdim=True)
            has = same.sum(1) > 0
            loss = -((logp * same).sum(1)[has] / same.sum(1)[has]).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
    return layer.weight.detach().numpy(), layer.bias.detach().numpy()


def blend(raw, W, b, w=BLEND):
    """Original embedding and trained layer side by side, so one dot product scores both."""
    z = raw @ W.T + b
    z = z / np.linalg.norm(z, axis=1, keepdims=True)
    return np.concatenate([raw * math.sqrt(1 - w), z * math.sqrt(w)], 1).astype(np.float32)


def fit(building_id):
    X, ids = training_views(building_id)
    W, b = train(X, room_labels(building_id, ids))
    np.savez(os.path.join(index_dir(building_id), "head.npz"), W=W, b=b, blend=BLEND)
    return {"building_id": str(building_id), "training_views": int(len(X)),
            "rooms": int(len(set(room_labels(building_id, ids))))}


def encoder(building_id):
    """Function mapping raw embeddings (N x D) to what the matcher compares.
    Identity if no layer has been trained for this building."""
    path = os.path.join(index_dir(building_id), "head.npz")
    if not os.path.exists(path):
        return lambda raw: raw
    d = np.load(path)
    return lambda raw: blend(raw, d["W"], d["b"], float(d["blend"]))


def cross_validate(building_id, rerank=False, limit=None, folds=5, seed=0):
    """Accuracy with the layer trained on other positions only. Same four tests as
    perception.evaluate; `limit` scores a random subset per fold-test when re-ranking."""
    from .evaluate import make_queries, score
    emb, meta = load(building_id)
    clean, hard, truth = make_queries(meta, seed)
    X, ids = training_views(building_id)
    y = room_labels(building_id, ids)
    sweeps = sorted(set(truth))
    random.Random(seed).shuffle(sweeps)
    tests = (("same_spot_clean", clean, False), ("same_spot_degraded", hard, False),
             ("held_out_clean", clean, True), ("held_out_degraded", hard, True))
    total = {}
    for fi in range(folds):
        fold = set(sweeps[fi::folds])
        keep = np.array([n not in fold for n in ids])
        W, b = train(X[keep], y[keep], seed=fi)
        enc = lambda raw: blend(raw, W, b)
        pick = [i for i, t in enumerate(truth) if t in fold]
        for name, qs, held in tests:
            r = score(enc(emb), meta, [qs[i] for i in pick], [truth[i] for i in pick], held,
                      rerank=rerank, limit=(limit // folds if limit else None), seed=seed,
                      encode=enc)
            t = total.setdefault(name, {})
            for k, v in r["counts"].items():
                t[k] = t.get(k, 0) + v
    out = {"building_id": str(building_id), "blend": BLEND,
           "method": "trained layer, 5-fold" + (" + point matching" if rerank else "")}
    for name, c in total.items():
        n = c["n"]
        out[name] = {"queries": n, "room_top1": round(c["room1"] / n, 3), "room_top5": round(c["room5"] / n, 3),
                     "top1": round(c["top1"] / n, 3), "top5": round(c["top5"] / n, 3)}
        if rerank:
            out[name]["confirmed_share"] = round(c["conf"] / n, 3)
            out[name]["room_top1_when_confirmed"] = round(c["conf_right"] / c["conf"], 3) if c["conf"] else None
    return out


if __name__ == "__main__":
    import json
    args = sys.argv[2:]
    if "--cv" in args:
        limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
        res = cross_validate(sys.argv[1], rerank="--rerank" in args, limit=limit)
        if "--json" in args:
            json.dump(res, open(args[args.index("--json") + 1], "w"), indent=1)
        print(json.dumps(res, indent=1))
    else:
        print(json.dumps(fit(sys.argv[1])))
