"""Aerial tile of a building with its scan and entrances drawn on it.

    python -m env.aerial <building_id> [--half 60]

Lane B owns reading the aerial imagery. This module only fetches one top-down Vexcel
tile around a building and draws what lane A knows on it (scanned positions, entrances,
staging spots), to check where the scan's georeference puts the building and to give the
console a picture with the chosen door marked. Tiles are cached in env/cache/vexcel/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from . import config

LAYERS = "urban,urban-r,wide-area,urban-g"
M_PER_DEG = 111_320.0


def _bbox(lat: float, lon: float, half_m: float) -> tuple[float, float, float, float]:
    dlat = half_m / M_PER_DEG
    dlon = half_m / (M_PER_DEG * math.cos(math.radians(lat)))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def ortho_tile(lat: float, lon: float, half_m: float = 60, px: int = 1280) -> dict:
    """Top-down tile centered on lat/lon, `half_m` meters to each edge.
    Returns {"path", "bbox": (west, south, east, north), "size": (w, h)}."""
    w, s, e, n = _bbox(lat, lon, half_m)
    wkt = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"          # WKT is longitude first
    body = {"wkt": wkt, "srid": 4326, "layer": LAYERS, "crop": "bounding_box",
            "image-format": "jpeg", "destination-dimensions": f"{px},{px}"}
    key = hashlib.md5(json.dumps(body, sort_keys=True).encode()).hexdigest()
    path = config.CACHE / "vexcel" / f"ortho_{key}.jpg"
    if not path.exists():
        for attempt in range(5):
            r = requests.post(f"{config.VEXCEL_URL}/ortho/extract", json=body, timeout=300,
                              headers={"X-API-KEY": config.key("VEXCEL_API_KEY")})
            if r.status_code != 429:
                break
            time.sleep(min(2 ** attempt, 30) + random.random())
        if not r.ok:
            raise RuntimeError(f"ortho/extract failed ({r.status_code}): {r.text[:200]}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    with Image.open(path) as im:
        size = im.size
    return {"path": str(path), "bbox": (w, s, e, n), "size": size}


def draw_building(building_id: str, half_m: float = 60, path: list[str] | None = None) -> Path:
    """Aerial tile with scanned positions (small dots), entrances (squares, labeled),
    staging spots (triangles) and optionally a route drawn on it."""
    g = json.loads((config.BUILDINGS / str(building_id) / "graph.json").read_text())
    pts = [n for n in g["nodes"] if n.get("latlon")]
    if not pts:
        raise RuntimeError("this building has no lat/lon (scan without a georeference)")
    panos = [n for n in pts if n.get("kind", "pano") == "pano"]
    lat = sum(n["latlon"][0] for n in panos) / len(panos)
    lon = sum(n["latlon"][1] for n in panos) / len(panos)
    tile = ortho_tile(lat, lon, half_m)
    w, s, e, n_ = tile["bbox"]
    img = Image.open(tile["path"]).convert("RGB")
    W, H = img.size

    def px(latlon):
        return ((latlon[1] - w) / (e - w) * W, (n_ - latlon[0]) / (n_ - s) * H)

    d = ImageDraw.Draw(img)
    for node in panos:
        x, y = px(node["latlon"])
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(0, 220, 255))
    by_id = {node["id"]: node for node in g["nodes"]}
    if path:
        line = [px(by_id[p]["latlon"]) for p in path if by_id.get(p, {}).get("latlon")]
        if len(line) > 1:
            d.line(line, fill=(0, 255, 0), width=5)
    for node in pts:
        x, y = px(node["latlon"])
        if node.get("is_entrance"):
            col = (255, 40, 40) if node.get("primary") else (255, 200, 0)
            d.rectangle([x - 9, y - 9, x + 9, y + 9], fill=col, outline=(0, 0, 0), width=2)
            d.text((x + 12, y - 8), node.get("label") or node["id"], fill=(255, 255, 255),
                   stroke_width=2, stroke_fill=(0, 0, 0))
        elif node.get("kind") in ("staging", "arrival"):
            d.polygon([(x, y - 11), (x - 10, y + 8), (x + 10, y + 8)], fill=(255, 0, 255), outline=(0, 0, 0))
            d.text((x + 12, y - 8), node.get("label") or node["id"], fill=(255, 255, 255),
                   stroke_width=2, stroke_fill=(0, 0, 0))
    out = config.BUILDINGS / str(building_id) / "aerial.jpg"
    img.save(out, quality=88)
    meta = {"path": str(out.relative_to(config.ROOT)), "bbox_wsen": tile["bbox"], "size": [W, H],
            "meters_per_pixel": round(2 * half_m / W, 4), "north_up": True}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return out


def pixel_to_latlon(building_id: str, x: float, y: float) -> list[float]:
    """Lat/lon of a pixel in buildings/<id>/aerial.jpg (for placing staging spots by eye)."""
    meta = json.loads((config.BUILDINGS / str(building_id) / "aerial.json").read_text())
    w, s, e, n = meta["bbox_wsen"]
    W, H = meta["size"]
    return [round(n - y / H * (n - s), 7), round(w + x / W * (e - w), 7)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("building_id")
    ap.add_argument("--half", type=float, default=60)
    a = ap.parse_args()
    print(draw_building(a.building_id, a.half).relative_to(config.ROOT))
