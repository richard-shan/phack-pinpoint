"""STUB tool layer — every contract tool (plan 5 + 4A) as a function returning FIXED, valid,
realistic data INSTANTLY. Fixtures live in pinpoint/fixtures/building_bayou/*.json so they are
easy to eyeball and swap.

Ownership of the real versions (plan 6):
  Person A: get_property, look, entrances, ALL routing tools (see routing.py)
  Person B: match_photo, detect_site_features, node attributes, find_equipment, hazards_along
  Person C (REAL already): best_question, update_belief (localization.py); Verifier's check_claim
  Person D: ask_caller, send_packet, notify_contact (channel + approval UI)
"""
import copy
import json
from functools import lru_cache
from .config import FIXTURES, DEMO_BUILDING
from .trace import traced

_B = FIXTURES / "building_bayou"


@lru_cache(maxsize=32)
def _load_cached(name):
    return json.loads((_B / name).read_text())


def _load(name):
    # deepcopy so callers can mutate (e.g. belief fields) without poisoning the cache
    return copy.deepcopy(_load_cached(name))


# ---------- Person A (records + env) ----------
@traced("records")
def get_property(address):
    """address -> normalized CoStar record. STUB: single demo building."""
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
    """node_id -> image + attributes + neighbors. STUB from graph fixture."""
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


# ---------- Person B (perception) ----------
@traced("locator")
def match_photo(building_id, image_path, k=10):
    """photo -> ranked candidate positions with evidence overlay. STUB behavior:
      * if image_path contains 'synth/node_<id>.jpg', build a synthetic candidate set centered on
        that node (top candidate = truth with score 0.72, plus 3-4 distractors sampled from
        nearby nodes on the same floor with mild score decay). Lets the eval harness drive the
        pipeline across many ground-truth nodes without a real matcher.
      * otherwise return the fixed ambiguous set from match.json (demo behavior)."""
    marker = "synth/node_"
    if image_path and marker in image_path:
        truth = image_path.split(marker, 1)[1].rsplit(".", 1)[0]
        return _synth_candidates(truth, k)
    cands = _load("match.json")["candidates"]
    return cands[:k]


def _synth_candidates(truth_node_id, k=10):
    """Build an ambiguous-but-correct candidate set around truth_node_id. Top-1 is the truth
    with score 0.72; distractors are same-floor neighbors with score decay. If the truth node
    has fewer than k same-floor peers, pad with other-floor nodes to keep the set interesting."""
    import random
    g = _load("graph.json")
    nodes = {n["id"]: n for n in g["nodes"]}
    if truth_node_id not in nodes:
        return _load("match.json")["candidates"][:k]
    truth = nodes[truth_node_id]
    truth_floor = truth.get("floor")
    # Peers on the same floor, excluding entrances and the truth itself
    peers = [n for nid, n in nodes.items()
             if nid != truth_node_id and n.get("floor") == truth_floor and not n.get("is_entrance")]
    # Deterministic shuffle keyed by the truth node id so synth incidents are reproducible
    rng = random.Random(hash(truth_node_id) & 0xFFFFFFFF)
    rng.shuffle(peers)
    distractors = peers[:max(0, k - 1)]
    # Pad with other floors if still short
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
    """Site features. Tries Vexcel aerial first (real API) then falls back to fixture.
    When Vexcel returns, merges its property attributes under 'aerial_features' and sets
    'aerial_tile_path' so the packet can carry a citation. On any Vexcel failure the
    fallback trace event is already logged inside vexcel.fetch_aerial."""
    from . import vexcel  # local import so tests can monkeypatch
    s = _load("site.json")
    base = {"entrances": s["entrances"], "staging": s["staging"],
            "obstructions": s["obstructions"], "tile_path": s["tile_path"],
            "evidence_path": "fixtures/building_bayou/site.json"}
    # Use building fixture lat/lon if caller didn't supply.
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
    except vexcel.VexcelUnavailable:
        base["source"] = "fixture_only"
    return base


@traced("router")
def find_equipment(building_id, type, near_node):
    """Nearest equipment of a type. STUB: scans graph node equipment + approx hops."""
    g = _load("graph.json")
    hits = []
    for n in g["nodes"]:
        if type in n.get("equipment", []):
            hits.append({"node_id": n["id"], "room_label": n["room_label"],
                         "meters": None, "evidence_path": n.get("pano_path")})
    return hits


@traced("router")
def hazards_along(building_id, path):
    """Hazards on a route. STUB: flag locked doors + narrow/low-confidence doors on the path."""
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


# ---------- Person D (channel + gates) ----------
@traced("commander")
def ask_caller(incident_id, text, images=None, _mock_truth_attrs=None):
    """Send the caller a question, return their reply. STUB: in mock mode the 'caller' answers
    truthfully from the ground-truth node's attributes so the belief loop converges in the demo."""
    if _mock_truth_attrs is not None:
        # The orchestrator passes the attribute being asked; answer from ground truth.
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
    """APPROVAL GATE (plan 5). The orchestrator / deepagents interrupt_on handles the actual pause;
    this only runs AFTER a human approves. STUB: marks the packet sent."""
    return {"status": "sent", "incident_id": incident_id, "channel": "sms+link"}


def notify_contact(incident_id, contact):
    """APPROVAL GATE. STUB."""
    return {"status": "notified", "incident_id": incident_id, "contact": contact}


# ---------- Person C (verifier) ----------
def check_claim(claim_text, sources):
    """Verifier primitive: does this packet sentence have a backing source among the incident files?
    STUB heuristic — real version asks the LLM to map sentence -> file+field. Here: a claim is
    'supported' if its tagged source key exists in the provided sources dict and is non-empty."""
    src_key = None
    # claims are tagged like '... [src:brief.use_type]'
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
