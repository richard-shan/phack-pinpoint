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

from . import stubs, routing, localization, trace, learning
from .errors import (PinpointFailure, PropertyNotFound, MatterportUnavailable,
                     EmptyCandidates, IntakeIncomplete, ExhaustedCandidates)
from .config import (INCIDENTS, DEMO_BUILDING, CONFIDENCE_THRESHOLD, MAX_QUESTIONS, AGENTS_MD)
from .packet import PacketWriter


def run_incident(intake: dict, mock_llm=True, auto_approve=True, approve_fn=None,
                 simulate_rejection_all=False):
    # ---- intake validation (fire before subagents) ----
    required = ("address", "caller_photo_path", "building_id")
    missing = [f for f in required if not (intake or {}).get(f)]
    incident_id = (intake or {}).get("incident_id") or time.strftime("inc-%Y%m%d-%H%M%S")
    idir = INCIDENTS / incident_id
    # if two runs fall in the same second (common in tests), append a suffix
    _suffix = 0
    while idir.exists() and intake.get("incident_id") is None:
        _suffix += 1
        incident_id = time.strftime("inc-%Y%m%d-%H%M%S") + f"-{_suffix}"
        idir = INCIDENTS / incident_id
    idir.mkdir(parents=True, exist_ok=True)
    intake = dict(intake, incident_id=incident_id)
    _write(idir / "intake.json", intake)

    trace.start(idir)
    if missing:
        for m in missing:
            trace.log("commander", f"intake_missing:{m}",
                      args={"field": m}, result_summary="required field absent",
                      phase="validation")
        return _unable_intake_incomplete(idir, intake, missing, auto_approve, approve_fn)
    building_id = intake["building_id"]

    # ---- learned corrections for this building (AGENTS.md) ----
    corrections = learning.read_corrections(building_id)
    trace.log("commander", "read_corrections",
              args={"building_id": building_id},
              result_summary=f"{len(corrections)} prior correction(s)",
              phase="context")

    # ---- Commander plans (plan.md) ----
    plan_md = _plan_md(incident_id, intake)
    (idir / "plan.md").write_text(plan_md)
    trace.log("commander", "write_todos", result_summary="6-step incident plan", phase="plan")

    packet = PacketWriter(idir / "packet.md", intake)
    packet.set_correction_notes(learning.summarize_for_packet(corrections))
    packet.flush()  # empty skeleton visible immediately

    # ---- Records / Locator / Site IN PARALLEL ----
    trace.log("commander", "task:parallel", args=["records", "locator", "site"], phase="delegate")
    brief = location = site = None
    locator_failure = None
    records_failure = None
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(_records, idir, intake): "records",
            ex.submit(_locator, idir, intake, mock_llm): "locator",
            ex.submit(_site, idir, building_id, address=intake.get("address")): "site",
        }
        # flush each packet section as its future finishes (not in submit order)
        for fut in cf.as_completed(futures):
            kind = futures[fut]
            try:
                result = fut.result()
            except PropertyNotFound as exc:
                records_failure = exc
                trace.log("commander", f"failure_mode:{exc.failure_mode}",
                          result_summary=str(exc), phase="failure")
                continue
            except (MatterportUnavailable, EmptyCandidates) as exc:
                locator_failure = exc
                trace.log("commander", f"failure_mode:{exc.failure_mode}",
                          result_summary=str(exc), phase="failure")
                continue
            if kind == "records":
                brief = result
                packet.set_brief(brief); packet.flush()
            elif kind == "locator":
                location = result
                packet.set_location(location); packet.flush()
            else:
                site = result
                packet.set_site(site); packet.flush()

    # ---- short-circuit: hard failure means skip Router and mark packet UNABLE ----
    failure_sources = {}
    if records_failure is not None:
        # Clear any partial state the Locator/Site futures left behind (they may have
        # finished OK in parallel while Records failed; we do not want to render them).
        packet.brief = packet.location = packet.site = packet.route = None
        brief = location = site = None
        packet.set_failure(
            status="UNABLE",
            mode=records_failure.failure_mode,
            sentence="Address not found in the property database. "
                     "Dispatcher must verify the address and re-run.",
            cite_tag="costar.lookup_miss",
            attempted={"address": intake.get("address")},
        )
        def _or_missing(x):
            return x if x not in (None, "") else "(missing)"
        failure_sources = {
            "costar": {"lookup_miss": _or_missing(intake.get("address"))},
            "intake": {"address": _or_missing(intake.get("address")),
                       "caller_photo_path": _or_missing(intake.get("caller_photo_path")),
                       "building_id": _or_missing(intake.get("building_id"))},
            "failure": {"mode": records_failure.failure_mode,
                        "detail": str(records_failure)},
        }
        location = {"node_id": None, "confidence": 0.0, "questions_asked": 0,
                    "evidence_path": None, "failure_mode": records_failure.failure_mode}
    elif locator_failure is not None:
        # Clear the Location section so an empty/None candidate does not render.
        packet.location = None
        is_mp = isinstance(locator_failure, MatterportUnavailable)
        sentence = ("Unable to match the caller photo to a position in this building. "
                    "Dispatcher must direct responders to the main entrance and relay "
                    "room info by voice.")
        cite = "matterport.unavailable" if is_mp else "locator.empty_result"
        src_block = ({"matterport": {"unavailable": str(locator_failure)}} if is_mp
                     else {"locator": {"empty_result": str(locator_failure)}})
        packet.set_failure(
            status="DEGRADED", mode=locator_failure.failure_mode,
            sentence=sentence, cite_tag=cite,
            attempted={"caller_photo_path": intake.get("caller_photo_path")},
        )
        _m = lambda x: x if x not in (None, "") else "(missing)"
        failure_sources = {**src_block,
                           "intake": {"caller_photo_path": _m(intake.get("caller_photo_path")),
                                      "address": _m(intake.get("address")),
                                      "building_id": _m(intake.get("building_id"))},
                           "failure": {"mode": locator_failure.failure_mode,
                                       "detail": str(locator_failure)}}
        # Degraded: we still have brief + site; default the staging door to ext_main.
        route_bundle = {
            "foot": {"entrance_id": "ext_main", "path": [], "seconds": None},
            "stretcher": {"entrance_id": "ext_main", "path": [], "seconds": None},
            "under_belief": None, "foot_directions": [], "stretcher_directions": [],
            "hazards": [], "equipment": [],
            "front_door_baseline": None, "stretcher_differs": False,
            "seconds_saved_vs_front": None}
        packet.set_route(route_bundle); packet.flush()
        location = {"node_id": None, "confidence": 0.0, "questions_asked": 0,
                    "evidence_path": None,
                    "failure_mode": locator_failure.failure_mode}
    else:
        # ---- Router (needs all three) ----
        route_bundle = _router(idir, building_id, location, site)
        packet.set_route(route_bundle); packet.flush()

    # ---- Verifier (runs on EVERY packet, degraded or not) ----
    audit = _verifier(idir, packet,
                      brief if records_failure is None else {"failure_mode": "property_not_found"},
                      location,
                      site if records_failure is None else {},
                      route_bundle if (records_failure is None or locator_failure is not None) else None,
                      corrections, extra_sources=failure_sources)
    if records_failure is not None or locator_failure is not None:
        audit["status"] = "UNABLE" if records_failure is not None else "DEGRADED"
    else:
        audit["status"] = "OK"
    packet.set_audit(audit); packet.flush()
    import json as _json
    (idir / "audit.json").write_text(_json.dumps(audit, indent=2, default=str))

    seconds_to_draft = trace.elapsed_ms() / 1000.0
    trace.log("commander", "draft_ready", result_summary=f"{seconds_to_draft:.2f}s to draft packet",
              phase="milestone")

    # ---- APPROVAL GATE (interrupt) ----
    decision_raw = _approval_gate(incident_id, packet, auto_approve, approve_fn)
    if isinstance(decision_raw, dict):
        decision = decision_raw.get("decision", "approve")
        corrected_room_id = decision_raw.get("corrected_room_id")
    else:
        decision = decision_raw
        corrected_room_id = None
    trace.log("dispatcher", "approval", result_summary=decision, phase="interrupt")
    if decision == "approve":
        res = stubs.send_packet(incident_id)
        trace.log("commander", "send_packet", result_summary=res, phase="gate")
        packet.mark_sent(); packet.flush()
    elif decision == "reject" and corrected_room_id:
        rejected_label = (location.get("room_type") or location.get("node_id"))
        path = learning.append_correction(
            building_id=building_id,
            rejected_room_id={"node_id": location.get("node_id"),
                              "room_label": rejected_label},
            corrected_room_id=corrected_room_id,
            caller_text=intake.get("caller_text", ""),
        )
        trace.log("commander", "correction_logged",
                  args={"rejected": location.get("node_id"),
                        "corrected": corrected_room_id},
                  result_summary=f"appended to {path}", phase="learning")

    # ---- rejection-retry / exhausted-candidates ----
    if simulate_rejection_all and decision == "approve":
        # Override: simulate the dispatcher rejecting with no correction, replan once, reject
        # again, and finish UNABLE/exhausted_candidates. The approval-gate contract still holds:
        # we log every reject event and the final UNABLE packet is still verified.
        trace.log("dispatcher", "approval",
                  result_summary="reject (no correction)", phase="interrupt")
        blacklisted = [location["node_id"]] if location and location.get("node_id") else []
        try:
            location2 = _locator(idir, intake, mock_llm, blacklist=set(blacklisted))
            packet.set_location(location2); packet.flush()
            route_bundle = _router(idir, building_id, location2, site)
            packet.set_route(route_bundle); packet.flush()
            trace.log("commander", "replan_after_reject",
                      result_summary=f"blacklisted={blacklisted}", phase="recovery")
            location = location2
        except (MatterportUnavailable, EmptyCandidates) as exc:
            trace.log("commander", f"replan_failed:{exc.failure_mode}",
                      result_summary=str(exc), phase="recovery")
        trace.log("dispatcher", "approval",
                  result_summary="reject again (no correction)", phase="interrupt")
        packet.set_failure(
            status="UNABLE", mode="exhausted_candidates",
            sentence=("Dispatcher rejected the top candidate twice without providing a "
                      "correction. The agent cannot propose a different room without more "
                      "input. Hand off to the dispatcher for manual direction."),
            cite_tag="candidates.exhausted",
            attempted={"blacklisted": ",".join(blacklisted) or "(none)"})
        _m2 = lambda x: x if x not in (None, "") else "(missing)"
        extra_fs = {
            "candidates": {"exhausted":
                           f"blacklisted after two rejections: {blacklisted}"},
            "intake": {"blacklisted": ",".join(blacklisted) or "(none)",
                       "address": _m2(intake.get("address")),
                       "caller_photo_path": _m2(intake.get("caller_photo_path")),
                       "building_id": _m2(intake.get("building_id"))},
            "failure": {"mode": "exhausted_candidates",
                        "detail": "two consecutive dispatcher rejections"},
        }
        audit = _verifier(idir, packet, brief, location, site, route_bundle, corrections,
                          extra_sources=extra_fs)
        audit["status"] = "UNABLE"
        packet.set_audit(audit); packet.flush()
        _write(idir / "audit.json", audit)
        trace.log("commander", "failure_mode:exhausted_candidates",
                  result_summary="two rejects, no corrections", phase="failure")
        decision = "exhausted"

    total = trace.elapsed_ms() / 1000.0
    trace.log("commander", "done", result_summary=f"{total:.2f}s total", phase="end")
    trace.log("system", "incident_complete", result_summary=f"{total:.2f}s total", phase="end")
    _write(idir / "summary.json", {"incident_id": incident_id, "decision": decision,
            "corrected_room_id": corrected_room_id,
            "seconds_to_draft": round(seconds_to_draft, 2), "seconds_total": round(total, 2),
            "questions_asked": location.get("questions_asked", 0),
            "prior_corrections_read": len(corrections)})
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


def _locator(idir, intake, mock_llm, blacklist=None):
    building_id = intake["building_id"]
    cands = stubs.match_photo(building_id, intake["caller_photo_path"], k=10)
    if blacklist:
        cands = [c for c in cands if c.get("node_id") not in blacklist]
    if not cands:
        raise EmptyCandidates("no candidates remain after blacklist")
    localization.normalize_belief(cands)
    _write(idir / "candidates.json", {"candidates": cands})

    truth = _ground_truth_attrs(building_id, intake=intake)
    asked = 0
    asked_attrs = []
    log = []

    skip, skip_reason = _should_skip_questions(building_id, cands)
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


def _site(idir, building_id, address=None, lat=None, lon=None):
    feats = stubs.detect_site_features(building_id, address=address, lat=lat, lon=lon)
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


def _verifier(idir, packet, brief, location, site, route_bundle, corrections=None,
              extra_sources=None):
    sources = {"brief": brief, "location": location, "site": site, "route": route_bundle,
               "corrections": corrections or []}
    if extra_sources:
        sources.update(extra_sources)
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
def _should_skip_questions(building_id, cands_now):
    """Skip asking when it cannot change the dispatched door: one candidate dominates, or every
    top candidate shares a room AND best_entry_under_belief says the same door."""
    above = [c for c in cands_now if c.get("belief", 0.0) > 0.1]
    if len(above) < 2:
        return True, "only one candidate above belief 0.1"
    nodes = routing.load_graph(building_id)["nodes"]
    rooms = {(nodes.get(c["node_id"]) or {}).get("room_label") or c["node_id"] for c in above}
    if len(rooms) > 1:
        return False, None
    ub = routing.best_entry_under_belief(building_id, above, "foot")
    if ub.get("question_worth_asking"):
        return False, None
    return True, (f"all top candidates share room={next(iter(rooms))} "
                  f"and best_entry={ub.get('entrance_id')}")


def _ground_truth_attrs(building_id, intake=None):
    """For mock caller answers. If the intake carries a ground_truth_node_id (synth eval), use
    that; otherwise fall back to the fixture's ground_truth_node (demo behavior)."""
    gt = (intake or {}).get("ground_truth_node_id") or stubs._load("match.json")["ground_truth_node"]
    node = routing.load_graph(building_id)["nodes"].get(gt)
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

# --------------------------- failure-mode helpers ---------------------------
def _unable_intake_incomplete(idir, intake, missing, auto_approve, approve_fn):
    """Fast-path UNABLE packet when the intake lacks address / photo_path / building_id."""
    packet = PacketWriter(idir / "packet.md", intake or {})
    packet.set_failure(
        status="UNABLE",
        mode="intake_incomplete",
        sentence=("Required intake field(s) missing: " + ", ".join(missing) +
                  ". Dispatcher must complete the intake and re-run."),
        cite_tag="intake.incomplete",
        attempted={f: (intake or {}).get(f) for f in ("address", "caller_photo_path",
                                                      "building_id")},
    )
    packet.flush()
    _m = lambda x: x if x not in (None, "") else "(missing)"
    extra = {
        "intake": {"incomplete": ", ".join(missing),
                   "address": _m((intake or {}).get("address")),
                   "caller_photo_path": _m((intake or {}).get("caller_photo_path")),
                   "building_id": _m((intake or {}).get("building_id"))},
        "failure": {"mode": "intake_incomplete",
                    "detail": "intake missing: " + ", ".join(missing)},
    }
    audit = _verifier(idir, packet, brief={}, location={}, site={}, route_bundle=None,
                      corrections=[], extra_sources=extra)
    audit["status"] = "UNABLE"
    packet.set_audit(audit); packet.flush()
    import json as _json
    (idir / "audit.json").write_text(_json.dumps(audit, indent=2, default=str))
    trace.log("commander", "failure_mode:intake_incomplete",
              result_summary="missing: " + ", ".join(missing), phase="failure")
    # Approval gate still fires so D can see the UNABLE packet.
    decision = _approval_gate(intake.get("incident_id") or idir.name, packet,
                              auto_approve, approve_fn)
    if isinstance(decision, dict):
        decision = decision.get("decision", "approve")
    trace.log("dispatcher", "approval", result_summary=decision, phase="interrupt")
    trace.log("commander", "done", result_summary="UNABLE/intake_incomplete", phase="end")
    _write(idir / "summary.json",
           {"incident_id": idir.name, "status": "UNABLE",
            "failure_mode": "intake_incomplete", "missing_fields": missing,
            "seconds_to_draft": round(trace.elapsed_ms() / 1000.0, 2),
            "decision": decision})
    return {"incident_id": idir.name, "dir": str(idir), "status": "UNABLE",
            "failure_mode": "intake_incomplete", "decision": decision,
            "seconds_to_draft": round(trace.elapsed_ms() / 1000.0, 2),
            "seconds_total": round(trace.elapsed_ms() / 1000.0, 2)}

