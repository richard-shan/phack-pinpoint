"""The agent's tool layer on real data.

`agent/pinpoint/stubs.py` and `agent/pinpoint/routing.py` define the tools the agent
calls, backed by one made-up building. `install()` replaces them, keeping every name and
signature, with lane A's records and routing (`env.tools`) and lane B's photo matching
(`perception.tools`). The agent's own parts (the question loop, the belief update, the
verifier, the approval gates) are untouched.

What is still simulated, and says so in its output:
- the caller, when no console is attached: answers come from the true position if the
  run was given one (`truth_node`), otherwise the question goes unanswered;
- the photo match, only when a run asks for it with `simulate_match`.
"""
from __future__ import annotations

import math
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "agent") not in sys.path:
    sys.path.insert(0, str(ROOT / "agent"))

from env import tools as A                                  # noqa: E402
from env.graph import Graph                                 # noqa: E402
from pinpoint import orchestrator, routing, stubs, trace    # noqa: E402
from pinpoint.errors import EmptyCandidates, MatterportUnavailable, PropertyNotFound  # noqa: E402

# One incident at a time: what the tools need to know about the run in progress.
SESSION: dict = {}

# Where pictures for the console go. The console serves app/assets/, and assets/real/ is git-ignored.
ASSETS = ROOT / "app" / "assets" / "real" / "live"

NEAR_M = 12.0          # "can you see it from where you are": straight line, same floor

QUESTIONS = {
    "floor": "What floor are you on?",
    "room_type": "What kind of room are you in (hallway, office, lobby, restroom)?",
    "near_elevator": "Can you see elevator doors from where you are? (yes or no)",
    "near_outside_door": "Can you see a door that leads outside from where you are? (yes or no)",
    "near_stairs": "Can you see a staircase or a stairwell door from where you are? (yes or no)",
    "has_window": "Is there a window near you? (yes or no)",
    "floor_surface": "Is the floor carpet, tile, or bare concrete?",
    "furnished": "Is there furniture in the room? (yes or no)",
    "has_exit_sign": "Can you see a lit EXIT sign? (yes or no)",
    "has_fire_extinguisher": "Can you see a fire extinguisher? (yes or no)",
}


def reset(**values) -> dict:
    """Start a run. Keys: building_id, truth_node, simulate_match, console (a
    live.console_api.Console), console_incident, ask ("console" | "simulate" | "none"),
    ask_timeout_s."""
    SESSION.clear()
    SESSION.update({"ask": "simulate", "ask_timeout_s": 90.0, "asked": []}, **values)
    return SESSION


# ------------------------------------------------------------------ helpers
def _graph(building_id) -> Graph:
    return Graph.load(str(building_id))


def _near(g: Graph, node: str, others: list[str]) -> bool:
    p = g.xyz(node)
    floor = g.nodes[node].get("floor")
    for o in others:
        q = g.xyz(o)
        if p and q and g.nodes[o].get("floor") == floor and math.dist(p, q) <= NEAR_M:
            return True
    return False


def _landmarks(g: Graph) -> dict[str, list[str]]:
    hit = getattr(g, "_live_landmarks", None)
    if hit is None:
        stairs = {n for (a, b), e in g.edges.items() if e["type"] == "stairs" for n in (a, b)}
        hit = {
            "elevator": [n["id"] for n in g.of_kind("elevator")],
            "outside_door": [n["id"] for n in g.nodes.values()
                             if n.get("is_entrance") and not (n.get("approach") or {}).get("via")
                             or g.kind(n["id"]) == "exterior_door"],
            "stairs": sorted(stairs),
        }
        g._live_landmarks = hit
    return hit


def question_attributes(building_id, node_id) -> dict:
    """What a caller at this position could say about it, from the graph alone: floor,
    room type where the scan names rooms, and whether an elevator, an outside door or
    stairs is within sight. Lane B's per-node attributes are merged in when they exist."""
    g = _graph(building_id)
    n = g.nodes[node_id]
    lm = _landmarks(g)
    out = {"floor": n.get("floor_name") or f"Floor {(n.get('floor') or 0) + 1}"}
    if g.room(node_id):
        out["room_type"] = g.room(node_id)
    if lm["elevator"]:
        out["near_elevator"] = _near(g, node_id, lm["elevator"])
    if lm["outside_door"]:
        out["near_outside_door"] = _near(g, node_id, lm["outside_door"])
    if lm["stairs"]:
        out["near_stairs"] = _near(g, node_id, lm["stairs"])
    extra = _perception_attributes(building_id).get(node_id)
    if extra:
        out.update({k: v for k, v in extra.items() if v is not None and k != "room_type"})
    return out


def _perception_attributes(building_id) -> dict:
    cache = SESSION.setdefault("_b_attributes", {})
    if building_id not in cache:
        cache[building_id] = {}
        try:
            from perception import tools as B
            g = _graph(building_id)
            for ref, row in B.question_attributes(str(building_id)).items():
                tail = ref.rsplit("/", 1)[-1]
                if tail in g.nodes:
                    cache[building_id][tail] = row
        except Exception:                      # no attribute cache, or perception not installed
            pass
    return cache[building_id]


def _floor_number(g: Graph, node_id) -> int | None:
    """Building floor number for joining to CoStar tenants (1 = ground)."""
    name = g.nodes[node_id].get("floor_name") or ""
    m = re.search(r"(\d+)", name)
    if "basement" in name.lower():
        return None
    return int(m.group(1)) if m else (g.nodes[node_id].get("floor") or 0) + 1 + int(g.data.get("floor_offset") or 0)


# ------------------------------------------------------------------ records (lane A)
@trace.traced("records")
def get_property(address):
    rec = A.get_property(address) if address else {}
    if (not rec.get("building_id")) and SESSION.get("building_id"):
        rec = A.get_property(str(SESSION["building_id"]))
    if not rec.get("building_id"):
        raise PropertyNotFound(f"address not in CoStar: {address!r}")
    return rec


@trace.traced("records")
def get_tenants(building_id):
    return A.get_tenants(str(building_id))


@trace.traced("locator")
def look(node_id):
    return A.look(node_id, SESSION.get("building_id"))


@trace.traced("site")
def entrances(building_id):
    return A.entrances(str(building_id))


# ------------------------------------------------------------------ perception (lane B)
def _simulated_candidates(building_id, truth, k):
    g = _graph(building_id)
    if truth not in g.nodes:
        raise EmptyCandidates(f"{truth} is not a position in building {building_id}")
    p, floor = g.xyz(truth), g.nodes[truth].get("floor")
    others = sorted((n for n in g.nodes if g.kind(n) == "pano" and n != truth and g.xyz(n)),
                    key=lambda n: (g.nodes[n].get("floor") != floor, math.dist(p, g.xyz(n))))
    out = [{"node_id": truth, "score": 0.55}]
    out += [{"node_id": n, "score": round(0.45 * 0.6 ** (i + 1), 4)} for i, n in enumerate(others[:k - 1])]
    for c in out:
        c.update(simulated=True, evidence_path=g.nodes[c["node_id"]].get("pano_path"))
    return out


def publish(src: Path, name: str) -> str:
    """Copy a picture to where the console serves it. -> the address the console uses"""
    dest = ASSETS / SESSION["console_incident"] / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(Path(src).read_bytes())
    return f"/assets/real/live/{SESSION['console_incident']}/{name}"


def _publish(path, rank):
    """A match-evidence image as a path relative to the repo. With a console attached, the
    best match is also copied to where the console can serve it (app/assets/real/ is
    git-ignored), and that address is returned instead."""
    if not path:
        return path
    src = Path(path)
    cid = SESSION.get("console_incident")
    if rank == 0 and SESSION.get("console") and cid and src.is_file():
        try:
            return publish(src, "match.jpg")
        except OSError:
            pass
    try:
        return str(src.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


@trace.traced("locator")
def match_photo(building_id, image_path, k=10):
    """photo -> ranked positions [{node_id, score, evidence_path, attributes}] with node ids
    as the graph spells them. Positions in a scan the graph does not cover are dropped."""
    building_id = str(building_id)
    if not image_path:
        raise EmptyCandidates("caller photo path missing")
    if SESSION.get("simulate_match"):
        cands = _simulated_candidates(building_id, SESSION["simulate_match"], k)
    else:
        if not Path(image_path).exists() or Path(image_path).stat().st_size == 0:
            raise EmptyCandidates(f"caller photo unreadable: {image_path}")
        try:
            from . import keys
            keys.export_env()
            from perception import tools as B
        except ImportError as exc:
            raise MatterportUnavailable(
                "photo matching needs lane B's packages (torch, kornia). Run this with "
                f".venv/bin/python after `python -m live.setup`. ({exc})")
        try:
            cands = B.match_photo(building_id, str(image_path), k)
        except FileNotFoundError as exc:
            raise MatterportUnavailable(f"no photo index for building {building_id}: "
                                        f"run .venv/bin/python -m live.index {building_id} ({exc})")
        except ValueError as exc:
            raise EmptyCandidates(str(exc))
    g = _graph(building_id)
    out = []
    for c in cands:
        node = c["node_id"] if c["node_id"] in g.nodes else str(c["node_id"]).rsplit("/", 1)[-1]
        if node not in g.nodes:
            continue                                   # another scan of the same property
        out.append(dict(c, node_id=node, attributes=question_attributes(building_id, node),
                        evidence_path=_publish(c.get("evidence_path"), len(out))))
    if not out:
        raise EmptyCandidates("no matched position is inside the scan this building's graph covers")
    SESSION["candidates"] = out
    return out


@trace.traced("site")
def detect_site_features(building_id, address=None, lat=None, lon=None):
    """Entrances and staging spots as the graph has them (marked by hand from the scan
    and the aerial tile). Lane B's aerial reading is attached when it has been run."""
    building_id = str(building_id)
    g = _graph(building_id)
    staging = [{"staging_id": n["id"], "node_id": n["id"], "label": n.get("label") or n["id"],
                "latlon": n.get("latlon"), "confirmed": bool(n.get("confirmed"))}
               for n in g.of_kind("staging")]
    aerial = ROOT / "buildings" / building_id / "aerial.jpg"
    out = {"entrances": A.entrances(building_id), "staging": staging, "obstructions": [],
           "tile_path": str(aerial.relative_to(ROOT)) if aerial.exists() else None,
           "evidence_path": f"buildings/{building_id}/overrides.json",
           "scope": g.data.get("scope"), "source": "lane A graph: doors from the scan, staging from the aerial"}
    try:                                   # lane B's reading of the aerial, only if already cached
        import os
        if os.environ.get("ANTHROPIC_API_KEY"):
            from perception import tools as B
            feats = B.detect_site_features(building_id)
            if not feats.get("stub"):
                out["aerial_features"] = feats
                out["source"] += "; lane B aerial reading attached"
    except Exception:
        pass
    return out


@trace.traced("router")
def find_equipment(building_id, type, near_node):
    hits = A.find_equipment(str(building_id), type, near_node)
    if isinstance(hits, dict):
        return []
    g = _graph(building_id)
    return [dict(h, room_label=h.get("label") or g.room(h["node_id"]) or "scan position " + h["node_id"][:6])
            for h in hits]


@trace.traced("router")
def hazards_along(building_id, path):
    hz = A.hazards_along(str(building_id), path or [])
    return hz if isinstance(hz, list) else []


# ------------------------------------------------------------------ the caller (lane D)
def _parse_reply(attr, text, values):
    """A typed reply -> one of the values the candidates hold for this attribute."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if all(isinstance(v, bool) for v in values):
        if re.search(r"\b(no|nope|not|don't|dont|can't|cant|none|neither)\b", t):
            return False
        if re.search(r"\b(yes|yeah|yep|yup|i can|i do|there is|i see)\b", t):
            return True
        return None
    words = {"ground": 1, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    num = re.search(r"\d+", t)
    n = int(num.group()) if num else next((v for w, v in words.items() if re.search(rf"\b{w}\b", t)), None)
    for v in values:
        s = str(v).lower()
        if s in t or t in s:
            return v
        if attr == "floor" and n is not None and re.search(rf"\b{n}\b", s):
            return v
    return None


def ask_caller(incident_id, text, images=None, _mock_truth_attrs=None):
    """Ask the caller about one attribute and return {"reply", "raw"}. `text` is the
    attribute name the agent's question picker chose; the wording comes from QUESTIONS."""
    attr = (text or "").strip()
    question = QUESTIONS.get(attr, f"Can you tell me about: {attr}?")
    values = sorted({str(c["attributes"].get(attr)) for c in SESSION.get("candidates") or []})
    typed = [c["attributes"].get(attr) for c in SESSION.get("candidates") or [] if attr in c["attributes"]]
    console, cid = SESSION.get("console"), SESSION.get("console_incident")
    mode = SESSION.get("ask", "simulate")
    t0 = time.time()
    raw, reply, source = None, "(no answer)", "none"
    silent = SESSION.get("caller_silent") or mode == "none"
    if console and cid and not silent:
        console.message(cid, "dispatcher", f"Pinpoint asks: {question}")
    if silent:
        source = "not asked: the caller did not answer the previous question" if SESSION.get("caller_silent") else "none"
    elif mode == "console" and console and cid:
        got = console.wait_for_caller(cid, timeout_s=SESSION.get("ask_timeout_s", 90.0))
        if got:
            reply, raw, source = got, _parse_reply(attr, got, typed), "caller, typed in the console"
        else:
            SESSION["caller_silent"] = True          # do not keep the crew waiting on more questions
            source = f"no reply within {round(SESSION.get('ask_timeout_s', 90.0))} s"
    elif mode == "simulate" and SESSION.get("truth_node"):
        raw = question_attributes(SESSION["building_id"], SESSION["truth_node"]).get(attr)
        if raw is not None:
            reply = ("yes" if raw else "no") if isinstance(raw, bool) else str(raw)
            source = "simulated caller, answering from the true position"
            if console and cid:
                console.message(cid, "caller", f"{reply}  [simulated caller]")
    SESSION["asked"].append({"attribute": attr, "question": question, "reply": reply, "raw": raw,
                             "source": source, "options": values})
    trace.log("commander", "ask_caller", args={"attribute": attr, "question": question},
              result_summary=f"{reply!r} ({source})", dt_ms=round((time.time() - t0) * 1000, 1))
    return {"reply": reply, "raw": raw, "images": images or [], "question": question, "source": source}


def send_packet(incident_id):
    """Runs only after a dispatcher approves. Nothing is delivered outside this machine."""
    where = "console (approved locally)" if SESSION.get("console") else "local file"
    return {"status": "sent", "incident_id": incident_id, "channel": where}


# ------------------------------------------------------------------ routing (lane A)
def load_graph(building_id):
    return A.load_graph(str(building_id))


def route(building_id, from_node, to_node, profile, blocked_edges=None):
    """`ext_main` is the agent's name for the front door: it becomes the entrance marked
    as the street-address door, and the time includes getting to that door from outside."""
    building_id = str(building_id)
    g = _graph(building_id)
    if from_node == "ext_main" and "ext_main" not in g.nodes:
        front = (A.best_entry(building_id, to_node, profile) or {}).get("front_door")
        if not front:
            return {"path": [], "seconds": None, "meters": None, "reachable": False,
                    "reason": "no entrance is marked as the front door"}
        r = A.route(building_id, front["entrance_id"], to_node, profile)
        return dict(r, seconds=front["seconds"], entrance_id=front["entrance_id"],
                    label=front.get("label"), outside_seconds=front.get("outside_seconds"))
    return A.route(building_id, from_node, to_node, profile, blocked_edges)


def best_entry(building_id, target, profile):
    return A.best_entry(str(building_id), target, profile)


def best_entry_under_belief(building_id, candidates, profile):
    return A.best_entry_under_belief(str(building_id), candidates, profile)


def backup_route(building_id, from_node, to_node, profile, avoid_path=None):
    return A.backup_route(str(building_id), from_node, to_node, profile, avoid_path)


def replan(building_id, current_node, to_node, profile, blocked_edges):
    return A.replan(str(building_id), current_node, to_node, profile, blocked_edges)


def exit_route(building_id, from_node, profile):
    return A.exit_route(str(building_id), from_node, profile)


def directions(building_id, path):
    d = A.directions(str(building_id), path or [])
    return d if isinstance(d, list) else []


# ------------------------------------------------------------------ the agent's Records step
def records(idir, intake):
    """The agent's Records step, keeping what CoStar says about tenants and risks. (The
    stock step keeps only a made-up building's clinic suite.)"""
    rec = stubs.get_property(intake["address"])
    keep = ("building_id", "name", "address", "city", "state", "use_type", "use_subtype", "stories",
            "year_built", "sqft", "construction", "sprinklers", "elevators", "tenancy", "scan_date",
            "contact", "occupancy_risks", "not_known", "note", "evidence_path")
    brief = {k: rec.get(k) for k in keep}
    brief["tenants"] = [{k: t.get(k) for k in ("name", "suite", "floor", "floor_name", "industry", "risk_flags")}
                        for t in rec.get("tenants") or [] if t.get("name")]
    brief["located_tenant"] = None
    orchestrator._write(Path(idir) / "brief.json", brief)
    return brief


# ------------------------------------------------------------------ install
_STUBS = ("get_property", "get_tenants", "look", "entrances", "match_photo", "detect_site_features",
          "find_equipment", "hazards_along", "ask_caller", "send_packet")
_ROUTING = ("load_graph", "route", "best_entry", "best_entry_under_belief", "backup_route", "replan",
            "exit_route", "directions")
_original: dict = {}


def install() -> None:
    """Point the agent at the real tools. Safe to call twice."""
    from .packet import LivePacketWriter
    me = sys.modules[__name__]
    if not _original:
        _original.update({("stubs", n): getattr(stubs, n) for n in _STUBS})
        _original.update({("routing", n): getattr(routing, n) for n in _ROUTING})
        _original[("orchestrator", "_records")] = orchestrator._records
        _original[("orchestrator", "PacketWriter")] = orchestrator.PacketWriter
    for n in _STUBS:
        setattr(stubs, n, getattr(me, n))
    for n in _ROUTING:
        setattr(routing, n, getattr(me, n))
    orchestrator._records = records
    orchestrator.PacketWriter = LivePacketWriter


def uninstall() -> None:
    """Put the agent's stub tools back (the agent's own tests expect them)."""
    mods = {"stubs": stubs, "routing": routing, "orchestrator": orchestrator}
    for (mod, name), fn in _original.items():
        setattr(mods[mod], name, fn)
    _original.clear()
