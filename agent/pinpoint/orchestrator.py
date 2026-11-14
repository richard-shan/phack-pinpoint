"""Fallback orchestrator — the runnable spine for Part C TODAY (see DEEPAGENTS_NOTES.md for why
this exists alongside deep_agent.py). It proves the exact pipeline the deep agent will run:

  1. write intake.json + plan.md
  2. Records / Locator / Site IN PARALLEL (threads) — the critical path (plan 4)
       Locator runs the active-localization question loop (best_question/update_belief)
  3. stream packet.md section-by-section as each subagent returns (perceived latency ~0)
  4. Router after the three; best door under belief
  5. Verifier audits every claim; unsupported lines flagged
  6. APPROVAL GATE (interrupt) before send_packet
  7. send_packet on approval

Same tool layer as deep_agent.py, so swapping orchestrators is a one-liner in run_incident.py.
"""
import json
import time
import concurrent.futures as cf
from pathlib import Path

from . import stubs, routing, localization, trace
from .config import (INCIDENTS, DEMO_BUILDING, CONFIDENCE_THRESHOLD, MAX_QUESTIONS, AGENTS_MD)
from .packet import PacketWriter


def run_incident(intake: dict, mock_llm=True, auto_approve=True, approve_fn=None):
    incident_id = intake.get("incident_id") or time.strftime("inc-%Y%m%d-%H%M%S")
    idir = INCIDENTS / incident_id
    idir.mkdir(parents=True, exist_ok=True)
    intake = dict(intake, incident_id=incident_id)
    _write(idir / "intake.json", intake)

    trace.start(idir)
    building_id = intake["building_id"]

    # ---- Commander plans (plan.md) ----
    plan_md = _plan_md(incident_id, intake)
    (idir / "plan.md").write_text(plan_md)
    trace.log("commander", "write_todos", result_summary="6-step incident plan", phase="plan")

    packet = PacketWriter(idir / "packet.md", intake)
    packet.flush()  # empty skeleton visible immediately

    # ---- Records / Locator / Site IN PARALLEL ----
    trace.log("commander", "task:parallel", args=["records", "locator", "site"], phase="delegate")
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        f_records = ex.submit(_records, idir, intake)
        f_locator = ex.submit(_locator, idir, intake, mock_llm)
        f_site = ex.submit(_site, idir, building_id)

        # stream each section the instant its subagent returns
        brief = f_records.result()
        packet.set_brief(brief); packet.flush()

        location = f_locator.result()
        packet.set_location(location); packet.flush()

        site = f_site.result()
        packet.set_site(site); packet.flush()

    # ---- Router (needs all three) ----
    route_bundle = _router(idir, building_id, location, site)
    packet.set_route(route_bundle); packet.flush()

    # ---- Verifier ----
    audit = _verifier(idir, packet, brief, location, site, route_bundle)
    packet.set_audit(audit); packet.flush()

    seconds_to_draft = trace.elapsed_ms() / 1000.0
    trace.log("commander", "draft_ready", result_summary=f"{seconds_to_draft:.2f}s to draft packet",
              phase="milestone")

    # ---- APPROVAL GATE (interrupt) ----
    decision = _approval_gate(incident_id, packet, auto_approve, approve_fn)
    trace.log("dispatcher", "approval", result_summary=decision, phase="interrupt")
    if decision == "approve":
        res = stubs.send_packet(incident_id)
        trace.log("commander", "send_packet", result_summary=res, phase="gate")
        packet.mark_sent(); packet.flush()

    total = trace.elapsed_ms() / 1000.0
    trace.log("system", "incident_complete", result_summary=f"{total:.2f}s total", phase="end")
    _write(idir / "summary.json", {"incident_id": incident_id, "decision": decision,
            "seconds_to_draft": round(seconds_to_draft, 2), "seconds_total": round(total, 2),
            "questions_asked": location.get("questions_asked", 0)})
    return {"incident_id": incident_id, "dir": str(idir), "decision": decision,
            "seconds_to_draft": round(seconds_to_draft, 2), "seconds_total": round(total, 2)}


# --------------------------- subagents ---------------------------
def _records(idir, intake):
    rec = stubs.get_property(intake["address"])
    located_suite = next((t for t in rec["tenants"] if t.get("floor") == 2
                          and t.get("use") == "clinic"), None)
    brief = {"building_id": rec["building_id"], "address": rec["address"],
             "use_type": rec["use_type"], "stories": rec["stories"],
             "year_built": rec["year_built"], "sqft": rec["sqft"],
             "scan_date": rec["scan_date"], "contact": rec["contact"],
             "located_tenant": located_suite, "evidence_path": rec["evidence_path"]}
    _write(idir / "brief.json", brief)
    return brief


def _locator(idir, intake, mock_llm):
    building_id = intake["building_id"]
    cands = stubs.match_photo(building_id, intake["caller_photo_path"], k=10)
    localization.normalize_belief(cands)
    _write(idir / "candidates.json", {"candidates": cands})

    truth = _ground_truth_attrs(building_id, intake=intake)
    asked = 0
    asked_attrs = []
    log = []
    skip_reason = None

    def _would_question_change_route(cands_now):
        """If every candidate above belief 0.1 routes to the same best entrance AND lives in
        the same room_id, asking the caller cannot change the dispatched route. Returns
        (skip: bool, reason: str|None, under_belief_snapshot: dict|None)."""
        above = [c for c in cands_now if c.get("belief", 0.0) > 0.1]
        if len(above) < 2:
            return True, "only one candidate above belief 0.1", None
        g = json.loads((stubs._B / "graph.json").read_text())
        nodes_by_id = {n["id"]: n for n in g["nodes"]}
        rooms = set()
        for c in above:
            n = nodes_by_id.get(c["node_id"], {})
            rooms.add(n.get("room_label") or c["node_id"])
        if len(rooms) > 1:
            return False, None, None
        ub = routing.best_entry_under_belief(building_id, cands_now, "foot")
        per = ub.get("per_candidate_best_door", {})
        above_ids = {c["node_id"] for c in above}
        best_doors = {per[nid] for nid in above_ids if nid in per}
        if len(best_doors) == 1:
            return True, f"all top candidates share room={next(iter(rooms))} and best_entry={next(iter(best_doors))}", ub
        return False, None, None

    skip, skip_reason, _ub = _would_question_change_route(cands)
    if skip:
        trace.log("commander", "question_skipped",
                  result_summary=skip_reason, phase="optimization")

    while not skip and asked < MAX_QUESTIONS:
        conf = localization.top_confidence(cands)
        if conf >= CONFIDENCE_THRESHOLD:
            break
        q = localization.best_question(cands, exclude=asked_attrs)
        if not q.get("eligible"):
            break
        # Commander phrases + asks; mock caller answers from ground truth
        reply = stubs.ask_caller(intake["incident_id"], text=q["attribute"],
                                 _mock_truth_attrs=truth)
        localization.update_belief(cands, q["attribute"], reply["raw"])
        asked_attrs.append(q["attribute"])
        asked += 1
        log.append({"q": q["attribute"], "hint": q["question_hint"],
                    "info_gain": q["expected_info_gain"], "answer": reply["raw"],
                    "top_conf_after": round(localization.top_confidence(cands), 3)})
        trace.log("locator", "ask+update_belief",
                  args={"attr": q["attribute"], "gain": q["expected_info_gain"]},
                  result_summary=f"conf->{localization.top_confidence(cands):.2f}", phase="subagent")

    cands_sorted = sorted(cands, key=lambda c: c["belief"], reverse=True)
    top = cands_sorted[0]
    location = {"node_id": top["node_id"], "confidence": round(top["belief"], 3),
                "floor": top["attributes"].get("floor"),
                "room_type": top["attributes"].get("room_type"),
                "questions_asked": asked,
                "question_skipped": skip,
                "skip_reason": skip_reason,
                "question_log": log,
                "runner_up": {"node_id": cands_sorted[1]["node_id"],
                              "confidence": round(cands_sorted[1]["belief"], 3)}
                             if len(cands_sorted) > 1 else None,
                "evidence_path": top.get("evidence_path"),
                "below_threshold": localization.top_confidence(cands) < CONFIDENCE_THRESHOLD}
    _write(idir / "location.json", location)
    _write(idir / "candidates.json", {"candidates": cands_sorted})
    return location


def _site(idir, building_id):
    feats = stubs.detect_site_features(building_id)
    _write(idir / "site.json", feats)
    return feats


def _router(idir, building_id, location, site):
    target = location["node_id"]
    candidates = json.loads((idir / "candidates.json").read_text())["candidates"]

    foot = routing.best_entry(building_id, target, "foot")
    stretcher = routing.best_entry(building_id, target, "stretcher")
    under_belief = routing.best_entry_under_belief(building_id, candidates, "foot")

    foot_dir = routing.directions(building_id, foot.get("path", []))
    stretcher_dir = routing.directions(building_id, stretcher.get("path", []))
    hazards = stubs.hazards_along(building_id, foot.get("path", []))
    aed = stubs.find_equipment(building_id, "aed", target)

    # front-door baseline for the "meters/seconds saved" metric (plan 9)
    front = routing.route(building_id, "ext_main", target, "foot")
    stretcher_differs = stretcher.get("entrance_id") != foot.get("entrance_id") or \
        stretcher.get("path") != foot.get("path")

    bundle = {"foot": foot, "stretcher": stretcher, "under_belief": under_belief,
              "foot_directions": foot_dir, "stretcher_directions": stretcher_dir,
              "hazards": hazards, "equipment": aed,
              "front_door_baseline": front, "stretcher_differs": stretcher_differs,
              "seconds_saved_vs_front": (round(front["seconds"] - foot["seconds"], 1)
                                         if front.get("seconds") and foot.get("seconds") else None)}
    _write(idir / "route.json", bundle)
    trace.log("router", "best_entry+directions",
              result_summary=f"best door={foot.get('entrance_id')} "
                             f"saves {bundle['seconds_saved_vs_front']}s vs front", phase="subagent")
    return bundle


def _verifier(idir, packet, brief, location, site, route_bundle):
    sources = {"brief": brief, "location": location, "site": site, "route": route_bundle}
    results = [stubs.check_claim(c, sources) for c in packet.tagged_claims()]
    unsupported = [r for r in results if not r["supported"]]
    audit = {"checked": len(results), "supported": len(results) - len(unsupported),
             "unsupported": unsupported, "claims": results}
    _write(idir / "audit.json", audit)
    trace.log("verifier", "check_claim",
              result_summary=f"{audit['supported']}/{audit['checked']} supported", phase="subagent")
    return audit


def _approval_gate(incident_id, packet, auto_approve, approve_fn):
    """The interrupt. In the real deep agent this is interrupt_on={'send_packet':True}. Here we
    pause and ask a human (or auto-approve for an unattended/mock run)."""
    trace.log("commander", "interrupt:send_packet", result_summary="awaiting dispatcher",
              phase="interrupt")
    if approve_fn is not None:
        return approve_fn(incident_id, packet.path)
    if auto_approve:
        return "approve"
    ans = input(f"\n[APPROVAL GATE] Send packet for {incident_id}? [y/N] ").strip().lower()
    return "approve" if ans == "y" else "reject"


# --------------------------- helpers ---------------------------
def _ground_truth_attrs(building_id, intake=None):
    """For mock caller answers. If the intake carries a ground_truth_node_id (synth eval), use
    that; otherwise fall back to the fixture's ground_truth_node (demo behavior)."""
    gt = None
    if intake:
        gt = intake.get("ground_truth_node_id")
    if not gt:
        gt = json.loads((stubs._B / "match.json").read_text())["ground_truth_node"]
    g = json.loads((stubs._B / "graph.json").read_text())
    node = next((n for n in g["nodes"] if n["id"] == gt), None)
    return node.get("attributes", {}) if node else {}


def _plan_md(incident_id, intake):
    return f"""# Incident {incident_id} — Commander plan

Caller address: {intake['address']}
Caller text: {intake.get('caller_text','')}

- [ ] 1. Records: pull CoStar brief for the address
- [ ] 2. Locator: match photo -> position; ask the caller only if it changes the answer
- [ ] 3. Site: read aerial for entrances / staging / obstructions
- [ ] 4. Router: best door + route (foot + stretcher) once 1-3 return
- [ ] 5. Verifier: every packet line must cite a source or be flagged
- [ ] 6. Pause for dispatcher approval, then send

Steps 1-3 run in parallel. Step 4 needs all three.
"""


def _write(path: Path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str))
