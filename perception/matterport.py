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
        """Download every sweep's faces for a model with a single API query."""
        locs = self.query(SKYBOX_QUERY % (model_id, resolution))["model"]["locations"]
        n = 0
        for loc in locs:
            d = os.path.join(self.cache_dir, model_id, "pano", resolution, loc["id"])
            urls = ((loc.get("panos") or [{}])[0].get("skybox") or {}).get("children") or []
            if len(urls) != 6:
                continue
            os.makedirs(d, exist_ok=True)
            for i, url in enumerate(urls):
                p = os.path.join(d, "%d.jpg" % i)
                if os.path.exists(p):
                    continue
                with open(p + ".part", "wb") as f:
                    f.write(_fetch(url, self.timeout))
                os.replace(p + ".part", p)
            n += 1
        return n
