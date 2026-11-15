"""Convene the evidence court (live/court.py) for an incident the photo match could not settle.

Called by the watcher after the agent has drafted the packet. The court runs in the
deepagents environment (.venv-agents, or PINPOINT_AGENTS_PYTHON) as a separate process; if it
is missing, slow or abstains, nothing changes and the photo match stands.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIDENT = float(os.environ.get("PINPOINT_COURT_BELOW", "0.85"))   # convene only below this
MAX_CANDIDATES = 3
TIMEOUT_S = float(os.environ.get("PINPOINT_COURT_TIMEOUT_S", "240"))


def _python():
    for p in (os.environ.get("PINPOINT_AGENTS_PYTHON"), ROOT / ".venv-agents" / "bin" / "python"):
        if p and Path(p).exists():
            return str(p)
    return None


def _confidence(location: str):
    m = re.search(r"Confidence (\d+)%", location or "")
    return int(m.group(1)) / 100 if m else None


def _views(building_id, cand):
    """Scanned views of a candidate spot: its best-matching crop, then the four level views."""
    out = []
    ev = cand.get("evidence_path") or ""
    for p in ((ROOT / "app" / ev.lstrip("/")) if ev.startswith("/assets/") else ROOT / ev,):
        if ev and p.is_file():
            out.append(str(p))
    attrs = ROOT / "cache" / "attributes" / str(building_id) / (str(cand["node_id"]).replace("/", "_") + ".json")
    facts = {}
    if attrs.is_file():
        facts = json.loads(attrs.read_text())
        crops = ROOT / "cache" / "index" / str(building_id) / "crops"
        for p in facts.get("evidence_paths") or []:
            local = Path(p) if Path(p).is_file() else crops / Path(p).name     # paths saved on another machine
            if local.is_file():
                out.append(str(local))
        facts = {k: v for k, v in facts.items() if k not in ("evidence_paths", "usage", "model", "node_id")}
        for item in facts.get("equipment", []) + facts.get("hazards", []):
            item.pop("evidence_path", None)
    return out, facts


def build_case(console, cid, building_id):
    from env.graph import Graph
    from live import tools
    from live.console_api import save_photo
    inc = console.get(cid)
    conf = _confidence(inc.get("location", ""))
    if conf is not None and conf >= CONFIDENT:
        return None, f"photo match is {round(conf * 100)}% sure"
    g = Graph.load(str(building_id))
    picked, seen = [], set()
    for c in tools.SESSION.get("candidates") or []:
        n = g.nodes.get(c["node_id"])
        if not n:
            continue
        where = g.label(c["node_id"]) or g.room(c["node_id"]) or ""
        floor = n.get("floor_name") or f"Floor {(n.get('floor') or 0) + 1}"
        key = (floor, where or c["node_id"])
        if key in seen:
            continue                                   # same room on the same floor: not a real dispute
        views, facts = _views(building_id, c)
        if not views:
            continue
        seen.add(key)
        label = ", ".join(x for x in ((where[:1].upper() + where[1:]) if where else "", floor) if x)
        picked.append({"id": "ABC"[len(picked)], "node_id": c["node_id"], "label": label, "floor": floor,
                       "similarity": round(float(c.get("score") or 0), 3), "inliers": c.get("inliers"),
                       "facts": facts, "views": views})
        if len(picked) == MAX_CANDIDATES:
            break
    if len(picked) < 2:
        return None, "only one distinct candidate spot"
    work = Path(tempfile.mkdtemp(prefix="pinpoint-court-"))
    photo = save_photo(inc, work)
    if not photo:
        return None, "no caller photo"
    loc = (inc.get("channel") or {}).get("location") or {}
    texts = [m["text"] for m in inc.get("messages", []) if m["role"] == "caller"]
    return {"building": g.data.get("name") or str(building_id), "address": loc.get("address") or "",
            "caller_text": " / ".join(texts)[:600], "caller_photo": photo, "candidates": picked}, work


def hold_court(console, cid, building_id, log=print):
    """-> the verdict dict when the court ruled, else None. Applies the ruling to the incident."""
    if os.environ.get("PINPOINT_COURT", "1") == "0":
        return None
    python = _python()
    if not python:
        log("  evidence court: no deepagents environment (.venv-agents); skipped")
        return None
    case, work = build_case(console, cid, building_id)
    if case is None:
        log(f"  evidence court not needed: {work}")
        return None
    path = Path(work) / "case.json"
    path.write_text(json.dumps(case))
    log(f"  evidence court convened: {len(case['candidates'])} candidate spots")
    try:
        done = subprocess.run([python, "-m", "live.court", str(path)], cwd=ROOT, capture_output=True,
                              text=True, timeout=TIMEOUT_S, env=dict(os.environ))
        verdict = json.loads((done.stdout.strip().splitlines() or ["{}"])[-1])
    except (subprocess.TimeoutExpired, ValueError) as exc:
        log(f"  evidence court abstained: {type(exc).__name__}")
        return None
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    if not verdict or verdict.get("abstained"):
        log(f"  evidence court abstained: {verdict.get('abstained') if verdict else done.stderr[-300:]}")
        return None
    from env import console as console_export
    inc = console.get(cid)
    current = (inc.get("spatialRoute") or {}).get("destination") or case["candidates"][0]["node_id"]
    overruled = verdict["node_id"] != current
    record = {k: verdict[k] for k in ("winner", "label", "confidence", "decisive_detail", "ruling", "cases",
                                      "models", "seconds", "scouts")}
    record["overruled"] = overruled
    if not overruled:
        # The court agrees with the photo match: nothing changes for the dispatcher. The
        # ruling is kept on the record (and in the activity log) only.
        console.update(cid, updatedBy="agent", court=record)
        log(f"  evidence court agreed: {verdict['label']} ({round(verdict['confidence'] * 100)}%)")
        return verdict
    if console.has_scan(str(building_id)):
        body = console_export.route_update(str(building_id), verdict["node_id"], "foot")
        if body.get("reachable") is not False:
            body["reason"] = f"Rerouted by the evidence court: {verdict['decisive_detail']}"[:500]
            console.route(cid, body)
    console.update(cid, updatedBy="agent", court=record,
                   location=f"{verdict['label']} · Evidence court {round(verdict['confidence'] * 100)}%")
    log(f"  evidence court overruled the photo match: {verdict['label']} "
        f"({round(verdict['confidence'] * 100)}%), {verdict['seconds']}")
    return verdict
