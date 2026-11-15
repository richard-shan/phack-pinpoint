"""Matterport model API client. Standard library only; every call is cached to disk.

    from env.matterport import Matterport
    mp = Matterport()
    mp.sweeps("Hq6Dca1G95S")        # positions, floors, neighbors
    mp.pano("Hq6Dca1G95S/<sweep>")  # six cube-face image paths

Keys come from MATTERPORT_API_KEY / MATTERPORT_API_TOKEN, read from the
environment or from .env at the repo root.
"""
import base64
import contextlib
import json
import os
import random
import socket
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = "https://api.matterport.com/api/models/graph"

SWEEPS_QUERY = """{ model(id: "%s") { id name
  floors { id label sequence }
  locations { id position { x y z } floor { id sequence } neighbors } } }"""

# The image URLs are signed and expire, so they are fetched fresh and only the
# downloaded images are cached.
SKYBOX_QUERY = """{ model(id: "%s") { locations { id
  panos { id skybox(resolution: "%s") { status format children } } } } }"""


@contextlib.contextmanager
def _ipv4_only():
    """Resolve hosts to IPv4 only for the duration of a request. On networks with
    broken IPv6, Python waits out a full timeout per IPv6 address before falling
    back (about 40 s per call at the venue); curl and browsers do not."""
    real = socket.getaddrinfo

    def v4(host, port, family=0, *args, **kwargs):
        return real(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = v4
    try:
        yield
    finally:
        socket.getaddrinfo = real


BACKOFFS = {}      # HTTP status -> times we backed off, for monitoring rate limits


def _fetch(req_or_url, timeout, tries=7):
    """GET or POST with IPv4 only, backing off on rate limits and transient errors.
    429 = rate limited (the key and the image server are shared by the whole team)."""
    for attempt in range(tries):
        try:
            with _ipv4_only():
                with urllib.request.urlopen(req_or_url, timeout=timeout) as r:
                    return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == tries - 1:
                raise
            wait = float(e.headers.get("Retry-After") or 0) or min(5 * 2 ** attempt, 120)
            BACKOFFS[e.code] = BACKOFFS.get(e.code, 0) + 1
            time.sleep(wait + random.random())


def load_dotenv(path=None):
    """Read KEY=VALUE lines into os.environ without overriding what is set."""
    path = path or os.path.join(REPO, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class Matterport:
    def __init__(self, cache_dir=None, timeout=60):
        load_dotenv()
        self.key = os.environ.get("MATTERPORT_API_KEY")
        self.token = os.environ.get("MATTERPORT_API_TOKEN")
        self.cache_dir = cache_dir or os.path.join(REPO, "cache", "matterport")
        self.timeout = timeout

    def query(self, q):
        if not (self.key and self.token):
            raise RuntimeError("MATTERPORT_API_KEY and MATTERPORT_API_TOKEN are not set (see .env.example)")
        auth = base64.b64encode(("%s:%s" % (self.key, self.token)).encode()).decode()
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps({"query": q}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Basic " + auth})
        out = json.loads(_fetch(req, self.timeout))
        if out.get("errors"):
            raise RuntimeError("Matterport API error: %s" % json.dumps(out["errors"])[:500])
        return out["data"]

    def sweeps(self, model_id, refresh=False):
        """The model record: name, floors, and every sweep with position, floor and neighbors."""
        path = os.path.join(self.cache_dir, model_id, "sweeps.json")
        if os.path.exists(path) and not refresh:
            return json.load(open(path))
        model = self.query(SWEEPS_QUERY % model_id)["model"]
        if model is None:
            raise RuntimeError("model %s not found or not shared with this key" % model_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(model, open(path, "w"), indent=1)
        return model

    def rooms(self, model_id, refresh=False):
        """sweep id -> Matterport room id (rooms have ids but no names)."""
        path = os.path.join(self.cache_dir, model_id, "rooms.json")
        if os.path.exists(path) and not refresh:
            return json.load(open(path))
        locs = self.query('{ model(id: "%s") { locations { id room { id } } } }' % model_id)["model"]["locations"]
        out = {l["id"]: (l.get("room") or {}).get("id") for l in locs}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(out, open(path, "w"), indent=1)
        return out

    def rotations(self, model_id, refresh=False):
        """sweep id -> panorama rotation quaternion {x, y, z, w} in the scan's frame."""
        path = os.path.join(self.cache_dir, model_id, "rotations.json")
        if os.path.exists(path) and not refresh:
            return json.load(open(path))
        locs = self.query('{ model(id: "%s") { locations { id panos { rotation { x y z w } } } } }'
                          % model_id)["model"]["locations"]
        out = {l["id"]: l["panos"][0]["rotation"] for l in locs if l.get("panos")}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(out, open(path, "w"), indent=1)
        return out

    def pano(self, pano_ref, resolution="2k"):
        """Six cube-face images for one sweep, as local paths. `pano_ref` is
        "<model_id>/<sweep_id>". Downloads all of a model's sweeps it is asked for
        lazily, one sweep at a time."""
        model_id, sweep_id = pano_ref.split("/", 1)
        d = os.path.join(self.cache_dir, model_id, "pano", resolution, sweep_id)
        paths = [os.path.join(d, "%d.jpg" % i) for i in range(6)]
        if all(os.path.exists(p) for p in paths):
            return paths
        locs = self.query(SKYBOX_QUERY % (model_id, resolution))["model"]["locations"]
        loc = next((l for l in locs if l["id"] == sweep_id), None)
        if not loc or not loc["panos"] or not loc["panos"][0].get("skybox"):
            return None
        urls = loc["panos"][0]["skybox"].get("children") or []
        if len(urls) != 6:
            return None
        os.makedirs(d, exist_ok=True)
        for p, url in zip(paths, urls):
            with open(p + ".part", "wb") as f:
                f.write(_fetch(url, self.timeout))
            os.replace(p + ".part", p)
        return paths

    def prefetch_panos(self, model_id, resolution="2k"):
        """Download every sweep's faces for a model: one API query, then the images
        from Matterport's CDN in parallel (PINPOINT_CDN_THREADS, default 16). The API
        is rate-limited; the CDN is not the bottleneck, so only image fetches fan out."""
        from concurrent.futures import ThreadPoolExecutor
        locs = self.query(SKYBOX_QUERY % (model_id, resolution))["model"]["locations"]
        jobs, n = [], 0
        for loc in locs:
            d = os.path.join(self.cache_dir, model_id, "pano", resolution, loc["id"])
            urls = ((loc.get("panos") or [{}])[0].get("skybox") or {}).get("children") or []
            if len(urls) != 6:
                continue
            os.makedirs(d, exist_ok=True)
            n += 1
            for i, url in enumerate(urls):
                p = os.path.join(d, "%d.jpg" % i)
                if not os.path.exists(p):
                    jobs.append((url, p))

        def get(job):
            url, p = job
            with open(p + ".part", "wb") as f:
                f.write(_fetch(url, self.timeout))
            os.replace(p + ".part", p)

        with ThreadPoolExecutor(int(os.environ.get("PINPOINT_CDN_THREADS", "16"))) as pool:
            list(pool.map(get, jobs))
        return n

    BUNDLE_QUERY = """{ model(id: "%s") { id name
      floors { id label sequence }
      locations { id position { x y z } floor { id sequence } neighbors room { id }
        panos { skybox(resolution: "%s") { children } } }
      assets { meshes { format resolution status filename url }
               floorplans { format status filename url } } } }"""

    def fetch_all(self, model_id, resolution="2k"):
        """Everything for one scan with ONE API call (the API is the rate-limited part):
        positions, rooms, panoramas and meshes/floor plans. Writes the same cache files
        as sweeps(), rooms(), prefetch_panos() and assets(). Returns the position count."""
        from concurrent.futures import ThreadPoolExecutor
        base = os.path.join(self.cache_dir, model_id)
        m = self.query(self.BUNDLE_QUERY % (model_id, resolution))["model"]
        if m is None:
            raise RuntimeError("model %s not found or not shared with this key" % model_id)
        os.makedirs(base, exist_ok=True)
        locs = m["locations"]
        json.dump({"id": m["id"], "name": m["name"], "floors": m["floors"],
                   "locations": [{k: l[k] for k in ("id", "position", "floor", "neighbors")} for l in locs]},
                  open(os.path.join(base, "sweeps.json"), "w"), indent=1)
        json.dump({l["id"]: (l.get("room") or {}).get("id") for l in locs},
                  open(os.path.join(base, "rooms.json"), "w"), indent=1)
        jobs, n = [], 0
        for l in locs:
            urls = ((l.get("panos") or [{}])[0].get("skybox") or {}).get("children") or []
            if len(urls) != 6:
                continue
            d = os.path.join(base, "pano", resolution, l["id"])
            os.makedirs(d, exist_ok=True)
            n += 1
            jobs += [(u, os.path.join(d, "%d.jpg" % i)) for i, u in enumerate(urls)
                     if not os.path.exists(os.path.join(d, "%d.jpg" % i))]
        adir = os.path.join(base, "assets")
        os.makedirs(adir, exist_ok=True)
        saved, seen = [], set()
        for kind in ("meshes", "floorplans"):
            for item in (m.get("assets") or {}).get(kind) or []:
                if item.get("status") != "available" or not item.get("url"):
                    continue
                rel = item["url"].split("?")[0].split("/assets/", 1)[-1]
                if rel in seen:
                    continue
                seen.add(rel)
                name = rel.replace("/", "__")
                jobs.append((item["url"], os.path.join(adir, name)))
                saved.append({"kind": kind, "format": item["format"], "resolution": item.get("resolution"),
                              "file": name})

        def get(job):
            url, p = job
            if os.path.exists(p):
                return
            with open(p + ".part", "wb") as f:
                f.write(_fetch(url, self.timeout))
            os.replace(p + ".part", p)

        with ThreadPoolExecutor(int(os.environ.get("PINPOINT_CDN_THREADS", "16"))) as pool:
            list(pool.map(get, jobs))
        for f in saved:
            f["bytes"] = os.path.getsize(os.path.join(adir, f["file"]))
        json.dump(saved, open(os.path.join(adir, "assets.json"), "w"), indent=1)
        return n

    ASSETS_QUERY = """{ model(id: "%s") { assets {
      meshes { format resolution status filename url }
      floorplans { format status filename url } } } }"""

    def assets(self, model_id):
        """Download the scan's 3D meshes (OBJ) and floor plans (JPG, PNG, DXF). Returns
        the list of files saved under cache/matterport/<model>/assets/."""
        d = os.path.join(self.cache_dir, model_id, "assets")
        index = os.path.join(d, "assets.json")
        if os.path.exists(index):
            return json.load(open(index))
        a = self.query(self.ASSETS_QUERY % model_id)["model"]["assets"]
        os.makedirs(d, exist_ok=True)
        saved, seen = [], set()
        for kind in ("meshes", "floorplans"):
            for item in a.get(kind) or []:
                if item.get("status") != "available" or not item.get("url"):
                    continue
                # Several renders (per floor, per style) share a filename; name each by
                # its path under assets/, which is unique.
                rel = item["url"].split("?")[0].split("/assets/", 1)[-1]
                if rel in seen:
                    continue
                seen.add(rel)
                name = rel.replace("/", "__")
                path = os.path.join(d, name)
                if not os.path.exists(path):
                    with open(path + ".part", "wb") as f:
                        f.write(_fetch(item["url"], self.timeout))
                    os.replace(path + ".part", path)
                saved.append({"kind": kind, "format": item["format"], "resolution": item.get("resolution"),
                              "file": name, "bytes": os.path.getsize(path)})
        json.dump(saved, open(index, "w"), indent=1)
        return saved
