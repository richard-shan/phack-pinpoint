"""detect_site_features: the outside approach to a building, from Vexcel aerial imagery.

    .venv/bin/python -m perception.site 240517            # cached after the first run
    .venv/bin/python -m perception.site 240517 --refresh

Pipeline (every external call is cached under cache/vexcel/<building_id>/):
  1. CoStar kit        -> the building's lat/lon
  2. Vexcel property   -> parcel outline and building footprints
  3. Vexcel ortho      -> current overhead tile around the building
  4. Vexcel detect     -> awnings/canopies, parking lots, carports (open-vocabulary)
  5. Claude vision     -> entrances, staging spots, obstructions as points on the tile
  6. Geometry          -> entrances snapped to the footprint, side computed, not guessed
  7. Disaster pass     -> post-disaster ("graysky") image of the same ground, if any,
                          compared with the current tile

Every item carries pixel [x, y] in the image at tile_path, latlon, a confidence,
the sources that produced it and an evidence_path. Entrances found from straight
overhead are inferred from canopies, walkways and recesses: treat them as
candidates to confirm, not surveyed doors.
"""
import base64
import json
import math
import os
import sys
import time
from typing import List, Literal

import cv2
import numpy as np
import requests
from pydantic import BaseModel
from shapely import wkt as shapely_wkt
from shapely.geometry import Point, box, shape
from shapely.ops import nearest_points

from .matterport import REPO, load_dotenv

API = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"
DISASTER_LAYER = "graysky"
MARGIN_M = 45.0          # ground shown around the building footprint
MAX_HALF_M = 140.0       # cap on tile half-size
NO_FOOTPRINT_HALF_M = 90.0
SNAP_M = 12.0            # an entrance further than this from the footprint is suspect
VISION_PX = 1280         # long side of the image sent to the vision model
DETECT_LABELS = ["awning", "parking lot", "carport"]
# Five calls per building set, about 3 cents each. Haiku put the main entrance 12 m
# from the canopy on the first test building; Opus put it on the canopy and found
# the second door. Override with PINPOINT_SITE_MODEL.
DEFAULT_MODEL = "claude-opus-5-5"
OBLIQUES = ["oblique-north", "oblique-south", "oblique-east", "oblique-west"]
CONF = {"high": 0.8, "medium": 0.6, "low": 0.35}
EVENT_NAMES = {
    "us-tx-harvey-hurricane-2017": "Hurricane Harvey (2017)",
    "us-la-laura-hurricane-2020": "Hurricane Laura (2020)",
    "us-ca-losangeles-fire-2025": "Los Angeles wildfires (January 2025)",
    "us-tx-houston-explosion-2020": "Houston industrial explosion (January 2020)",
}


# ---------------------------------------------------------------- plumbing
def site_dir(building_id):
    d = os.path.join(REPO, "cache", "vexcel", str(building_id))
    os.makedirs(d, exist_ok=True)
    return d


def _headers():
    load_dotenv()
    key = os.environ.get("VEXCEL_API_KEY")
    if not key:
        raise RuntimeError("VEXCEL_API_KEY is not set (see .env.example)")
    return {"X-API-KEY": key}


def _send(method, path, **kw):
    for attempt in range(6):
        r = requests.request(method, "%s/%s" % (API, path), headers=_headers(), timeout=300, **kw)
        if r.status_code != 429:
            return r
        time.sleep(min(2 ** attempt, 30))
    return r


def _cached_json(path, fetch, refresh):
    if os.path.exists(path) and not refresh:
        return json.load(open(path))
    data = fetch()
    json.dump(data, open(path, "w"), indent=1)
    return data


def locate(building_id):
    """Address and lat/lon from the CoStar kit."""
    load_dotenv()
    kit = os.environ.get("COSTAR_KIT_DIR")
    if not kit:
        raise RuntimeError("COSTAR_KIT_DIR is not set (see .env.example)")
    want = int(building_id)
    with open(os.path.join(kit, "data", "commercial-property.jsonl")) as f:
        for line in f:
            if ('"id": %d,' % want) in line[:40] or ('"id":%d,' % want) in line[:40]:
                p = json.loads(line)
                if p["id"] == want:
                    a = p["primaryAddress"]
                    return {"building_id": str(want), "lat": a["latitude"], "lon": a["longitude"],
                            "address": " ".join(str(a.get(k) or "") for k in
                                                ("streetNumber", "streetName", "streetType")).strip()
                                       + ", %s, %s" % (a.get("city"), a.get("state")),
                            "stories": p.get("numStoriesAboveGrade"),
                            "source": "costar:commercial-property/%d" % want}
    raise ValueError("building %s is not in the CoStar kit" % building_id)


def _meters(lat):
    return 111320.0, 111320.0 * math.cos(math.radians(lat))      # metres per degree lat, lon


class Tile:
    """An overhead image and the lon/lat rectangle it covers. The mapping is linear
    in lon/lat; over a tile this small the error against the imagery's own
    projection is about a metre at the edges."""

    def __init__(self, path, rect, width, height):
        self.path, self.rect, self.w, self.h = path, rect, width, height

    def to_pixel(self, lon, lat):
        lon0, lat0, lon1, lat1 = self.rect
        return [round((lon - lon0) / (lon1 - lon0) * self.w, 1),
                round((lat1 - lat) / (lat1 - lat0) * self.h, 1)]

    def to_lonlat(self, x, y):
        lon0, lat0, lon1, lat1 = self.rect
        return lon0 + x / self.w * (lon1 - lon0), lat1 - y / self.h * (lat1 - lat0)

    @property
    def meters_per_pixel(self):
        _, mlon = _meters((self.rect[1] + self.rect[3]) / 2)
        return (self.rect[2] - self.rect[0]) * mlon / self.w


# ---------------------------------------------------------------- Vexcel
def property_record(loc, d, refresh):
    """Parcel and building footprints at the building's point, or None."""
    def fetch():
        r = _send("GET", "property/extract", params={
            "wkt": "POINT(%s %s)" % (loc["lon"], loc["lat"]), "srid": 4326, "layer": LAYERS,
            "latest": "true", "include": "precomputed", "unit-system": "metric"})
        if r.status_code == 404:
            return {"none": True}
        r.raise_for_status()
        return r.json()
    j = _cached_json(os.path.join(d, "property.json"), fetch, refresh)
    return None if j.get("none") or not j.get("features") else j["features"][0]


def pick_footprint(prop, loc):
    """The building CoStar means: the footprint nearest its point, largest on ties."""
    if not prop:
        return None, None
    pt = Point(loc["lon"], loc["lat"])
    best = None
    for s in (prop.get("properties") or {}).get("structures") or []:
        if not s.get("footprint.wkt"):
            continue
        g = shapely_wkt.loads(s["footprint.wkt"])
        key = (round(g.distance(pt) * 111320 / 5), -(s.get("footprint.area") or 0))   # 5 m bins
        if best is None or key < best[0]:
            best = (key, g, s)
    return (best[1], best[2]) if best else (None, None)


def tile_rect(loc, footprint):
    mlat, mlon = _meters(loc["lat"])
    if footprint is not None:
        x0, y0, x1, y1 = footprint.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hx = min((x1 - x0) / 2 * mlon + MARGIN_M, MAX_HALF_M)
        hy = min((y1 - y0) / 2 * mlat + MARGIN_M, MAX_HALF_M)
    else:
        cx, cy, hx, hy = loc["lon"], loc["lat"], NO_FOOTPRINT_HALF_M, NO_FOOTPRINT_HALF_M
    return [cx - hx / mlon, cy - hy / mlat, cx + hx / mlon, cy + hy / mlat]


def fetch_tile(rect, path, refresh, layer=LAYERS, collection=None):
    """Download the ortho image covering rect. Returns a Tile, or None on 404."""
    if not os.path.exists(path) or refresh:
        body = {"wkt": box(*rect).wkt, "srid": 4326, "layer": layer,
                "crop": "bounding_box", "image-format": "jpeg"}
        if collection:
            body["collection"] = collection
        r = _send("POST", "ortho/extract", json=body)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise RuntimeError("ortho/extract failed (%s): %s" % (r.status_code, r.text[:300]))
        with open(path, "wb") as f:
            f.write(r.content)
    img = cv2.imread(path)
    return Tile(path, rect, img.shape[1], img.shape[0])


def collections(loc, d, layer, name, refresh):
    """Imagery collections covering the building's point, newest first."""
    def fetch():
        r = _send("GET", "ortho/collections", params={
            "wkt": "POINT(%s %s)" % (loc["lon"], loc["lat"]), "srid": 4326, "layer": layer,
            "include": "collection,layer,first-capture-date,last-capture-date,graysky-event,min-gsd"})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return [f["properties"] for f in r.json()["features"]]
    out = _cached_json(os.path.join(d, name), fetch, refresh)
    return sorted(out, key=lambda c: c.get("last-capture-date") or "", reverse=True)


def vexcel_detect(rect, d, refresh):
    """Open-vocabulary detections on the current ortho: [{label, confidence, lonlat, bbox_wkt}]."""
    def fetch():
        w = box(*rect).wkt
        r = _send("POST", "elements/detect", json={
            "wkt": w, "srid": 4326, "buffer-meter": 0,
            "images": [{"endpoint": "ortho/extract",
                        "request": {"layer": LAYERS, "srid": 4326, "wkt": w, "crop": "bounding_box"}}],
            "model-input": {"elements": [{"label": l, "model": "sam3", "detection-threshold": 0.35}
                                         for l in DETECT_LABELS],
                            "include": ["bbox", "confidence"]}})
        if not r.ok:
            return {"error": "%s %s" % (r.status_code, r.text[:200]), "features": []}
        return r.json()
    j = _cached_json(os.path.join(d, "detect.json"), fetch, refresh)
    out = []
    for ft in j.get("features", []):
        for det in ft["properties"].get("detections", []):
            g = shapely_wkt.loads(det["bbox"])
            out.append({"label": ft["properties"]["label"], "confidence": round(float(det["confidence"]), 2),
                        "lonlat": [g.centroid.x, g.centroid.y], "bbox_wkt": det["bbox"]})
    return out, j.get("error")


def fetch_obliques(loc, footprint, rect, d, refresh):
    """Angled views of the building from each side, newest capture, where the
    imagery has them. Returns {direction: path}. Side doors hidden from straight
    overhead show up here."""
    area = footprint.wkt if footprint is not None else box(*rect).wkt
    def fetch():
        r = _send("GET", "oriented/collections", params={
            "wkt": area, "srid": 4326, "layer": "urban", "product-type": "oblique",
            "include": "product-type,collection,last-capture-date"})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return [f["properties"] for f in r.json()["features"]]
    feats = _cached_json(os.path.join(d, "oblique_collections.json"), fetch, refresh)
    out = {}
    for direction in OBLIQUES:
        have = [f for f in feats if f["product-type"] == direction]
        if not have:
            continue
        path = os.path.join(d, "%s.jpg" % direction)
        if not os.path.exists(path) or refresh:
            r = _send("POST", "oriented/extract", json={
                "wkt": area, "srid": 4326, "layer": "urban", "product-type": direction,
                "rotation": "natural", "buffer-meter": 25, "crop": "bbox", "image-format": "jpeg/png",
                "drawing": [{"wkt": area, "outline-width": 3, "outline-color": "255,204,0",
                             "fill-color": "255,204,0", "transparency": 0}]})
            if not r.ok:
                continue
            with open(path, "wb") as f:
                f.write(r.content)
        out[direction] = path
    return out


def _shrunk(path, d, long_side=900):
    """A copy small enough to send to the vision model."""
    img = cv2.imread(path)
    if img is None:
        return None
    s = long_side / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    out = os.path.join(d, os.path.splitext(os.path.basename(path))[0] + "_vision.jpg")
    cv2.imwrite(out, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out


# ---------------------------------------------------------------- vision
class Entrance(BaseModel):
    x: float                 # 0-100, percent of image width, read off the ruler
    y: float                 # 0-100, percent of image height
    kind: Literal["main", "secondary", "service", "loading_dock", "unknown"]
    covered: bool            # under a canopy or awning
    description: str         # what in the image says this is an entrance
    confidence: Literal["high", "medium", "low"]


class Staging(BaseModel):
    x: float
    y: float
    vehicle: Literal["ambulance", "engine", "either"]
    surface: Literal["parking_aisle", "driveway", "street", "fire_lane", "loading_area"]
    description: str
    confidence: Literal["high", "medium", "low"]


class Obstruction(BaseModel):
    x: float
    y: float
    type: Literal["covered_parking", "trees", "fence_or_gate", "median_or_curb", "narrow_drive",
                  "dumpster", "parked_vehicles", "other"]
    description: str
    confidence: Literal["high", "medium", "low"]


class SiteView(BaseModel):
    entrances: List[Entrance]
    staging: List[Staging]
    obstructions: List[Obstruction]
    vehicle_access: str      # how a vehicle gets from the street into the lot
    summary: str


class Change(BaseModel):
    x: float
    y: float
    type: Literal["standing_water", "debris", "blocked_road", "damaged_structure", "burned_area", "other"]
    description: str
    confidence: Literal["high", "medium", "low"]


class DisasterView(BaseModel):
    water_visible: bool           # standing or flood water on the ground in the SECOND image
    damage_visible: bool
    approach_affected: Literal["none", "partly", "blocked", "cannot_tell"]
    changes: List[Change]         # only things visible in the second image; empty if nothing
    summary: str


SITE_PROMPT = """This is a straight-down aerial image of a commercial building and its surroundings. \
North is up. The building of interest is outlined in yellow. The ruler along the top and left edges and \
the faint grid mark position as a percentage of the image width (x) and height (y).

You are preparing the outside approach for an ambulance or fire crew responding to a medical call \
inside the outlined building. Report:

- entrances: doors into the outlined building. From overhead a door itself is hidden, so use the signs \
of one: an entrance canopy or awning, a walkway or plaza leading to the wall, accessible parking spaces \
clustered near a wall, a recess in the facade, a loading dock. Put the point on the building's edge \
where the door would be. Include only entrances you have a visible reason for and say the reason.
- staging: up to four spots where an ambulance or an engine could stop close to an entrance without \
blocking the lot: a parking aisle, driveway or fire lane on pavement, not inside a marked parking space \
and not under a roof.
- obstructions: things between the street and the entrances that would slow a vehicle or a stretcher: \
covered parking with low clearance, fences or gates, medians and curbs, tree cover, narrow drives.

Give x and y for each as percentages read from the ruler on the overhead image. Any further images \
are angled side views of the same building (also outlined in yellow) looking in the stated direction; \
use them to confirm or find doors, but every coordinate you give is on the overhead image. Report only \
what you can see. If the image \
does not show something clearly, lower the confidence rather than leave it out or invent detail."""

DISASTER_PROMPT = """Two straight-down aerial images of the same ground, north up, same framing. The \
first is a recent ordinary capture. The second was captured on %s right after %s. The building of \
interest is outlined in yellow in both; rulers and grid mark position as percentages.

Compare them for an emergency crew that had to reach the outlined building on the day of the second \
image. Report only what is visible in the second image: standing or flood water on pavement or grass, \
debris, blocked roads or drives, damaged or burned structures. Ordinary differences between the two \
captures (different parked cars, tree growth, shadows, season, repaving, image sharpness) are not \
damage and must not be listed. Dark, smooth pavement is usually shadow or new asphalt, not water; \
count water only where it clearly covers ground that is dry in the first image. If the second image \
shows no effects around this building, say so plainly and return an empty list."""


def vision_image(tile, footprint, out_path):
    """The tile shrunk for the vision model, with the footprint outlined and a percent ruler."""
    img = cv2.imread(tile.path)
    s = VISION_PX / max(img.shape[:2])
    img = cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    if footprint is not None:
        for poly in getattr(footprint, "geoms", [footprint]):
            pts = np.array([[p * s for p in tile.to_pixel(x, y)] for x, y in poly.exterior.coords], np.int32)
            cv2.polylines(img, [pts], True, (0, 220, 255), 2, cv2.LINE_AA)
    over = img.copy()
    for k in range(1, 10):
        cv2.line(over, (w * k // 10, 0), (w * k // 10, h), (255, 255, 255), 1)
        cv2.line(over, (0, h * k // 10), (w, h * k // 10), (255, 255, 255), 1)
    img = cv2.addWeighted(over, 0.35, img, 0.65, 0)
    for k in range(0, 11):
        x, y = min(w * k // 10, w - 1), min(h * k // 10, h - 1)
        for (px, py), txt in (((x + 3 if k < 10 else x - 34, 16), str(k * 10)),
                              ((3, y + 16 if k < 10 else y - 6), str(k * 10))):
            if k == 0 and (px, py) != (3, 16):
                continue
            cv2.putText(img, txt, (px, py), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, txt, (px, py), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(out_path, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out_path


def _image_block(path):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(open(path, "rb").read()).decode()}}


def _ask(content, schema, model):
    import anthropic
    load_dotenv()
    r = anthropic.Anthropic().messages.parse(
        model=model, max_tokens=16000, output_config={"effort": "low"},
        messages=[{"role": "user", "content": content}], output_format=schema)
    if r.stop_reason != "end_turn" or r.parsed_output is None:
        raise RuntimeError("vision model returned no result (stop_reason=%s)" % r.stop_reason)
    out = r.parsed_output.model_dump()
    out["_model"] = model
    out["_usage"] = {"input": r.usage.input_tokens, "output": r.usage.output_tokens}
    return out


def site_model():
    load_dotenv()
    return os.environ.get("PINPOINT_SITE_MODEL") or DEFAULT_MODEL


# ---------------------------------------------------------------- geometry
SIDES = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"]


def side_of(footprint, lon, lat):
    """Which way the wall nearest a point faces: the outward direction of the
    closest footprint edge. (Bearing from the centre mislabels long buildings.)"""
    mlat, mlon = _meters(lat)
    best = None
    for poly in getattr(footprint, "geoms", [footprint]):
        pts = [((x - lon) * mlon, (y - lat) * mlat) for x, y in poly.exterior.coords]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            if L2 == 0:
                continue
            t = max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
            cx, cy = ax + t * dx, ay + t * dy
            dd = math.hypot(cx, cy)
            if best is None or dd < best[0]:
                best = (dd, dx, dy, cx, cy, poly)
    _, dx, dy, cx, cy, poly = best
    n = math.hypot(dx, dy)
    nx, ny = dy / n, -dx / n                                   # one of the two normals
    probe = Point(lon + (cx + nx * 1.5) / mlon, lat + (cy + ny * 1.5) / mlat)
    if poly.contains(probe):
        nx, ny = -nx, -ny
    ang = math.degrees(math.atan2(ny, nx)) % 360
    return SIDES[int((ang + 22.5) // 45) % 8]


def dist_m(a, b):
    mlat, mlon = _meters(a[1])
    return math.hypot((a[0] - b[0]) * mlon, (a[1] - b[1]) * mlat)


def snap_to_footprint(footprint, lon, lat):
    """Nearest point on the building's edge and how far the original was from it."""
    edge = nearest_points(footprint.boundary, Point(lon, lat))[0]
    return edge.x, edge.y, dist_m((lon, lat), (edge.x, edge.y))


# ---------------------------------------------------------------- drawing
def _marker(img, xy, color, label, shape_):
    x, y = int(xy[0]), int(xy[1])
    r = max(10, img.shape[1] // 110)
    if shape_ == "circle":
        cv2.circle(img, (x, y), r, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.circle(img, (x, y), r, color, 3, cv2.LINE_AA)
    elif shape_ == "square":
        cv2.rectangle(img, (x - r, y - r), (x + r, y + r), (0, 0, 0), 5)
        cv2.rectangle(img, (x - r, y - r), (x + r, y + r), color, 3)
    else:
        for a, b in (((x - r, y - r), (x + r, y + r)), ((x - r, y + r), (x + r, y - r))):
            cv2.line(img, a, b, (0, 0, 0), 6, cv2.LINE_AA)
            cv2.line(img, a, b, color, 3, cv2.LINE_AA)
    fs = max(0.7, img.shape[1] / 1900)
    cv2.putText(img, label, (x + r + 4, y + r // 2), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 6, cv2.LINE_AA)
    cv2.putText(img, label, (x + r + 4, y + r // 2), cv2.FONT_HERSHEY_SIMPLEX, fs, color, 2, cv2.LINE_AA)


def draw_overlay(tile, footprint, groups, out_path, crops_dir=None):
    """groups: [(items, color, shape, label_key)]. Writes the overlay and, per item,
    a crop around it whose path is stored on the item as evidence_path."""
    img = cv2.imread(tile.path)
    if footprint is not None:
        for poly in getattr(footprint, "geoms", [footprint]):
            pts = np.array([tile.to_pixel(x, y) for x, y in poly.exterior.coords], np.int32)
            cv2.polylines(img, [pts], True, (0, 220, 255), 3, cv2.LINE_AA)
    for items, color, shape_, key in groups:
        for it in items:
            _marker(img, it["pixel"], color, it[key], shape_)
    cv2.imwrite(out_path, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if crops_dir:
        os.makedirs(crops_dir, exist_ok=True)
        half = max(160, int(22 / tile.meters_per_pixel))          # about 44 m across
        for items, _, _, key in groups:
            for it in items:
                x, y = int(it["pixel"][0]), int(it["pixel"][1])
                x0, y0 = max(0, min(x - half, tile.w - 2 * half)), max(0, min(y - half, tile.h - 2 * half))
                p = os.path.join(crops_dir, "%s.jpg" % it[key])
                cv2.imwrite(p, img[y0:y0 + 2 * half, x0:x0 + 2 * half], [cv2.IMWRITE_JPEG_QUALITY, 90])
                it["evidence_path"] = p
    return out_path


# ---------------------------------------------------------------- the tool
def _point(tile, v):
    """Vision percent coordinates -> pixel on the full tile and lon/lat."""
    x = min(max(v["x"], 0.0), 100.0) / 100 * tile.w
    y = min(max(v["y"], 0.0), 100.0) / 100 * tile.h
    lon, lat = tile.to_lonlat(x, y)
    return [round(x, 1), round(y, 1)], lon, lat


def _side_by_side(before, after, out_path, left, right, height=900):
    """Before and after on one image, for the console."""
    ims = []
    for path, label in ((before, left), (after, right)):
        im = cv2.imread(path)
        im = cv2.resize(im, (round(im.shape[1] * height / im.shape[0]), height), interpolation=cv2.INTER_AREA)
        cv2.putText(im, label, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(im, label, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        ims.append(im)
    cv2.imwrite(out_path, np.hstack([ims[0], np.full((height, 8, 3), 255, np.uint8), ims[1]]),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out_path


def disaster_pass(loc, d, rect, footprint, current_small, model, refresh, before_path=None, before_date=""):
    """Post-disaster imagery over the same ground, compared with the current tile."""
    cols = collections(loc, d, DISASTER_LAYER, "graysky_collections.json", refresh)
    out = {"covered": bool(cols), "events": [],
           "note": "Coverage means a post-event flight imaged this ground. It does not mean the "
                   "building was damaged; see each event's findings."}
    seen = set()
    for c in cols:
        event = c.get("graysky-event") or "unidentified event (%s)" % c["collection"]
        if event in seen:                     # newest collection per event
            continue
        seen.add(event)
        path = os.path.join(d, "disaster_%s.jpg" % c["collection"])
        tile = fetch_tile(rect, path, refresh, layer=DISASTER_LAYER, collection=c["collection"])
        ev = {"event": event, "event_name": EVENT_NAMES.get(event, event), "collection": c["collection"],
              "captured": [c.get("first-capture-date"), c.get("last-capture-date")],
              "source": "vexcel:%s/%s" % (DISASTER_LAYER, c["collection"])}
        if tile is None:
            ev["error"] = "collection lists this point but returned no image for the tile"
            out["events"].append(ev)
            continue
        small = vision_image(tile, footprint, os.path.join(d, "disaster_%s_vision.jpg" % c["collection"]))
        date = (c.get("last-capture-date") or "")[:10]

        def fetch():
            return _ask([{"type": "text", "text": "First image, recent ordinary capture:"},
                         _image_block(current_small),
                         {"type": "text", "text": "Second image, captured %s:" % date},
                         _image_block(small),
                         {"type": "text", "text": DISASTER_PROMPT % (date, ev["event_name"])}],
                        DisasterView, model)
        try:
            v = _cached_json(os.path.join(d, "disaster_%s_vision.json" % c["collection"]), fetch, refresh)
        except Exception as e:                # the image is still useful without the reading
            ev.update(after_path=path, error="vision comparison failed: %s" % str(e)[:160])
            out["events"].append(ev)
            continue
        changes = []
        for i, ch in enumerate(v["changes"]):
            pixel, lon, lat = _point(tile, ch)
            changes.append({"id": "D%d" % (i + 1), "type": ch["type"], "description": ch["description"],
                            "pixel": pixel, "latlon": [round(lat, 7), round(lon, 7)],
                            "confidence": CONF[ch["confidence"]], "sources": ["claude_vision:%s" % v["_model"]]})
        overlay = draw_overlay(tile, footprint, [(changes, (40, 40, 255), "cross", "id")],
                               os.path.join(d, "disaster_%s_overlay.jpg" % c["collection"]),
                               os.path.join(d, "crops_%s" % c["collection"]))
        if before_path:
            ev["before_path"] = before_path
            ev["before_after_path"] = _side_by_side(
                before_path, overlay, os.path.join(d, "disaster_%s_before_after.jpg" % c["collection"]),
                "Current %s" % before_date, "%s  %s" % (ev["event_name"], date))
        ev.update(after_path=path, water_visible=v["water_visible"],
                  damage_visible=v["damage_visible"], approach_affected=v["approach_affected"],
                  obstructions=changes, summary=v["summary"], evidence_path=overlay,
                  image_size=[tile.w, tile.h])
        out["events"].append(ev)
    return out


def detect_site_features(building_id, refresh=False, disaster=True, recompute=False):
    """Contract: {entrances[], staging[], obstructions[], tile_path} plus sources,
    confidence and evidence for each item, and a `disaster` block.

    refresh    redo every external call
    recompute  rebuild the result from the cached calls (after a code change)"""
    d = site_dir(building_id)
    result_path = os.path.join(d, "site.json")
    if os.path.exists(result_path) and not (refresh or recompute):
        return json.load(open(result_path))

    loc = locate(building_id)
    notes = []
    prop = property_record(loc, d, refresh)
    footprint, structure = pick_footprint(prop, loc)
    if footprint is None:
        notes.append("Vexcel has no building footprint at this point: tile is a square around the "
                     "CoStar location and entrances are not snapped to a building edge.")
    rect = tile_rect(loc, footprint)
    tile = fetch_tile(rect, os.path.join(d, "ortho.jpg"), refresh)
    if tile is None:
        raise RuntimeError("Vexcel has no ortho imagery at building %s" % building_id)
    current = collections(loc, d, LAYERS, "collections.json", refresh)
    detections, detect_error = vexcel_detect(rect, d, refresh)
    if detect_error:
        notes.append("Vexcel elements/detect failed (%s); result uses vision only." % detect_error)

    model = site_model()
    small = vision_image(tile, footprint, os.path.join(d, "ortho_vision.jpg"))
    obliques = {}
    try:
        obliques = fetch_obliques(loc, footprint, rect, d, refresh)
    except Exception as e:
        notes.append("Oblique views unavailable (%s)." % str(e)[:100])
    if not obliques:
        notes.append("No oblique (angled) imagery here: entrances come from the overhead view alone.")

    def ask_site():
        content = [{"type": "text", "text": "Overhead image:"}, _image_block(small)]
        for direction, path in obliques.items():
            sm = _shrunk(path, d)
            if sm:
                content += [{"type": "text", "text": "Side view, camera looking %s:" % direction.split("-")[1]},
                            _image_block(sm)]
        return _ask(content + [{"type": "text", "text": SITE_PROMPT}], SiteView, model)
    v = _cached_json(os.path.join(d, "vision.json"), ask_site, refresh)
    vsrc = "claude_vision:%s" % v["_model"]
    awnings = [x for x in detections if x["label"] == "awning"]

    entrances = []
    for e in v["entrances"]:
        pixel, lon, lat = _point(tile, e)
        item = {"kind": e["kind"], "covered": e["covered"], "description": e["description"],
                "confidence": CONF[e["confidence"]], "sources": [vsrc]}
        if footprint is not None:
            sx, sy, off = snap_to_footprint(footprint, lon, lat)
            item["snapped_m"] = round(off, 1)
            if off <= SNAP_M:
                lon, lat = sx, sy
                pixel = tile.to_pixel(lon, lat)
            else:
                item["confidence"] = min(item["confidence"], CONF["low"])
                item["warning"] = "point is %.0f m from the building outline" % off
            item["side"] = side_of(footprint, lon, lat)
        near = [a for a in awnings if dist_m((lon, lat), a["lonlat"]) <= 10]
        if near:
            item["sources"].append("vexcel_detect:awning@%.2f" % max(a["confidence"] for a in near))
            item["confidence"] = round(min(item["confidence"] + 0.1, 0.95), 2)
            for a in near:
                a["used"] = True
        item.update(pixel=pixel, latlon=[round(lat, 7), round(lon, 7)])
        entrances.append(item)
    for a in awnings:                         # a canopy on the building that vision did not report
        if a.get("used") or footprint is None:
            continue
        sx, sy, off = snap_to_footprint(footprint, *a["lonlat"])
        if off <= 6:
            entrances.append({"kind": "unknown", "covered": True, "confidence": round(0.5 * a["confidence"] + 0.2, 2),
                              "description": "awning or canopy detected against the building edge",
                              "sources": ["vexcel_detect:awning@%.2f" % a["confidence"]],
                              "snapped_m": round(off, 1), "side": side_of(footprint, sx, sy),
                              "pixel": tile.to_pixel(sx, sy), "latlon": [round(sy, 7), round(sx, 7)]})
    merged = []                               # two reports of the same door
    for e in sorted(entrances, key=lambda e: -e["confidence"]):
        if all(dist_m(e["latlon"][::-1], m["latlon"][::-1]) > 6 for m in merged):
            merged.append(e)
    entrances = merged
    for i, e in enumerate(entrances):
        e["entrance_id"] = "E%d" % (i + 1)

    staging = []
    for i, s in enumerate(v["staging"]):
        pixel, lon, lat = _point(tile, s)
        item = {"staging_id": "S%d" % (i + 1), "kind": s["surface"], "vehicle": s["vehicle"],
                "description": s["description"], "confidence": CONF[s["confidence"]], "sources": [vsrc],
                "pixel": pixel, "latlon": [round(lat, 7), round(lon, 7)]}
        if footprint is not None and footprint.contains(Point(lon, lat)):
            item["confidence"] = CONF["low"]
            item["warning"] = "point falls on the building roof"
        if entrances:
            n = min(entrances, key=lambda e: dist_m((lon, lat), e["latlon"][::-1]))
            item["nearest_entrance"] = n["entrance_id"]
            item["meters_to_entrance"] = round(dist_m((lon, lat), n["latlon"][::-1]), 1)
        lots = [x for x in detections if x["label"] == "parking lot"
                and shapely_wkt.loads(x["bbox_wkt"]).contains(Point(lon, lat))]
        if lots:
            item["sources"].append("vexcel_detect:parking lot@%.2f" % max(x["confidence"] for x in lots))
        staging.append(item)

    obstructions = []
    for o in v["obstructions"]:
        pixel, lon, lat = _point(tile, o)
        obstructions.append({"type": o["type"], "description": o["description"],
                             "confidence": CONF[o["confidence"]], "sources": [vsrc],
                             "pixel": pixel, "latlon": [round(lat, 7), round(lon, 7)]})
    for x in detections:
        if x["label"] == "carport":
            lon, lat = x["lonlat"]
            if all(dist_m((lon, lat), o["latlon"][::-1]) > 15 for o in obstructions):
                obstructions.append({"type": "covered_parking", "confidence": x["confidence"],
                                     "description": "carport or covered parking: check clearance",
                                     "sources": ["vexcel_detect:carport@%.2f" % x["confidence"]],
                                     "pixel": tile.to_pixel(lon, lat), "latlon": [round(lat, 7), round(lon, 7)]})
    for i, o in enumerate(obstructions):
        o["obstruction_id"] = "O%d" % (i + 1)

    overlay = draw_overlay(tile, footprint, [
        (entrances, (60, 220, 60), "circle", "entrance_id"),
        (staging, (255, 160, 40), "square", "staging_id"),
        (obstructions, (40, 40, 255), "cross", "obstruction_id")],
        os.path.join(d, "overlay.jpg"), os.path.join(d, "crops"))

    newest = current[0] if current else {}
    result = {
        "building_id": str(building_id), "address": loc["address"],
        "latlon": [loc["lat"], loc["lon"]], "location_source": loc["source"],
        "tile_path": tile.path, "tile_size": [tile.w, tile.h],
        "tile_bounds_lonlat": [round(x, 7) for x in rect],
        "meters_per_pixel": round(tile.meters_per_pixel, 4),
        "tile_capture": {"collection": newest.get("collection"), "layer": newest.get("layer"),
                         "captured": [newest.get("first-capture-date"), newest.get("last-capture-date")],
                         "note": "newest collection at this point; the tile is the best available of %s" % LAYERS},
        "evidence_path": overlay,
        "oblique_paths": obliques,
        "footprint_wkt": footprint.wkt if footprint is not None else None,
        "ground_elevation_m": (structure or {}).get("ground-elevation.value"),
        "entrances": entrances, "staging": staging, "obstructions": obstructions,
        "vehicle_access": v["vehicle_access"], "summary": v["summary"],
        "vision_model": v["_model"],
        "notes": notes + [
            "Entrances are inferred from overhead signs (canopies, walkways, recesses), snapped to the "
            "building outline. Confirm against the interior scan before relying on one.",
            "Pixel coordinates are [x, y] in the image at tile_path, origin top-left, north up.",
            "The yellow outline is the ground footprint. Roofs of taller buildings appear shifted a "
            "few metres from it because the overhead image is not perfectly vertical."],
    }
    if disaster:
        result["disaster"] = disaster_pass(loc, d, rect, footprint, small, model, refresh,
                                           before_path=tile.path,
                                           before_date=(newest.get("last-capture-date") or "")[:7])
    json.dump(result, open(result_path, "w"), indent=1)
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for b in args:
        r = detect_site_features(b, refresh="--refresh" in sys.argv, recompute="--recompute" in sys.argv)
        print(json.dumps({k: r[k] for k in ("building_id", "address", "tile_path", "evidence_path",
                                            "meters_per_pixel", "summary")}, indent=1))
        for e in r["entrances"]:
            print("  %s %-10s %-9s conf %.2f  %s  [%s]" % (e["entrance_id"], e.get("side", "?"), e["kind"],
                                                         e["confidence"], e["description"][:70], ", ".join(e["sources"])))
        for s in r["staging"]:
            print("  %s %-14s -> %s %sm  conf %.2f  %s" % (s["staging_id"], s["kind"], s.get("nearest_entrance"),
                                                           s.get("meters_to_entrance"), s["confidence"], s["description"][:60]))
        for o in r["obstructions"]:
            print("  %s %-16s conf %.2f  %s" % (o["obstruction_id"], o["type"], o["confidence"], o["description"][:70]))
        for ev in r.get("disaster", {}).get("events", []):
            print("  disaster: %s %s water=%s approach=%s | %s" % (
                ev["event_name"], (ev["captured"][1] or "")[:10], ev.get("water_visible"),
                ev.get("approach_affected"), (ev.get("summary") or ev.get("error") or "")[:110]))


def fetch_imagery(building_id, refresh=False):
    """Every Vexcel image the site pass uses (overhead tile, angled views, post-disaster
    tiles), with no Claude calls, so imagery can be pulled for every building and
    analysed later. Writes imagery.json beside the files."""
    d = site_dir(building_id)
    loc = locate(building_id)
    prop = property_record(loc, d, refresh)
    footprint, _ = pick_footprint(prop, loc)
    rect = tile_rect(loc, footprint)
    out = {"building_id": str(building_id), "latlon": [loc["lat"], loc["lon"]] if "lat" in loc else None,
           "footprint": footprint is not None, "files": []}
    if fetch_tile(rect, os.path.join(d, "ortho.jpg"), refresh) is not None:
        out["files"].append("ortho.jpg")
    collections(loc, d, LAYERS, "collections.json", refresh)
    try:
        out["files"] += [os.path.basename(p) for p in (fetch_obliques(loc, footprint, rect, d, refresh) or {}).values()]
    except Exception as e:
        out["obliques_error"] = str(e)[:120]
    seen = set()
    for c in collections(loc, d, DISASTER_LAYER, "graysky_collections.json", refresh) or []:
        event = c.get("graysky-event") or "unidentified event (%s)" % c["collection"]
        if event in seen:
            continue
        seen.add(event)
        name = "disaster_%s.jpg" % c["collection"]
        if fetch_tile(rect, os.path.join(d, name), refresh, layer=DISASTER_LAYER,
                      collection=c["collection"]) is not None:
            out["files"].append(name)
            out.setdefault("disasters", []).append(EVENT_NAMES.get(event, event))
    json.dump(out, open(os.path.join(d, "imagery.json"), "w"), indent=1)
    return out
