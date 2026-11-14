"""Matterport GraphQL access for lane A, cached on disk (ported from the Threshold work).

The venue network is slow (~25-50 KB/s), so every external response is cached under
`env/cache/matterport/` and nothing is downloaded twice:

    <model>/model.json                 structure: floors, walls, rooms, sweeps (no signed URLs)
    <model>/geo.json                   lat/lon of every sweep, from the model's georeference
    <model>/floorplan_<seq>.<ext>      top-down floor plan image per floor
    <model>/urls_<res>.json            signed skybox URL templates, valid ~5 minutes
    <model>/faces/<res>/<sweep>_<i>.jpg  one cube face, fetched only when a view needs it

Signed skybox URLs share one token per model and resolution and expire after a few
minutes (`validUntil`). We refresh them when they are past `validUntil`, and on a 401/403
we re-query the model once and retry. Signed URLs and keys are never printed or logged.

Coordinates are Matterport's: meters, Z up. A sweep's `floor_xyz` is the floor point under
the camera and `pano_xyz` is the camera (about 1.3 m higher). `quat` is [x, y, z, w]; see
crop.py for how it maps the cube faces to world directions.
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

from . import config

RESOLUTIONS = ("low", "high", "2k", "4k")
# Measured on 21fi9ao6CEi (3 sweeps x 6 faces, Content-Length): face edge and bytes per face
#   low  =  256 px,   2-8 KB   (too soft for a 768 px crop)
#   high = 1024 px,  75-280 KB
#   2k   = 2048 px, 270-980 KB (~3.5x high)
#   4k   = 4096 px, 0.6-2.5 MB
# A 90-degree, 768 px crop samples 3-4 faces (two sides + floor). Side by side on doorway
# edges (21fi9ao6CEi, Hng19kP18tc), "2k" with crop.py's 2x supersampling is visibly sharper
# than "high" at grout lines, door frames and baseboards, so it is the default now that the
# network is fast. On a slow link pass resolution="high" (~0.5 MB per crop instead of ~1.8 MB).
DEFAULT_RESOLUTION = "2k"

MP_CACHE = config.CACHE / "matterport"
TIMEOUT = (10, 60)  # (connect, read) seconds per request; each request is retried once
_URL_SLACK_S = 20  # treat signed URLs as expired this many seconds before validUntil

_MODEL_QUERY = """
query($id: ID!) {
  model(id: $id) {
    id name created
    address { address locality administrativeArea postalCode countryCode }
    geocoordinates { latitude longitude altitude source
                     rotation { x y z w } translation { x y z } }
    floors { id label sequence
      vertices { id position { x y } }
      edges { id type thickness
              vertices { id }
              openings { id type width height relativeCenter lowerElevation label } } }
    rooms { id label floor { id } classifications { label } tags
            dimensions { width depth height areaFloor }
            boundary { edges { id } } }
    locations {
      id position { x y z }
      floor { id sequence } room { id } neighbors
      panos { id variant placement position { x y z } rotation { x y z w } resolutions }
    }
    mattertags { id label description position { x y z } floor { sequence } }
    labels { label position { x y z } floor { sequence } }
    assets { floorplans { format flags width height resolution origin { x y }
                          floor { sequence } status } }
  }
}"""

_FLOORPLAN_QUERY = """
query($id: ID!) {
  model(id: $id) { assets { floorplans { format flags width height resolution
                                         origin { x y } floor { sequence } status url validUntil } } }
}"""

_URLS_QUERY = """
query($id: ID!, $res: String!) {
  model(id: $id) {
    locations { id panos { skybox(resolution: $res) { status validUntil urlTemplate } } }
  }
}"""

_LIST_QUERY = """
query($q: String, $n: Int) {
  models(query: $q, pageSize: $n) {
    totalResults
    results { id name floors { count } rooms { count } }
  }
}"""

_lock = threading.RLock()
_session_obj: requests.Session | None = None
_url_mem: dict[tuple[str, str], dict] = {}


class MatterportError(RuntimeError):
    pass


# ------------------------------------------------------------------ plumbing
def _session() -> requests.Session:
    global _session_obj
    if _session_obj is None:
        _session_obj = requests.Session()
    return _session_obj


def _safe(s: str) -> str:
    """Keep IDs safe to use as file names."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", s):
        raise ValueError(f"unexpected id {s!r}")
    return s


def _model_dir(model_id: str) -> Path:
    return MP_CACHE / _safe(model_id)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _request(method: str, url: str, what: str, **kw) -> requests.Response:
    """HTTP with a timeout and one retry on network errors, 429 and 5xx.

    Errors are re-raised without the URL (signed query strings must not reach logs).
    """
    for attempt in range(2):
        try:
            r = _session().request(method, url, timeout=TIMEOUT, **kw)
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(1.0)
                continue
            raise MatterportError(f"{what}: {type(exc).__name__} after retry") from None
        if (r.status_code == 429 or r.status_code >= 500) and attempt == 0:
            time.sleep(2.0)
            continue
        return r
    raise AssertionError("unreachable")


def _gql(query: str, variables: dict | None = None) -> dict:
    r = _request("POST", config.MATTERPORT_URL, "Matterport GraphQL",
                 json={"query": query, "variables": variables or {}}, auth=config.matterport_auth())
    if r.status_code != 200:
        raise MatterportError(f"Matterport GraphQL HTTP {r.status_code}")
    body = r.json()
    if body.get("errors") and not body.get("data"):
        msgs = "; ".join(e.get("message", "?") for e in body["errors"])
        raise MatterportError(f"Matterport GraphQL error: {msgs}")
    return body["data"]


# ------------------------------------------------------------------ model structure
def _model_raw(model_id: str, refresh: bool = False) -> dict:
    path = _model_dir(model_id) / "model.json"
    with _lock:
        if path.exists() and not refresh:
            return json.loads(path.read_text())
        data = _gql(_MODEL_QUERY, {"id": model_id})
        model = data.get("model")
        if model is None:
            raise MatterportError(f"model {model_id} not found or not accessible")
        _write_atomic(path, json.dumps(model).encode())
        return model


def _xyz(p: dict | None) -> list[float] | None:
    return None if p is None else [p["x"], p["y"], p["z"]]


def get_sweeps(model_id: str, refresh: bool = False) -> dict[str, dict]:
    """Placed sweeps of a model, keyed by sweep (location) id.

    {sweep_id: {"id", "floor_xyz", "pano_xyz", "quat": [x, y, z, w], "floor_seq",
                "floor_id", "room_id", "neighbors", "placement"}}
    Unplaced sweeps are dropped, and neighbor lists only name placed sweeps.
    """
    raw = _model_raw(model_id, refresh)
    out: dict[str, dict] = {}
    for loc in raw.get("locations") or []:
        if not loc or not loc.get("panos"):
            continue
        pano = next((p for p in loc["panos"] if p.get("variant") == "default"), loc["panos"][0])
        placement = pano.get("placement") or "unknown"
        if placement == "unplaced":
            continue
        rot = pano.get("rotation") or {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
        floor = loc.get("floor") or {}
        out[loc["id"]] = {
            "id": loc["id"],
            "floor_xyz": _xyz(loc.get("position")),
            "pano_xyz": _xyz(pano.get("position")) or _xyz(loc.get("position")),
            "quat": [rot["x"], rot["y"], rot["z"], rot["w"]],
            "floor_seq": floor.get("sequence"),
            "floor_id": floor.get("id"),
            "room_id": (loc.get("room") or {}).get("id"),
            "neighbors": list(loc.get("neighbors") or []),
            "placement": placement,
            "resolutions": pano.get("resolutions") or [],
        }
    for s in out.values():
        s["neighbors"] = [n for n in s["neighbors"] if n in out and n != s["id"]]
    return out


def get_model_meta(model_id: str, refresh: bool = False) -> dict:
    """Name, address, geocoordinates, floors and rooms (with dimensions/classifications)."""
    raw = _model_raw(model_id, refresh)
    return {
        "id": raw.get("id", model_id),
        "name": raw.get("name"),
        "created": raw.get("created"),
        "address": raw.get("address"),
        "geocoordinates": raw.get("geocoordinates"),
        "floors": raw.get("floors") or [],
        "rooms": raw.get("rooms") or [],
        "mattertags": raw.get("mattertags") or [],
        "labels": raw.get("labels") or [],
        "floorplans": (raw.get("assets") or {}).get("floorplans") or [],
    }


def get_walls(model_id: str, refresh: bool = False) -> dict[int, list[dict]]:
    """Wall segments per floor sequence, in model coordinates (meters, same frame as sweeps).

    {floor_seq: [{"id", "type": "wall" | "invisible", "a": (x, y), "b": (x, y), "length",
                  "openings": [{"type": "doorway" | "opening" | "window" | "divider",
                                "center_m", "width", "height", "lower"}]}]}
    `center_m` is the distance of the opening's center from `a` along the wall. Many
    scans have no wall data at all; then every floor maps to an empty list.
    """
    raw = _model_raw(model_id, refresh)
    out: dict[int, list[dict]] = {}
    for f in raw.get("floors") or []:
        verts = {v["id"]: (v["position"]["x"], v["position"]["y"])
                 for v in f.get("vertices") or [] if v.get("position")}
        walls = []
        for e in f.get("edges") or []:
            ends = [verts.get(v["id"]) for v in e.get("vertices") or []]
            if len(ends) != 2 or None in ends:
                continue
            a, b = ends
            length = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            ops = [{"type": o.get("type"), "center_m": (o.get("relativeCenter") or 0.0) * length,
                    "width": o.get("width") or 0.0, "height": o.get("height"),
                    "lower": o.get("lowerElevation"), "label": o.get("label")}
                   for o in e.get("openings") or []]
            walls.append({"id": e["id"], "type": e.get("type"), "a": a, "b": b,
                          "length": length, "thickness": e.get("thickness"), "openings": ops})
        out[f.get("sequence")] = walls
    return out


def get_latlon(model_id: str, points: dict[str, list[float]], refresh: bool = False) -> dict:
    """{key: [lat, lon]} for model-space points {key: [x, y, z]}, using the scan's own
    georeference. Cached per key in <model>/geo.json. Returns {} for scans without one."""
    path = _model_dir(model_id) / "geo.json"
    with _lock:
        cached = json.loads(path.read_text()) if path.exists() and not refresh else {}
        todo = [k for k in points if k not in cached]
        for i in range(0, len(todo), 150):
            chunk = todo[i:i + 150]
            parts = " ".join(
                f"p{j}: geoLocationOf(modelLocation: {{x: {points[k][0]}, y: {points[k][1]}, "
                f"z: {points[k][2]}}}) {{ lat long status }}" for j, k in enumerate(chunk))
            data = _gql("query($id: ID!) { model(id: $id) { geocoordinates { " + parts + " } } }",
                        {"id": model_id})
            geo = ((data.get("model") or {}).get("geocoordinates")) or {}
            for j, k in enumerate(chunk):
                v = geo.get(f"p{j}")
                cached[k] = [float(v["lat"]), float(v["long"])] if v and v.get("lat") else None
        if todo:
            _write_atomic(path, json.dumps(cached).encode())
    return {k: cached.get(k) for k in points}


def get_model_xy(model_id: str, latlons: dict[str, list[float]], refresh: bool = False) -> dict:
    """{key: [x, y]} in model coordinates for {key: [lat, lon]}, using the scan's own
    georeference. Cached per lat/lon in <model>/geo_inverse.json."""
    path = _model_dir(model_id) / "geo_inverse.json"
    with _lock:
        cached = json.loads(path.read_text()) if path.exists() and not refresh else {}
        sig = {k: f"{v[0]:.7f},{v[1]:.7f}" for k, v in latlons.items()}
        todo = [k for k in latlons if sig[k] not in cached]
        if todo:
            parts = " ".join(
                f"p{j}: modelLocationOf(latitude: {latlons[k][0]}, longitude: {latlons[k][1]}, "
                f"altitude: 0) {{ x y z }}" for j, k in enumerate(todo))
            data = _gql("query($id: ID!) { model(id: $id) { geocoordinates { " + parts + " } } }",
                        {"id": model_id})
            geo = ((data.get("model") or {}).get("geocoordinates")) or {}
            for j, k in enumerate(todo):
                v = geo.get(f"p{j}")
                cached[sig[k]] = [v["x"], v["y"]] if v and v.get("x") is not None else None
            _write_atomic(path, json.dumps(cached).encode())
    return {k: cached.get(sig[k]) for k in latlons}


def get_floorplan(model_id: str, floor_seq: int = 0, flags: tuple[str, ...] = ("photogramy",),
                  fmt: str = "jpg") -> dict | None:
    """Download (once) the top-down floor plan of one floor.

    Returns {"path", "width", "height", "resolution" (pixels per meter), "origin": (x, y)}
    or None when the scan has no such plan. The exact-flags match picks the plain plan
    over the colored-room and measurement variants.
    """
    meta_path = _model_dir(model_id) / f"floorplan_{int(floor_seq)}_{'-'.join(flags)}.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if Path(meta["path"]).exists():
            return meta
    data = _gql(_FLOORPLAN_QUERY, {"id": model_id})
    plans = ((data.get("model") or {}).get("assets") or {}).get("floorplans") or []
    want = [p for p in plans if p.get("status") == "available" and p.get("url")
            and (p.get("floor") or {}).get("sequence") == floor_seq
            and p.get("format") == fmt and sorted(p.get("flags") or []) == sorted(flags)]
    if not want:
        return None
    best = max(want, key=lambda p: p.get("width") or 0)
    r = _request("GET", best["url"], f"floor plan download ({model_id}/{floor_seq})")
    if r.status_code != 200:
        raise MatterportError(f"floor plan download ({model_id}/{floor_seq}): HTTP {r.status_code}")
    img_path = _model_dir(model_id) / f"floorplan_{int(floor_seq)}_{'-'.join(flags)}.{fmt}"
    _write_atomic(img_path, r.content)
    origin = best.get("origin") or {}
    meta = {"path": str(img_path), "width": best.get("width"), "height": best.get("height"),
            "resolution": best.get("resolution"), "origin": [origin.get("x"), origin.get("y")],
            "flags": best.get("flags"), "floor_seq": floor_seq}
    _write_atomic(meta_path, json.dumps(meta).encode())
    return meta


def list_models(query: str = "*", page_size: int = 20, refresh: bool = False) -> list[dict]:
    """Search models visible to our token: [{id, name, floors, rooms}] (counts)."""
    key = re.sub(r"[^A-Za-z0-9]+", "_", query)[:60] or "all"
    path = MP_CACHE / "_lists" / f"{key}_{int(page_size)}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    data = _gql(_LIST_QUERY, {"q": query, "n": int(page_size)})
    res = [{"id": r["id"], "name": r.get("name"),
            "floors": (r.get("floors") or {}).get("count"),
            "rooms": (r.get("rooms") or {}).get("count")}
           for r in (data["models"]["results"] or [])]
    _write_atomic(path, json.dumps(res).encode())
    return res


# ------------------------------------------------------------------ skybox faces
def _parse_ts(s: str | None) -> float:
    if not s:
        return time.time() + 240
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def _url_templates(model_id: str, resolution: str, force: bool = False) -> dict:
    """{"valid_until": epoch, "t": {sweep_id: urlTemplate}} for one model + resolution."""
    k = (model_id, resolution)
    path = _model_dir(model_id) / f"urls_{resolution}.json"
    with _lock:
        now = time.time()
        if not force:
            cached = _url_mem.get(k)
            if cached is None and path.exists():
                cached = json.loads(path.read_text())
            if cached and cached["valid_until"] - _URL_SLACK_S > now:
                _url_mem[k] = cached
                return cached
        data = _gql(_URLS_QUERY, {"id": model_id, "res": resolution})
        templates, valid = {}, []
        for loc in (data.get("model") or {}).get("locations") or []:
            for pano in loc.get("panos") or []:
                sb = pano.get("skybox") or {}
                if sb.get("urlTemplate"):
                    templates.setdefault(loc["id"], sb["urlTemplate"])
                    valid.append(_parse_ts(sb.get("validUntil")))
        entry = {"valid_until": min(valid) if valid else now + 240, "t": templates}
        _url_mem[k] = entry
        _write_atomic(path, json.dumps(entry).encode())
        return entry


def _face_path(model_id: str, sweep_id: str, index: int, resolution: str) -> Path:
    return _model_dir(model_id) / "faces" / resolution / f"{_safe(sweep_id)}_{int(index)}.jpg"


def face_cached(model_id: str, sweep_id: str, index: int, resolution: str = DEFAULT_RESOLUTION) -> bool:
    return _face_path(model_id, sweep_id, index, resolution).exists()


def get_face_bytes(model_id: str, sweep_id: str, index: int,
                   resolution: str = DEFAULT_RESOLUTION) -> bytes:
    """JPEG bytes of cube face `index` (0-5) of a sweep, from cache or downloaded once."""
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}")
    if not 0 <= int(index) <= 5:
        raise ValueError("face index must be 0..5")
    path = _face_path(model_id, sweep_id, index, resolution)
    if path.exists():
        return path.read_bytes()
    for attempt in range(2):
        tmpl = _url_templates(model_id, resolution, force=attempt > 0)["t"].get(sweep_id)
        if tmpl is None:
            if attempt == 0:
                continue
            raise MatterportError(f"no {resolution} skybox for sweep {sweep_id} in {model_id}")
        where = f"face download ({model_id}/{sweep_id}/{index}/{resolution})"
        r = _request("GET", tmpl.replace("<face>", str(int(index))), where)
        if r.status_code in (401, 403) and attempt == 0:
            continue  # signed URL expired: re-query the model once and retry
        if r.status_code != 200:
            raise MatterportError(f"{where}: HTTP {r.status_code}")
        try:
            Image.open(io.BytesIO(r.content)).verify()  # never cache a truncated JPEG
        except Exception:
            raise MatterportError(f"{where}: response is not a valid image") from None
        _write_atomic(path, r.content)
        return r.content
    raise MatterportError(f"face download failed ({model_id}/{sweep_id}/{index}/{resolution})")


def get_face(model_id: str, sweep_id: str, index: int,
             resolution: str = DEFAULT_RESOLUTION) -> Image.Image:
    """Cube face `index` (0-5) of a sweep as an RGB image. Downloaded lazily, cached."""
    return Image.open(io.BytesIO(get_face_bytes(model_id, sweep_id, index, resolution))).convert("RGB")


def face_fetcher(model_id: str, sweep_id: str, resolution: str = DEFAULT_RESOLUTION):
    """A `get_face_fn(index) -> Image` for crop.render_view, bound to one sweep."""
    return lambda index: get_face(model_id, sweep_id, index, resolution)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Inspect a Matterport model (cached).")
    ap.add_argument("--list", metavar="QUERY", help="search models")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--model")
    a = ap.parse_args()
    if a.list:
        for m in list_models(a.list, a.n):
            print(f"{m['id']}  floors={m['floors']} rooms={m['rooms']}  {m['name']}")
    if a.model:
        meta, sw = get_model_meta(a.model), get_sweeps(a.model)
        edges = {tuple(sorted((s, n))) for s in sw for n in sw[s]["neighbors"]}
        print(meta["name"], "|", len(meta["floors"]), "floors |", len(meta["rooms"]), "rooms |",
              len(sw), "placed sweeps |", len(edges), "edges")
