"""Use the perception tools on the team GPU from any laptop. Python 3 and SSH only:
no models, no cache, no installs.

    from perception import remote
    remote.match_photo("10352309", "photo.jpg", k=5)     # same shape as perception.tools
    remote.buildings()

    python3 -m perception.remote buildings
    python3 -m perception.remote match 10352309 photo.jpg [k]
    python3 -m perception.remote sync                       # push this laptop's perception/ code to the GPU
    python3 -m perception.remote run perception.index 12345 # run any perception command on the GPU
    python3 -m perception.remote restart                    # restart the GPU service after a sync

Needs your SSH key on the instance (ask whoever runs it to add your public key).
The first call opens an SSH tunnel to the service in the background. Settings:
PINPOINT_GPU_HOST (default ubuntu@192.222.54.123), PINPOINT_GPU_DIR,
PINPOINT_PORT (8765).
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

HOST = os.environ.get("PINPOINT_GPU_HOST", "ubuntu@192.222.54.123")
DIR = os.environ.get("PINPOINT_GPU_DIR", "/lambda/nfs/phack/perception-train")
PORT = int(os.environ.get("PINPOINT_PORT", "8765"))
BASE = "http://127.0.0.1:%d" % PORT
EVIDENCE = os.path.join(tempfile.gettempdir(), "pinpoint_evidence")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]


def _port_open():
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def tunnel():
    """Open the SSH tunnel to the GPU service if it is not open already."""
    if _port_open():
        return
    subprocess.run(SSH + ["-o", "ExitOnForwardFailure=yes", "-f", "-N",
                          "-L", "%d:127.0.0.1:%d" % (PORT, PORT), HOST], check=True)
    for _ in range(20):
        if _port_open():
            return
        time.sleep(0.25)
    raise RuntimeError("SSH tunnel to %s did not come up" % HOST)


def _call(method, path, params=None, body=None, raw=False, timeout=120):
    tunnel()
    url = BASE + path + ("?" + urlencode({k: v for k, v in (params or {}).items() if v is not None}) if params else "")
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = e.reason
        raise RuntimeError("GPU service: %s" % msg) from None
    except (urllib.error.URLError, ConnectionError) as e:
        raise RuntimeError("GPU service not answering (%s); start it with: python3 -m perception.remote restart"
                           % getattr(e, "reason", e)) from None
    return out if raw else json.loads(out)


def fetch(url_path):
    """Download a /file/... image from the service; returns a local path (cached)."""
    os.makedirs(EVIDENCE, exist_ok=True)
    local = os.path.join(EVIDENCE, url_path.replace("/file/", "").replace("/", "_"))
    if not os.path.exists(local):
        open(local, "wb").write(_call("GET", url_path, raw=True))
    return local


def _localize(obj):
    """Point *_path fields at local copies, so results work like perception.tools."""
    if isinstance(obj, list):
        return [_localize(x) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    for k, v in obj.items():
        if k.endswith("_url") and isinstance(v, str) and v.startswith("/file/"):
            out[k[:-4]] = fetch(v)
        elif isinstance(v, (dict, list)):
            out[k] = _localize(v)
    return out


def health():
    return _call("GET", "/health")


def buildings():
    return _call("GET", "/buildings")


def match_photo(building_id, image_path, k=10, download_evidence=True):
    r = _call("POST", "/match_photo", {"building_id": building_id, "k": k}, open(image_path, "rb").read())
    return _localize(r["candidates"]) if download_evidence else r["candidates"]


def node_attributes(building_id, node_id):
    return _localize(_call("GET", "/node_attributes", {"building_id": building_id, "node_id": node_id}))


def question_attributes(building_id):
    return _call("GET", "/question_attributes", {"building_id": building_id})


def find_equipment(building_id, type, near_node=None, limit=5):
    return _localize(_call("GET", "/find_equipment", {"building_id": building_id, "type": type,
                                                      "near_node": near_node, "limit": limit}))


def hazards_along(building_id, path):
    return _localize(_call("POST", "/hazards_along", {"building_id": building_id}, {"path": path}))


def landmark(building_id, node_id):
    return _localize(_call("GET", "/landmark", {"building_id": building_id, "node_id": node_id}))


def detect_site_features(building_id):
    return _localize(_call("GET", "/site_features", {"building_id": building_id}))


# ---------------------------------------------------------------- running code on the GPU
def sync():
    """Copy this checkout's perception/ code to the GPU (not data or caches)."""
    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(["rsync", "-az", "--exclude", "__pycache__", "--exclude", "data/", here,
                    "%s:%s/" % (HOST, DIR)], check=True)


def run(*args):
    """Run `python -m <args>` on the GPU in the project folder, streaming output."""
    cmd = "cd %s && set -a && . ./.env && [ -f ~/pinpoint_keys.env ] && . ~/pinpoint_keys.env; set +a; " \
          ".venv/bin/python -m %s" % (DIR, " ".join("'%s'" % a.replace("'", "") for a in args))
    return subprocess.run(SSH + [HOST, cmd]).returncode


def restart():
    """(Re)start the GPU service; waits until it answers."""
    cmd = ("cd %s && for p in $(pgrep -u $(whoami) -f 'python -m perception[.]server'); do "
           "grep -q python /proc/$p/comm && [ \"$(readlink /proc/$p/cwd)\" = \"$PWD\" ] && kill $p; done; sleep 1; "
           "nohup .venv/bin/python -m perception.server > server.log 2>&1 < /dev/null &" % DIR)
    subprocess.run(SSH + [HOST, cmd], check=True)
    for _ in range(90):
        try:
            return health()
        except RuntimeError:
            time.sleep(2)
    raise RuntimeError("service did not come up; see server.log on the GPU")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
    elif a[0] == "buildings":
        for b in buildings():
            print("%-24s %5d positions  attributes:%s  site:%s" % (
                b["building_id"], b["positions"], "yes" if b["attributes"] else "no", "yes" if b["site"] else "no"))
    elif a[0] == "match":
        for c in match_photo(a[1], a[2], int(a[3]) if len(a) > 3 else 5):
            print("%-40s score %.3f  confirmed %-5s  %4d points  %s  evidence %s" % (
                c["node_id"], c["score"], c["confirmed"], c["inliers"], c["model_name"], c["evidence_path"]))
    elif a[0] == "sync":
        sync()
    elif a[0] == "run":
        sys.exit(run(*a[1:]))
    elif a[0] == "restart":
        print(restart())
    elif a[0] == "health":
        print(health())
    else:
        print(__doc__)
