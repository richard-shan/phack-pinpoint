# Pipeline steps the orchestrator runs (same tools deepagents gets).
from __future__ import annotations

import json
from pathlib import Path

from . import stubs, routing, localization, trace
from .config import CONFIDENCE_THRESHOLD, MAX_QUESTIONS
from .errors import EmptyCandidates
from .util import write_json


def plan_md(incident_id: str, intake: dict) -> str:
    return f"""# Incident {incident_id} — Commander plan

Caller address: {intake['address']}
Caller text: {intake.get('caller_text', '')}

- [ ] 1. Records: CoStar brief
- [ ] 2. Locator: match photo → position (ask only if it changes the door)
- [ ] 3. Site: aerial entrances / staging / obstructions
- [ ] 4. Router: best door + foot/stretcher routes
- [ ] 5. Verifier: every claim cites a source
- [ ] 6. Dispatcher approval → send

Steps 1–3 run in parallel. Step 4 needs all three.
"""


def run_records(idir: Path, intake: dict) -> dict:
    rec = stubs.get_property(intake["address"])
    located_suite = next(
        (t for t in rec["tenants"] if t.get("floor") == 2 and t.get("use") == "clinic"),
        None,
    )
    brief = {
        "building_id": rec["building_id"],
        "address": rec["address"],
        "use_type": rec["use_type"],
        "stories": rec["stories"],
        "year_built": rec["year_built"],
        "sqft": rec["sqft"],
        "scan_date": rec["scan_date"],
        "contact": rec["contact"],
        "located_tenant": located_suite,
        "evidence_path": rec["evidence_path"],
    }
    write_json(idir / "brief.json", brief)
    return brief


def should_skip_questions(building_id: str, cands: list) -> tuple[bool, str | None]:
    # True if asking wouldn't change the best door.
    above = [c for c in cands if c.get("belief", 0.0) > 0.1]
    if len(above) < 2:
        return True, "only one candidate above belief 0.1"
    nodes = routing.load_graph(building_id)["nodes"]
    rooms = {
        (nodes.get(c["node_id"]) or {}).get("room_label") or c["node_id"]
        for c in above
    }
    if len(rooms) > 1:
        return False, None
    ub = routing.best_entry_under_belief(building_id, above, "foot")
    if ub.get("question_worth_asking"):
        return False, None
    return True, (
        f"all top candidates share room={next(iter(rooms))} "
        f"and best_entry={ub.get('entrance_id')}"
    )


def _ground_truth_attrs(building_id: str, intake: dict | None = None) -> dict:
    gt = (intake or {}).get("ground_truth_node_id") or stubs._load("match.json")["ground_truth_node"]
    node = routing.load_graph(building_id)["nodes"].get(gt)
    return node.get("attributes", {}) if node else {}


def run_locator(idir: Path, intake: dict, mock_llm: bool = True, blacklist=None) -> dict:
    building_id = intake["building_id"]
    cands = stubs.match_photo(building_id, intake["caller_photo_path"], k=10)
    if blacklist:
        cands = [c for c in cands if c.get("node_id") not in blacklist]
    if not cands:
        raise EmptyCandidates("no candidates remain after blacklist")

    localization.normalize_belief(cands)
    write_json(idir / "candidates.json", {"candidates": cands})

    truth = _ground_truth_attrs(building_id, intake=intake)
    asked, asked_attrs, log = 0, [], []
    skip, skip_reason = should_skip_questions(building_id, cands)
    if skip:
        trace.log("commander", "question_skipped",
                  result_summary=skip_reason, phase="optimization")

    while not skip and asked < MAX_QUESTIONS:
        if localization.top_confidence(cands) >= CONFIDENCE_THRESHOLD:
            break
        q = localization.best_question(cands, exclude=asked_attrs)
        if not q.get("eligible"):
            break
        reply = stubs.ask_caller(
            intake["incident_id"], text=q["attribute"], _mock_truth_attrs=truth,
        )
        localization.update_belief(cands, q["attribute"], reply["raw"])
        asked_attrs.append(q["attribute"])
        asked += 1
        log.append({
            "q": q["attribute"],
            "hint": q["question_hint"],
            "info_gain": q["expected_info_gain"],
            "answer": reply["raw"],
            "top_conf_after": round(localization.top_confidence(cands), 3),
        })
        trace.log(
            "locator", "ask+update_belief",
            args={"attr": q["attribute"], "gain": q["expected_info_gain"]},
            result_summary=f"conf->{localization.top_confidence(cands):.2f}",
            phase="subagent",
        )

    cands_sorted = sorted(cands, key=lambda c: c["belief"], reverse=True)
    top = cands_sorted[0]
    location = {
        "node_id": top["node_id"],
        "confidence": round(top["belief"], 3),
        "floor": top["attributes"].get("floor"),
        "room_type": top["attributes"].get("room_type"),
        "questions_asked": asked,
        "question_skipped": skip,
        "skip_reason": skip_reason,
        "question_log": log,
        "runner_up": (
            {"node_id": cands_sorted[1]["node_id"],
             "confidence": round(cands_sorted[1]["belief"], 3)}
            if len(cands_sorted) > 1 else None
        ),
        "evidence_path": top.get("evidence_path"),
        "below_threshold": localization.top_confidence(cands) < CONFIDENCE_THRESHOLD,
    }
    write_json(idir / "location.json", location)
    write_json(idir / "candidates.json", {"candidates": cands_sorted})
    return location


def run_site(idir: Path, building_id: str, address=None, lat=None, lon=None) -> dict:
    feats = stubs.detect_site_features(building_id, address=address, lat=lat, lon=lon)
    write_json(idir / "site.json", feats)
    return feats


def front_door_fallback_route() -> dict:
    # Stage at street door when we have no interior fix.
    return {
        "foot": {"entrance_id": "ext_main", "path": [], "seconds": None},
        "stretcher": {"entrance_id": "ext_main", "path": [], "seconds": None},
        "under_belief": None,
        "foot_directions": [],
        "stretcher_directions": [],
        "hazards": [],
        "equipment": [],
        "front_door_baseline": None,
        "stretcher_differs": False,
        "seconds_saved_vs_front": None,
    }


def empty_location(failure_mode: str) -> dict:
    return {
        "node_id": None,
        "confidence": 0.0,
        "questions_asked": 0,
        "evidence_path": None,
        "failure_mode": failure_mode,
    }


def run_router(idir: Path, building_id: str, location: dict, site: dict) -> dict:
    target = location["node_id"]
    candidates = json.loads((idir / "candidates.json").read_text())["candidates"]

    foot = routing.best_entry(building_id, target, "foot")
    stretcher = routing.best_entry(building_id, target, "stretcher")
    under_belief = routing.best_entry_under_belief(building_id, candidates, "foot")
    foot_dir = routing.directions(building_id, foot.get("path", []))
    stretcher_dir = routing.directions(building_id, stretcher.get("path", []))
    hazards = stubs.hazards_along(building_id, foot.get("path", []))
    aed = stubs.find_equipment(building_id, "aed", target)
    front = routing.route(building_id, "ext_main", target, "foot")

    stretcher_differs = (
        stretcher.get("entrance_id") != foot.get("entrance_id")
        or stretcher.get("path") != foot.get("path")
    )
    saved = None
    if front.get("seconds") and foot.get("seconds"):
        saved = round(front["seconds"] - foot["seconds"], 1)

    bundle = {
        "foot": foot,
        "stretcher": stretcher,
        "under_belief": under_belief,
        "foot_directions": foot_dir,
        "stretcher_directions": stretcher_dir,
        "hazards": hazards,
        "equipment": aed,
        "front_door_baseline": front,
        "stretcher_differs": stretcher_differs,
        "seconds_saved_vs_front": saved,
    }
    write_json(idir / "route.json", bundle)
    trace.log(
        "router", "best_entry+directions",
        result_summary=f"best door={foot.get('entrance_id')} saves {saved}s vs front",
        phase="subagent",
    )
    return bundle


def run_verifier(idir: Path, packet, brief, location, site, route_bundle,
                 corrections=None, extra_sources=None) -> dict:
    sources = {
        "brief": brief,
        "location": location,
        "site": site,
        "route": route_bundle,
        "corrections": corrections or [],
        "records": {"contact": (brief or {}).get("contact") if brief else None},
    }
    if extra_sources:
        sources.update(extra_sources)
    results = [stubs.check_claim(c, sources) for c in packet.tagged_claims()]
    unsupported = [r for r in results if not r["supported"]]
    audit = {
        "checked": len(results),
        "supported": len(results) - len(unsupported),
        "unsupported": unsupported,
        "claims": results,
    }
    write_json(idir / "audit.json", audit)
    trace.log(
        "verifier", "check_claim",
        result_summary=f"{audit['supported']}/{audit['checked']} supported",
        phase="subagent",
    )
    return audit
