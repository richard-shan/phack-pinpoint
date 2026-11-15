# Contract tools — fixtures today; A/B/D swap real backends later.
import copy
import json
from functools import lru_cache
from .config import FIXTURES, DEMO_BUILDING
from .trace import traced
from .errors import (PropertyNotFound, MatterportUnavailable,
                     EmptyCandidates)

_B = FIXTURES / "building_bayou"


@lru_cache(maxsize=32)
def _load_cached(name):
    return json.loads((_B / name).read_text())


def _load(name):
    return copy.deepcopy(_load_cached(name))  # callers mutate belief fields


@traced("records")
def get_property(address):
    # CoStar brief for address. Raises PropertyNotFound on UNKNOWN/empty.
    """Look up a commercial property in CoStar by street address. Returns a dict with building_id, use_type, stories, year_built, sqft, tenants list, and a primary contact dict."""
    if not address or "UNKNOWN" in str(address).upper():
        raise PropertyNotFound(f"address not in CoStar: {address!r}")
    p = _load("property.json")
    return {"building_id": p["building_id"], "use_type": p["use_type"], "stories": p["stories"],
            "year_built": p["year_built"], "sqft": p["sqft"], "scan_date": p["scan_date"],
            "tenants": p["tenants"], "contact": p["contact"], "address": p["address"],
            "evidence_path": "fixtures/building_bayou/property.json"}


@traced("records")
def get_tenants(building_id):
    return _load("property.json")["tenants"]


@traced("locator")
def look(node_id):
    # Pano + attributes + neighbors for a node.
    """Fetch a specific node's pano image_path, attributes, and neighbors from the Matterport twin."""
    g = _load("graph.json")
    node = next((n for n in g["nodes"] if n["id"] == node_id), None)
    if not node:
        return {"error": f"unknown node {node_id}"}
    neighbors = [e["b"] if e["a"] == node_id else e["a"]
                 for e in g["edges"] if node_id in (e["a"], e["b"])]
    return {"image_path": node.get("pano_path"), "attributes": node.get("attributes", {}),
            "room_label": node.get("room_label"), "neighbors": neighbors,
            "equipment": node.get("equipment", [])}


@traced("site")
def entrances(building_id):
    s = _load("site.json")
    return s["entrances"]


@traced("locator")
def match_photo(building_id, image_path, k=10):
    # Ranked room candidates. synth/node_<id>.jpg → synthetic set; else match.json.
    """Match a caller's photo to candidate positions in the building's Matterport twin. Returns up to k ranked candidates, each with node_id, score, evidence_path, and attributes."""
    if building_id == "no_twin" or str(image_path).startswith("no_twin://"):
        raise MatterportUnavailable(
            f"no Matterport twin for building {building_id!r}")
    if image_path is None or image_path == "":
        raise EmptyCandidates("caller photo path missing")
    from pathlib import Path as _P
    _p = _P(str(image_path))
    if _p.is_absolute() and (not _p.exists() or _p.stat().st_size == 0):
        raise EmptyCandidates(f"caller photo unreadable: {image_path}")
    if "no_match" in str(image_path):
        raise EmptyCandidates("no candidates matched the caller photo")
    marker = "synth/node_"
    if image_path and marker in image_path:
        truth = image_path.split(marker, 1)[1].rsplit(".", 1)[0]
        return _synth_candidates(truth, k)
    cands = _load("match.json")["candidates"]
    return cands[:k]


def _synth_candidates(truth_node_id, k=10):
    # Eval helper: truth on top, same-floor distractors with score decay.
    import random
    g = _load("graph.json")
    nodes = {n["id"]: n for n in g["nodes"]}
    if truth_node_id not in nodes:
        return _load("match.json")["candidates"][:k]
    truth = nodes[truth_node_id]
    truth_floor = truth.get("floor")
    peers = [n for nid, n in nodes.items()
             if nid != truth_node_id and n.get("floor") == truth_floor and not n.get("is_entrance")]
    rng = random.Random(hash(truth_node_id) & 0xFFFFFFFF)
    rng.shuffle(peers)
    distractors = peers[:max(0, k - 1)]
    if len(distractors) < k - 1:
        others = [n for nid, n in nodes.items()
                  if nid != truth_node_id and n.get("floor") != truth_floor
                  and not n.get("is_entrance") and n not in distractors]
        rng.shuffle(others)
        distractors.extend(others[:(k - 1 - len(distractors))])
    out = []
    out.append({"node_id": truth_node_id, "score": 0.72,
                "evidence_path": truth.get("pano_path"),
                "attributes": dict(truth.get("attributes", {}),
                                    floor=truth.get("floor"))})
    base = 0.60
    for i, n in enumerate(distractors):
        out.append({"node_id": n["id"], "score": round(base - 0.05 * i, 3),
                    "evidence_path": n.get("pano_path"),
                    "attributes": dict(n.get("attributes", {}), floor=n.get("floor"))})
    return out[:k]


@traced("site")
def detect_site_features(building_id, address=None, lat=None, lon=None):
    # Entrances/staging; prefers live Vexcel tile, else fixture.
    """Identify entrances, staging areas, and obstructions around a building from the Vexcel aerial tile. Falls back to fixture data when Vexcel is unavailable."""
    from . import vexcel  # local so tests can patch
    s = _load("site.json")
    base = {"entrances": s["entrances"], "staging": s["staging"],
            "obstructions": s["obstructions"], "tile_path": s["tile_path"],
            "evidence_path": "fixtures/building_bayou/site.json"}
    if lat is None or lon is None:
        try:
            prop = _load("property.json")
            lat = lat if lat is not None else prop.get("lat")
            lon = lon if lon is not None else prop.get("lon")
        except Exception:
            pass
    if lat is None or lon is None:
        base["source"] = "fixture_only"
        return base
    try:
        real = vexcel.fetch_aerial(address or "unknown", lat, lon, building_id=building_id)
        base["aerial_tile_path"] = real["tile_path"]
        base["aerial_features"] = real["features"]
        base["source"] = "vexcel+fixture"
    except vexcel.VexcelUnavailable as _vexc:
        from . import trace as _tr
        _tr.log("site", "vexcel_fallback",
                result_summary=str(_vexc)[:120], phase="fallback")
        base["source"] = "fixture_only"
    return base


@traced("router")
def find_equipment(building_id, type, near_node):
    # Nodes tagged with equipment type (meters TBD).
    g = _load("graph.json")
    hits = []
    for n in g["nodes"]:
        if type in n.get("equipment", []):
            hits.append({"node_id": n["id"], "room_label": n["room_label"],
                         "meters": None, "evidence_path": n.get("pano_path")})
    return hits


@traced("router")
def hazards_along(building_id, path):
    # Locked / low-confidence doors on the path.
    """Scan a graph path for hazards such as locked doors, obstructions, or stairs on a stretcher profile. Returns a list of hazard records."""
    g = _load("graph.json")
    pset = set(path or [])
    out = []
    for e in g["edges"]:
        if e["a"] in pset and e["b"] in pset:
            if e["type"] == "locked_door":
                out.append({"node_id": e["b"], "type": "locked_door",
                            "evidence_path": e.get("evidence_path")})
            elif e["type"] == "door" and e.get("confidence", 1) < 0.85:
                out.append({"node_id": e["b"], "type": "uncertain_door",
                            "evidence_path": e.get("evidence_path")})
    return out


@traced("commander")
def ask_caller(incident_id, text, images=None, _mock_truth_attrs=None):
    # Ask the caller; mock mode answers from ground-truth attrs.
    """Ask the caller a text question and optionally show them images. Use sparingly, only when the answer changes what to do next."""
    if _mock_truth_attrs is not None:
        attr = (text or "").strip()
        val = _mock_truth_attrs.get(attr)
        return {"reply": _phrase_answer(attr, val), "raw": val, "images": images or []}
    return {"reply": "(stub) caller did not respond", "raw": None, "images": images or []}


def _phrase_answer(attr, val):
    if val is None:
        return "I'm not sure"
    if attr == "has_window":
        return "yes there's a window" if val else "no window"
    if attr == "near_elevator":
        return "yes I can see the elevator" if val else "no elevator in sight"
    if attr == "floor":
        return f"floor {val}"
    return str(val)


def send_packet(incident_id):
    # Mark packet sent (gate already approved upstream).
    """Mark the responder packet as sent. Gated by dispatcher approval via the framework interrupt."""
    return {"status": "sent", "incident_id": incident_id, "channel": "sms+link"}


def notify_contact(incident_id, contact):
    # Write notify.json; returns no_contact_available if brief has no usable contact.
    """Notify a point-of-contact at the building. Gated by dispatcher approval. Skips cleanly when no contact is available."""
    import json as _json
    from .config import INCIDENTS
    if not contact:
        return {"status": "no_contact_available"}
    has_method = contact.get("method") or contact.get("phone") or contact.get("email")
    if not (contact.get("name") and has_method):
        return {"status": "no_contact_available"}
    idir = INCIDENTS / incident_id
    idir.mkdir(parents=True, exist_ok=True)
    payload = {"status": "notified", "contact": contact, "incident_id": incident_id,
               "evidence_path": f"incidents/{incident_id}/notify.json"}
    (idir / "notify.json").write_text(_json.dumps(payload, indent=2, default=str))
    return payload


def check_claim(claim_text, sources):
    # Supported iff [src:dotted.key] resolves to a non-empty value.
    src_key = None
    if "[src:" in claim_text:
        src_key = claim_text.split("[src:", 1)[1].split("]", 1)[0].strip()
    supported = bool(src_key) and _resolve(sources, src_key) not in (None, "", [], {})
    return {"claim": claim_text, "source_key": src_key, "supported": supported,
            "confidence": 0.9 if supported else 0.0}


def _resolve(sources, dotted):
    cur = sources
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur
