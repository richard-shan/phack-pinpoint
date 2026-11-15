"""Perception tools as a small HTTP service, so any laptop can use the GPU instance.

Runs on the GPU instance, bound to localhost only; laptops reach it through an SSH
tunnel (perception/remote.py opens one automatically). SSH access to the instance is
the only credential.

    .venv/bin/python -m perception.server            # on the instance, port 8765

Endpoints (JSON unless noted):
    GET  /health
    GET  /buildings                                   indexed buildings and position counts
    POST /match_photo?building_id=..&k=..             body: the photo's raw bytes
    GET  /node_attributes?building_id=..&node_id=..
    GET  /question_attributes?building_id=..
    GET  /find_equipment?building_id=..&type=..&near_node=..&limit=..
    POST /hazards_along?building_id=..               body: {"path": [node ids]}
    GET  /landmark?building_id=..&node_id=..
    GET  /site_features?building_id=..               cached results only (no API spend)
    GET  /file/<path under cache/>                    images referenced by the above

Paths to images in responses are rewritten to "/file/..." URLs (key `*_url`) beside
the original path, so a client can fetch the evidence it needs.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .matterport import REPO

CACHE = os.path.join(REPO, "cache")
PORT = int(os.environ.get("PINPOINT_PORT", "8765"))
GPU = threading.Lock()          # one model call at a time on the GPU
MAX_UPLOAD = 25 * 1024 * 1024


def _local(path):
    """A path written on any machine -> the same file under this machine's cache/."""
    if not isinstance(path, str) or "/cache/" not in path:
        return None
    p = os.path.join(CACHE, path.split("/cache/", 1)[1])
    return p if os.path.exists(p) else None


def _with_urls(obj):
    """Add a `<key>_url` beside every value that names a file under cache/."""
    if isinstance(obj, list):
        return [_with_urls(x) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        out[k] = _with_urls(v)
        if isinstance(v, str):
            p = _local(v)
            if p:
                out[k + "_url"] = "/file/" + os.path.relpath(p, CACHE)
        elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            urls = [_local(x) for x in v]
            if all(urls):
                out[k + "_urls"] = ["/file/" + os.path.relpath(p, CACHE) for p in urls]
    return out


def _buildings():
    d = os.path.join(CACHE, "index")
    out = []
    for b in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        meta = os.path.join(d, b, "meta.json")
        if os.path.exists(os.path.join(d, b, "emb.npy")) and os.path.exists(meta):
            m = json.load(open(meta))
            out.append({"building_id": b, "positions": len({r["node_id"] for r in m["rows"]}),
                        "descriptor": m.get("descriptor", "dinov2"),
                        "attributes": os.path.exists(os.path.join(CACHE, "attributes", b, "_all.json")),
                        "site": os.path.exists(os.path.join(CACHE, "vexcel", b, "site.json"))})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "pinpoint-perception/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _q(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("body too large")
        return self.rfile.read(n)

    def _route(self, method):
        from . import tools
        url = urlparse(self.path)
        q = self._q()
        if method == "GET" and url.path.startswith("/file/"):
            rel = unquote(url.path[len("/file/"):])
            p = os.path.realpath(os.path.join(CACHE, rel))
            if not p.startswith(os.path.realpath(CACHE) + os.sep) or not os.path.isfile(p):
                return self._send(404, {"error": "no such file"})
            ctype = "image/png" if p.endswith(".png") else "image/jpeg" if re.search(r"\.jpe?g$", p) else \
                "application/octet-stream"
            return self._send(200, open(p, "rb").read(), ctype)
        if url.path == "/health":
            import torch
            return self._send(200, {"ok": True, "gpu": torch.cuda.is_available(),
                                    "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None})
        if url.path == "/buildings":
            return self._send(200, _buildings())
        b = q.get("building_id")
        if not b:
            return self._send(400, {"error": "building_id is required"})
        if method == "POST" and url.path == "/match_photo":
            data = self._body()
            ext = ".png" if data[:4] == b"\x89PNG" else ".heic" if b"ftypheic" in data[:32] else ".jpg"
            up = os.path.join(CACHE, "uploads")
            os.makedirs(up, exist_ok=True)
            path = os.path.join(up, hashlib.sha1(data).hexdigest()[:16] + ext)
            open(path, "wb").write(data)
            t = time.time()
            with GPU:
                res = tools.match_photo(b, path, int(q.get("k", 10)))
            return self._send(200, {"candidates": _with_urls(res), "seconds": round(time.time() - t, 2)})
        if method == "GET" and url.path == "/node_attributes":
            return self._send(200, _with_urls(tools.node_attributes(b, q["node_id"])))
        if method == "GET" and url.path == "/question_attributes":
            return self._send(200, tools.question_attributes(b))
        if method == "GET" and url.path == "/find_equipment":
            return self._send(200, _with_urls(tools.find_equipment(b, q["type"], q.get("near_node"),
                                                                    int(q.get("limit", 5)))))
        if method == "POST" and url.path == "/hazards_along":
            return self._send(200, _with_urls(tools.hazards_along(b, json.loads(self._body())["path"])))
        if method == "GET" and url.path == "/landmark":
            return self._send(200, _with_urls(tools.landmark(b, q["node_id"])))
        if method == "GET" and url.path == "/site_features":
            p = os.path.join(CACHE, "vexcel", b, "site.json")
            if not os.path.exists(p):
                return self._send(404, {"error": "no site result cached for building %s" % b})
            return self._send(200, _with_urls(json.load(open(p))))
        return self._send(404, {"error": "unknown endpoint %s %s" % (method, url.path)})

    def _safe(self, method):
        try:
            self._route(method)
        except FileNotFoundError as e:
            self._send(404, {"error": str(e)})
        except (KeyError, ValueError) as e:
            self._send(400, {"error": "%s: %s" % (type(e).__name__, e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])})

    def do_GET(self):
        self._safe("GET")

    def do_POST(self):
        self._safe("POST")


def main():
    from .embed import _load
    from .rerank import _load as _load_matcher
    print("loading models...", flush=True)
    _load()
    _load_matcher()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("perception server on 127.0.0.1:%d (%d buildings indexed)" % (PORT, len(_buildings())), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
