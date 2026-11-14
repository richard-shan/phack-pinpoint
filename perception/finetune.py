"""Fine-tune the embedding model on scans of many buildings. Needs a real GPU.

    python -m perception.finetune render buildings.json views.npy    # CPU, parallel
    python -m perception.finetune train views.npy out_dir            # GPU

The frozen model knows what places look like in general. Fine-tuning teaches it the
task we need: two photos taken in the same room should be close, two photos of
different rooms in the same building should be far, whatever the heading, tilt,
zoom or photo quality. Labels are free: Matterport gives every scanned position a
room id. Training views are random camera views cut from each panorama, half of
them degraded like a phone photo.

Train on buildings that are NOT the ones you test on. buildings.json is
[{"building_id": ..., "models": [matterport model ids]}]; their panoramas must
already be in cache/matterport (fetch_train.py does that).

RESULT, 14 Nov: this made matching WORSE and is not used. Trained on 62 scans
(382 rooms, 30,848 views) and tested on building 240517, which it never saw, room
top-1 fell from 0.83 to 0.74 after 500 steps and 0.64 after 1,000 (same spot, clean),
and from 0.49 to 0.43 and 0.40 held out. Pulling every view of a room together erases
the detail that tells look-alike rooms apart. Larger stock models (large, giant) and
448 px input were also tried and did not improve the held-out case. What does help
is the per-building layer in perception.head, blended with the stock embedding.
Kept for the record and in case someone tries a gentler objective.

The result (out_dir/dinov2_ft.pt) is a drop-in replacement for the stock weights:
put it at cache/models/dinov2_ft.pt and perception.embed loads it. Indexes built
with other weights must be rebuilt.
"""
import json
import os
import random
import sys
import time

import numpy as np

VIEWS_PER_SWEEP = 16
RENDER = 256          # rendered size; the centre 224 is kept, as the image processor does
KEEP = 224


def _render_sweep(args):
    """Worker: all training views for one sweep, as uint8 (V, 224, 224, 3) RGB."""
    import cv2
    from .crops import load_faces, view
    from .evaluate import degrade
    cv2.setNumThreads(1)
    face_dir, seed = args
    try:
        faces = load_faces([os.path.join(face_dir, "%d.jpg" % i) for i in range(6)])
    except Exception:
        return None
    rng = random.Random(seed)
    out = np.zeros((VIEWS_PER_SWEEP, KEEP, KEEP, 3), np.uint8)
    o = (RENDER - KEEP) // 2
    for i in range(VIEWS_PER_SWEEP):
        im = view(faces, rng.uniform(0, 360), rng.uniform(-25, 10), rng.uniform(50, 80), RENDER,
                  rng.uniform(-12, 12))
        if i % 2:
            im = degrade(im, rng)
        out[i] = im[o:o + KEEP, o:o + KEEP, ::-1]
    return out


def render(buildings_json, out_path, workers=24):
    from multiprocessing import Pool
    from .matterport import Matterport
    mp = Matterport()
    jobs, labels = [], []          # labels: (building, model, sweep, room)
    seen = set()
    for b in json.load(open(buildings_json)):
        for mid in b["models"]:
            if mid in seen:
                continue
            seen.add(mid)
            sweeps = os.path.join(mp.cache_dir, mid, "sweeps.json")
            if not os.path.exists(sweeps):
                continue
            rooms = mp.rooms(mid) if os.path.exists(os.path.join(mp.cache_dir, mid, "rooms.json")) else {}
            for loc in json.load(open(sweeps))["locations"]:
                d = os.path.join(mp.cache_dir, mid, "pano", "2k", loc["id"])
                if all(os.path.exists(os.path.join(d, "%d.jpg" % i)) for i in range(6)):
                    jobs.append((d, len(jobs)))
                    labels.append((b["building_id"], mid, loc["id"], rooms.get(loc["id"]) or loc["id"]))
    n = len(jobs)
    print("rendering %d sweeps x %d views" % (n, VIEWS_PER_SWEEP), flush=True)
    arr = np.lib.format.open_memmap(out_path, "w+", np.uint8, (n * VIEWS_PER_SWEEP, KEEP, KEEP, 3))
    ok = np.zeros(n, bool)
    t0 = time.time()
    with Pool(workers) as pool:
        for i, views in enumerate(pool.imap(_render_sweep, jobs, chunksize=4)):
            if views is not None:
                arr[i * VIEWS_PER_SWEEP:(i + 1) * VIEWS_PER_SWEEP] = views
                ok[i] = True
            if i % 1000 == 0:
                print("  %d/%d  %.0fs" % (i, n, time.time() - t0), flush=True)
    arr.flush()
    json.dump({"labels": labels, "ok": ok.tolist(), "views_per_sweep": VIEWS_PER_SWEEP},
              open(out_path + ".json", "w"))
    print("rendered %d sweeps (%d unreadable) in %.0fs" % (int(ok.sum()), int((~ok).sum()), time.time() - t0))


def train(views_path, out_dir, steps=3000, models_per_batch=16, rooms_per_model=4, views_per_room=8,
          lr=1e-5, temp=0.07, save_every=1000, seed=0):
    import torch
    from transformers import AutoModel
    from .embed import MODEL_NAME
    torch.manual_seed(seed)
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    arr = np.load(views_path, mmap_mode="r")
    info = json.load(open(views_path + ".json"))
    V = info["views_per_sweep"]
    by_model = {}                                  # model -> room -> [view indices]
    for i, ((b, mid, sweep, room), ok) in enumerate(zip(info["labels"], info["ok"])):
        if ok:
            by_model.setdefault(mid, {}).setdefault(room, []).extend(range(i * V, (i + 1) * V))
    # A model needs at least two rooms to supply "same building, different room" negatives.
    by_model = {m: r for m, r in by_model.items() if len(r) >= 2}
    models = sorted(by_model)
    weights = [sum(len(v) for v in by_model[m].values()) ** 0.5 for m in models]
    print("training on %d scans, %d rooms, %d views" % (
        len(models), sum(len(r) for r in by_model.values()),
        sum(len(v) for r in by_model.values() for v in r.values())), flush=True)

    dev = "cuda"
    model = AutoModel.from_pretrained(MODEL_NAME).to(dev).train()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.05)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps, pct_start=0.1)
    mean = torch.tensor([0.485, 0.456, 0.406], device=dev).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=dev).view(1, 3, 1, 1)
    t0, running = time.time(), 0.0
    for step in range(1, steps + 1):
        idx, lab = [], []
        for m in rng.choices(models, weights, k=models_per_batch):
            for room in rng.sample(sorted(by_model[m]), min(rooms_per_model, len(by_model[m]))):
                pool = by_model[m][room]
                idx += rng.choices(pool, k=views_per_room)
                lab += [hash((m, room))] * views_per_room
        order = np.argsort(idx)                    # sorted reads from the memmap
        x = torch.from_numpy(arr[np.array(idx)[order]]).to(dev).permute(0, 3, 1, 2).float() / 255
        y = torch.tensor(lab, device=dev)[torch.from_numpy(order).to(dev)]
        x = x * (1 + 0.2 * (torch.rand(len(x), 1, 1, 1, device=dev) - 0.5))     # brightness jitter
        x = (x.clamp(0, 1) - mean) / std
        with torch.autocast("cuda", dtype=torch.bfloat16):
            h = model(pixel_values=x).last_hidden_state
        h = h.float()
        nrm = torch.nn.functional.normalize
        z = nrm(torch.cat([nrm(h[:, 0], dim=-1), nrm(h[:, 1:].mean(1), dim=-1)], -1), dim=-1)  # as embed()
        sim = z @ z.T / temp
        eye = torch.eye(len(z), device=dev, dtype=torch.bool)
        same = (y[:, None] == y[None, :]) & ~eye
        logp = sim - torch.logsumexp(sim.masked_fill(eye, -1e9), dim=1, keepdim=True)
        has = same.sum(1) > 0
        loss = -((logp * same).sum(1)[has] / same.sum(1)[has]).mean()
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        running += loss.item()
        if step % 100 == 0:
            print("step %d  loss %.3f  %.0fs  (%d views/s)" % (
                step, running / 100, time.time() - t0, step * len(idx) / (time.time() - t0)), flush=True)
            running = 0.0
        if step % save_every == 0 or step == steps:
            torch.save(model.state_dict(), os.path.join(out_dir, "dinov2_ft_step%d.pt" % step))
    torch.save(model.state_dict(), os.path.join(out_dir, "dinov2_ft.pt"))
    print("saved", os.path.join(out_dir, "dinov2_ft.pt"))


if __name__ == "__main__":
    if sys.argv[1] == "render":
        render(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "train":
        extra = {}
        for a in sys.argv[4:]:
            k, v = a.lstrip("-").split("=")
            extra[k] = float(v) if "." in v or "e" in v else int(v)
        train(sys.argv[2], sys.argv[3], **extra)
