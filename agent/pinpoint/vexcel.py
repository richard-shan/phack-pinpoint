# Vexcel aerial tile + property attrs. Raises VexcelUnavailable → fixture fallback.
import json
import math
import os
from pathlib import Path

import requests

from .config import FIXTURES
from .trace import log
from .errors import VexcelUnavailable  # noqa: F401

API = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"
TIMEOUT = 5.0  # site is on the critical path
_API_TXT = FIXTURES.parent.parent.parent / "api.txt"  # repo-root api.txt


def _load_key():
    # api_key line in api.txt, then the next non-empty value.
    try:
        lines = _API_TXT.read_text().splitlines()
    except OSError as e:
        raise VexcelUnavailable(f"api.txt unreadable: {e.__class__.__name__}") from e
    for i, ln in enumerate(lines):
        if ln.strip() == "api_key":
            for ln2 in lines[i + 1:]:
                if ln2.strip():
                    return ln2.strip()
    raise VexcelUnavailable("api.txt missing Vexcel api_key line")


def _square_wkt(lat, lon, half_m):
    # Rough meter box around lat/lon as WKT.
    dlat = half_m / 111_320.0
    dlon = half_m / (111_320.0 * math.cos(math.radians(lat)))
    w, e = lon - dlon, lon + dlon
    s, n = lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def _extract_features(property_response):
    # Pull the few fields we care about from property/extract.
    out = {
        "source": "vexcel.property/extract",
        "structure_count": None,
        "parcel_tree_pct": None,
        "pool_detected": None,
        "primary_roof_material": None,
        "primary_roof_shape": None,
        "primary_roof_condition": None,
        "primary_footprint_sqft": None,
    }
    try:
        features = property_response.get("features") or []
        if not features:
            return out
        attrs = features[0].get("properties", {}) or {}
        out["structure_count"] = attrs.get("structure.count")
        out["parcel_tree_pct"] = attrs.get("parcel-tree-cover.percent")
        out["pool_detected"] = attrs.get("pool.detected")
        for s in attrs.get("structures", []) or []:
            if s.get("primary-structure"):
                rc = s.get("roof-characteristics") or {}
                out["primary_roof_material"] = (rc.get("material") or {}).get("class")
                out["primary_roof_shape"] = (rc.get("shape") or {}).get("value")
                out["primary_roof_condition"] = rc.get("condition")
                fp = s.get("footprint") or {}
                if fp.get("area") is not None:
                    out["primary_footprint_sqft"] = round(fp["area"])
                break
    except (AttributeError, TypeError, IndexError) as e:
        log("site", "vexcel_features_parse_error",
            result_summary=f"{e.__class__.__name__}", phase="fallback")
    return out


def fetch_aerial(address, lat, lon, building_id="unknown"):
    # Ortho tile + features; caches jpeg under fixtures/<building_id>/.
    try:
        key = _load_key()
    except VexcelUnavailable:
        log("site", "vexcel_fallback", result_summary="no api key", phase="fallback")
        raise
    headers = {"X-API-KEY": key}

    cache_dir = FIXTURES / building_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    tile_path = cache_dir / "vexcel_tile.jpg"

    point_wkt = f"POINT({lon} {lat})"
    try:
        pr = requests.get(
            f"{API}/property/extract",
            headers=headers,
            params={
                "wkt": point_wkt,
                "srid": 4326,
                "layer": LAYERS,
                "latest": "true",
                "include": "precomputed",
                "unit-system": "imperial",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        log("site", "vexcel_fallback",
            result_summary=f"property/extract {e.__class__.__name__}", phase="fallback")
        raise VexcelUnavailable(f"property/extract network: {e.__class__.__name__}") from e

    if pr.status_code == 401:
        log("site", "vexcel_fallback", result_summary="401 unauthorized", phase="fallback")
        raise VexcelUnavailable("property/extract 401 unauthorized")

    if pr.status_code == 404:
        area_wkt = _square_wkt(lat, lon, half_m=40)
        features = _extract_features({})
    elif pr.ok:
        try:
            pj = pr.json()
        except ValueError as e:
            log("site", "vexcel_fallback", result_summary="property json decode",
                phase="fallback")
            raise VexcelUnavailable("property/extract bad json") from e
        features = _extract_features(pj)
        try:
            geom = pj["features"][0]["geometry"]
            if isinstance(geom, str):
                area_wkt = geom
            else:
                coords = geom.get("coordinates") or []
                if geom.get("type") == "Polygon" and coords:
                    ring = coords[0]
                    pts = ",".join(f"{x} {y}" for x, y in ring)
                    area_wkt = f"POLYGON(({pts}))"
                else:
                    area_wkt = _square_wkt(lat, lon, half_m=40)
        except (KeyError, IndexError, TypeError):
            area_wkt = _square_wkt(lat, lon, half_m=40)
    else:
        log("site", "vexcel_fallback",
            result_summary=f"property/extract {pr.status_code}", phase="fallback")
        raise VexcelUnavailable(f"property/extract {pr.status_code}")

    try:
        tr = requests.post(
            f"{API}/ortho/extract",
            headers=headers,
            json={
                "wkt": area_wkt,
                "srid": 4326,
                "layer": LAYERS,
                "buffer-meter": 10,
                "crop": "bounding_box",
                "image-format": "jpeg",
                "destination-dimensions": "640,640",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        log("site", "vexcel_fallback",
            result_summary=f"ortho/extract {e.__class__.__name__}", phase="fallback")
        raise VexcelUnavailable(f"ortho/extract network: {e.__class__.__name__}") from e

    if not tr.ok:
        log("site", "vexcel_fallback",
            result_summary=f"ortho/extract {tr.status_code}", phase="fallback")
        raise VexcelUnavailable(f"ortho/extract {tr.status_code}")
    if not tr.content or len(tr.content) < 500:
        log("site", "vexcel_fallback",
            result_summary=f"ortho/extract empty ({len(tr.content)} B)", phase="fallback")
        raise VexcelUnavailable("ortho/extract empty response")

    tile_path.write_bytes(tr.content)

    log("site", "vexcel_ok",
        result_summary=f"tile {len(tr.content)//1024} KB, features {sum(1 for v in features.values() if v is not None)}",
        phase="fallback")

    return {
        "tile_path": str(tile_path.relative_to(FIXTURES.parent.parent)),
        "tile_url": f"{API}/ortho/extract",
        "features": features,
        "address": address,
    }
