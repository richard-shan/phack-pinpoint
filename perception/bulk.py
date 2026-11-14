"""Index many buildings unattended. Resumable; safe to stop and restart.

    python -m perception.bulk download buildings.json     # panoramas, politely
    python -m perception.bulk index buildings.json        # index each building once its scans are in
    python -m perception.bulk status buildings.json

buildings.json is [{"building_id": ..., "models": [matterport model ids], ...}], in
priority order. Run `download` and `index` as two processes; `index` waits for
`download`. Indexes are built without saving the 52 view images per position (they
would be about 3 MB per position); match_photo re-renders the few views it needs
from the panoramas, so matching these buildings must run on a machine that has
cache/matterport for them.

Matterport rate-limits by caller. `download` uses two connections, backs off on
429, and pauses five minutes after a scan fails outright.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from .matterport import REPO, Matterport

STATE = os.path.join(REPO, "cache", "bulk")


def _mark(model_id, status):
    os.makedirs(STATE, exist_ok=True)
    open(os.path.join(STATE, "%s.%s" % (model_id, status)), "w").close()


def _is(model_id, status):
    return os.path.exists(os.path.join(STATE, "%s.%s" % (model_id, status)))


def download(buildings, connections=2):
    mp = Matterport(timeout=90)
    jobs = []
    for b in buildings:
        for m in b["models"]:
            if m not in jobs and not _is(m, "done") and not _is(m, "skip"):
                jobs.append(m)
    print("%d scans to download" % len(jobs), flush=True)
    t0 = time.time()
    count = [0, 0]

    def go(m):
        for attempt in range(4):
            try:
                mp.sweeps(m)
                mp.rooms(m)
                n = mp.prefetch_panos(m)
                _mark(m, "done")
                count[0] += 1
                count[1] += n
                return
            except Exception as e:
                msg = repr(e)
                if "not found" in msg or "not shared" in msg or "403" in msg or "404" in msg:
                    _mark(m, "skip")
                    print("skip %s: %s" % (m, msg[:100]), flush=True)
                    return
                print("retry %s (%d): %s" % (m, attempt + 1, msg[:100]), flush=True)
                time.sleep(300)
        print("gave up on %s for this run" % m, flush=True)

    with ThreadPoolExecutor(connections) as ex:
        for i, _ in enumerate(ex.map(go, jobs)):
            if i % 10 == 0:
                print("downloaded %d scans, %d positions, %.0f min" % (count[0], count[1], (time.time() - t0) / 60),
                      flush=True)
    print("DOWNLOAD FINISHED: %d scans, %d positions, %.0f min" % (count[0], count[1], (time.time() - t0) / 60),
          flush=True)


def _indexed(building_id):
    from .index import index_dir
    return os.path.exists(os.path.join(index_dir(building_id), "emb.npy"))


def index(buildings, wait=True):
    from .index import build
    while True:
        pending, built = 0, 0
        for b in buildings:
            if _indexed(b["building_id"]):
                continue
            if not all(_is(m, "done") or _is(m, "skip") for m in b["models"]):
                pending += 1
                continue
            models = [m for m in b["models"] if _is(m, "done")]
            if not models:
                continue
            t = time.time()
            try:
                r = build(b["building_id"], model_ids=models, save_crops=False, quiet=True)
                print("indexed %s: %d positions, %d views, %.0fs" % (
                    b["building_id"], r["nodes"], r["views"], time.time() - t), flush=True)
                built += 1
            except Exception as e:
                print("index FAILED %s: %s" % (b["building_id"], repr(e)[:160]), flush=True)
                _mark("building_%s" % b["building_id"], "indexfail")
        if not wait or (pending == 0 and built == 0):
            break
        if built == 0:
            time.sleep(60)
    print("INDEX FINISHED", flush=True)


def status(buildings):
    scans = {m for b in buildings for m in b["models"]}
    done = sum(_is(m, "done") for m in scans)
    skip = sum(_is(m, "skip") for m in scans)
    idx = [b for b in buildings if _indexed(b["building_id"])]
    positions = 0
    for b in idx:
        from .index import index_dir
        positions += len({r["node_id"] for r in json.load(open(os.path.join(index_dir(b["building_id"]), "meta.json")))["rows"]})
    print("scans: %d downloaded, %d unavailable, %d to go (of %d)" % (done, skip, len(scans) - done - skip, len(scans)))
    print("buildings indexed: %d of %d (%d positions)" % (len(idx), len(buildings), positions))


if __name__ == "__main__":
    B = json.load(open(sys.argv[2]))
    {"download": download, "index": index, "status": status}[sys.argv[1]](B)
