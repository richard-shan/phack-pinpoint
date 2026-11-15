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


def _past(until):
    """until: "HH:MM" UTC, or None."""
    if not until:
        return False
    h, m = map(int, until.split(":"))
    now = time.gmtime()
    return (now.tm_hour, now.tm_min) >= (h, m)


def _pacer(start, ceiling, every_s=900):
    """Raise the image-server cap by one every `every_s` seconds while no 429 has come
    back; on a 429, fall back to the last cap that ran clean and stop raising."""
    import threading
    from . import matterport as M

    def run():
        good, seen = start, M.BACKOFFS.get(429, 0)
        M.CDN_GATE.set(start)
        while True:
            time.sleep(every_s)
            now = M.BACKOFFS.get(429, 0)
            if now > seen:
                M.CDN_GATE.set(good)
                print("pacer: rate-limited at %d concurrent images; holding at %d" % (M.CDN_GATE.limit, good),
                      flush=True)
                return
            good = M.CDN_GATE.limit
            if good >= ceiling:
                return
            M.CDN_GATE.set(good + 1)
            print("pacer: %d concurrent images ran clean; trying %d" % (good, good + 1), flush=True)

    threading.Thread(target=run, daemon=True).start()


def download(buildings, connections=None, until=None, faces=(0, 1, 2, 3, 4, 5)):
    """until: stop starting new scans at this UTC time ("HH:MM"). faces: which cube
    faces to fetch; (1, 2, 3, 4) saves a third of the image requests."""
    connections = connections or int(os.environ.get("PINPOINT_BULK_CONNECTIONS", "2"))
    _pacer(int(os.environ.get("PINPOINT_CDN_LIMIT", "4")), int(os.environ.get("PINPOINT_CDN_CEILING", "12")))
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
        if _past(until):
            return
        for attempt in range(4):
            try:
                n = mp.fetch_all(m, faces=faces)      # one API call: positions, rooms, panoramas, meshes
                _mark(m, "done")
                _mark(m, "assets")
                count[0] += 1
                count[1] += n
                return
            except Exception as e:
                msg = repr(e)
                if any(x in msg for x in ("not found", "not shared", "403", "404", "410")):
                    _mark(m, "skip")
                    print("skip %s: %s" % (m, msg[:100]), flush=True)
                    return
                print("retry %s (%d): %s" % (m, attempt + 1, msg[:100]), flush=True)
                time.sleep(300)
        print("gave up on %s for this run" % m, flush=True)

    with ThreadPoolExecutor(connections) as ex:
        for i, _ in enumerate(ex.map(go, jobs)):
            if i % 10 == 0:
                from .matterport import BACKOFFS
                print("downloaded %d scans, %d positions, %.0f min, backoffs %s" % (
                    count[0], count[1], (time.time() - t0) / 60, BACKOFFS), flush=True)
    print("DOWNLOAD FINISHED: %d scans, %d positions, %.0f min" % (count[0], count[1], (time.time() - t0) / 60),
          flush=True)


def assets(buildings, connections=2, until=None):
    """Meshes and floor plans for scans whose panoramas are already downloaded."""
    mp = Matterport(timeout=120)
    todo = []
    for b in buildings:
        for m in b["models"]:
            if m not in todo and _is(m, "done") and not _is(m, "assets"):
                todo.append(m)
    print("%d scans need meshes and floor plans" % len(todo), flush=True)
    t0, count = time.time(), [0, 0]

    def go(m):
        if _past(until):
            return
        try:
            files = mp.assets(m)
            _mark(m, "assets")
            count[0] += 1
            count[1] += sum(f["bytes"] for f in files)
        except Exception as e:
            print("assets failed %s: %s" % (m, repr(e)[:100]), flush=True)

    with ThreadPoolExecutor(connections) as ex:
        for i, _ in enumerate(ex.map(go, todo)):
            if i % 25 == 0:
                print("assets for %d scans, %.1f GB, %.0f min" % (count[0], count[1] / 1e9, (time.time() - t0) / 60),
                      flush=True)
    print("ASSETS FINISHED: %d scans, %.1f GB" % (count[0], count[1] / 1e9), flush=True)


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


# Haiku 5.5 and Opus 5.5 list prices, dollars per million tokens (input, output).
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}
SPEND = os.path.join(STATE, "spend.json")


def _spend():
    return json.load(open(SPEND)) if os.path.exists(SPEND) else {"dollars": 0.0, "buildings": {}}


def _cost(model, tin, tout):
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return tin * pin / 1e6 + tout * pout / 1e6


def _site_tokens(obj, acc):
    if isinstance(obj, dict):
        if "_usage" in obj and "_model" in obj:
            acc.append((obj["_model"], obj["_usage"]["input"], obj["_usage"]["output"]))
        for v in obj.values():
            _site_tokens(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _site_tokens(v, acc)
    return acc


def _site_cost(building_id):
    """Claude spend of the site pass, from the vision responses it caches."""
    import glob
    from .site import site_dir
    acc = []
    for f in glob.glob(os.path.join(site_dir(building_id), "*vision.json")):
        try:
            _site_tokens(json.load(open(f)), acc)
        except ValueError:
            pass
    return sum(_cost(m, i, o) for m, i, o in acc)


def enrich(buildings, budget, wait=True, site=True):
    """Attributes, then site features, for each indexed building in list order, until
    the measured Claude spend reaches `budget` dollars. Resumable: spend is recorded
    in cache/bulk/spend.json and finished buildings are skipped."""
    from . import attributes
    from .site import detect_site_features, site_dir
    os.makedirs(STATE, exist_ok=True)
    ledger = _spend()
    print("budget $%.2f, already spent $%.2f" % (budget, ledger["dollars"]), flush=True)
    while True:
        did = 0
        for b in buildings:
            bid = b["building_id"]
            if ledger["dollars"] >= budget:
                print("BUDGET REACHED: $%.2f spent" % ledger["dollars"], flush=True)
                return
            if bid in ledger["buildings"] or not _indexed(bid) or ledger.get("failures", {}).get(bid, 0) >= 2:
                continue
            cost, notes = 0.0, []
            try:
                r = attributes.run(bid)
                cost += _cost(r["model"], r["tokens_in"], r["tokens_out"])
                notes.append("%d positions" % r["nodes"])
            except Exception as e:
                notes.append("attributes failed: %s" % repr(e)[:100])
            if site:
                try:
                    if not os.path.exists(os.path.join(site_dir(bid), "site.json")):
                        detect_site_features(bid)
                        cost += _site_cost(bid)
                        notes.append("site ok")
                except Exception as e:
                    notes.append("site failed: %s" % repr(e)[:100])
            ledger["dollars"] += cost
            failed = any("failed" in n for n in notes)
            tries = ledger.setdefault("failures", {}).get(bid, 0) + failed
            ledger["failures"][bid] = tries
            if failed and tries < 2:          # retry once on a later pass
                json.dump(ledger, open(SPEND, "w"), indent=1)
                print("will retry %s: %s" % (bid, "; ".join(notes)), flush=True)
                continue
            ledger["buildings"][bid] = {"dollars": round(cost, 4), "notes": notes}
            json.dump(ledger, open(SPEND, "w"), indent=1)
            print("enriched %s: $%.3f (total $%.2f) %s" % (bid, cost, ledger["dollars"], "; ".join(notes)),
                  flush=True)
            did += 1
        if not wait or did == 0 and all(_indexed(b["building_id"]) or b["building_id"] in ledger["buildings"]
                                        for b in buildings):
            break
        if did == 0:
            time.sleep(120)
    print("ENRICH FINISHED: $%.2f spent" % ledger["dollars"], flush=True)


def status(buildings):
    scans = {m for b in buildings for m in b["models"]}
    done = sum(_is(m, "done") for m in scans)
    skip = sum(_is(m, "skip") for m in scans)
    idx = [b for b in buildings if _indexed(b["building_id"])]
    positions = 0
    for b in idx:
        from .index import index_dir
        positions += len({r["node_id"] for r in json.load(open(os.path.join(index_dir(b["building_id"]), "meta.json")))["rows"]})
    print("scans: %d downloaded, %d unavailable, %d to go (of %d); meshes and floor plans for %d" % (
        done, skip, len(scans) - done - skip, len(scans), sum(_is(m, "assets") for m in scans)))
    print("buildings indexed: %d of %d (%d positions)" % (len(idx), len(buildings), positions))


if __name__ == "__main__":
    # python -m perception.bulk download|index|status|enrich buildings.json [--until HH:MM] [--budget D] [--no-site]
    B = json.load(open(sys.argv[2]))
    args = sys.argv[3:]
    opt = lambda k: args[args.index(k) + 1] if k in args else None
    if sys.argv[1] == "download":
        download(B, until=opt("--until"), faces=(1, 2, 3, 4) if "--side-faces" in args else tuple(range(6)))
    elif sys.argv[1] == "assets":
        assets(B, until=opt("--until"))
    elif sys.argv[1] == "index":
        index(B)
    elif sys.argv[1] == "enrich":
        enrich(B, float(opt("--budget") or 10), site="--no-site" not in args)
    else:
        status(B)
