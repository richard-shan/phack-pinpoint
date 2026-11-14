# Default demo path: deterministic pipeline, no LLM.
from __future__ import annotations

import concurrent.futures as cf
import time
from pathlib import Path

from . import stubs, learning, trace
from .config import INCIDENTS
from .errors import PropertyNotFound, MatterportUnavailable, EmptyCandidates
from .packet import PacketWriter
from .pipeline import (
    plan_md, run_records, run_locator, run_site, run_router, run_verifier,
    should_skip_questions,
)
from .gates import approval_gate, parse_decision, notify_contact_gate
from .failures import (
    apply_records_failure, apply_locator_failure, apply_exhausted,
    unable_intake_incomplete,
)
from .util import write_json

# tests import these names from here
_should_skip_questions = should_skip_questions
_write = write_json


def run_incident(intake: dict, mock_llm=True, auto_approve=True, approve_fn=None,
                 simulate_rejection_all=False):
    required = ("address", "caller_photo_path", "building_id")
    missing = [f for f in required if not (intake or {}).get(f)]
    incident_id, idir = _alloc_incident_dir(intake)
    intake = dict(intake, incident_id=incident_id)
    write_json(idir / "intake.json", intake)
    trace.start(idir)

    if missing:
        for m in missing:
            trace.log("commander", f"intake_missing:{m}",
                      args={"field": m}, result_summary="required field absent",
                      phase="validation")
        return unable_intake_incomplete(idir, intake, missing, auto_approve, approve_fn)

    building_id = intake["building_id"]
    corrections = learning.read_corrections(building_id)
    trace.log("commander", "read_corrections",
              args={"building_id": building_id},
              result_summary=f"{len(corrections)} prior correction(s)", phase="context")

    (idir / "plan.md").write_text(plan_md(incident_id, intake))
    trace.log("commander", "write_todos", result_summary="6-step incident plan", phase="plan")

    packet = PacketWriter(idir / "packet.md", intake)
    packet.set_correction_notes(learning.summarize_for_packet(corrections))
    packet.flush()

    brief, location, site, route_bundle = None, None, None, None
    records_failure, locator_failure = None, None
    failure_sources = {}

    # records / locator / site together
    trace.log("commander", "task:parallel", args=["records", "locator", "site"], phase="delegate")
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(run_records, idir, intake): "records",
            ex.submit(run_locator, idir, intake, mock_llm): "locator",
            ex.submit(run_site, idir, building_id, address=intake.get("address")): "site",
        }
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
                packet.set_brief(brief)
            elif kind == "locator":
                location = result
                packet.set_location(location)
            else:
                site = result
                packet.set_site(site)
            packet.flush()

    if records_failure is not None:
        location, failure_sources = apply_records_failure(packet, intake, records_failure)
        brief = site = None
    elif locator_failure is not None:
        location, route_bundle, failure_sources = apply_locator_failure(
            packet, intake, locator_failure,
        )
    else:
        route_bundle = run_router(idir, building_id, location, site)
        packet.set_route(route_bundle)
        packet.flush()

    audit = run_verifier(
        idir, packet,
        brief if records_failure is None else {"failure_mode": "property_not_found"},
        location,
        site if records_failure is None else {},
        route_bundle if (records_failure is None or locator_failure is not None) else None,
        corrections, extra_sources=failure_sources,
    )
    if records_failure is not None:
        audit["status"] = "UNABLE"
    elif locator_failure is not None:
        audit["status"] = "DEGRADED"
    else:
        audit["status"] = "OK"
    packet.set_audit(audit)
    packet.flush()
    write_json(idir / "audit.json", audit)

    seconds_to_draft = trace.elapsed_ms() / 1000.0
    trace.log("commander", "draft_ready",
              result_summary=f"{seconds_to_draft:.2f}s to draft packet", phase="milestone")

    decision, corrected_room_id = parse_decision(
        approval_gate(incident_id, packet, auto_approve, approve_fn)
    )
    trace.log("dispatcher", "approval", result_summary=decision, phase="interrupt")

    if decision == "approve":
        res = stubs.send_packet(incident_id)
        trace.log("commander", "send_packet", result_summary=res, phase="gate")
        packet.mark_sent()
        packet.flush()
    elif decision == "reject" and corrected_room_id:
        rejected_label = location.get("room_type") or location.get("node_id")
        path = learning.append_correction(
            building_id=building_id,
            rejected_room_id={"node_id": location.get("node_id"), "room_label": rejected_label},
            corrected_room_id=corrected_room_id,
            caller_text=intake.get("caller_text", ""),
        )
        trace.log("commander", "correction_logged",
                  args={"rejected": location.get("node_id"), "corrected": corrected_room_id},
                  result_summary=f"appended to {path}", phase="learning")

    if simulate_rejection_all and decision == "approve":
        decision = _simulate_exhausted(
            idir, intake, packet, brief, site, location, route_bundle,
            corrections, mock_llm, building_id,
        )

    if decision == "approve":
        notify_contact_gate(incident_id, packet, brief, auto_approve, approve_fn)

    total = trace.elapsed_ms() / 1000.0
    trace.log("commander", "done", result_summary=f"{total:.2f}s total", phase="end")
    trace.log("system", "incident_complete", result_summary=f"{total:.2f}s total", phase="end")
    write_json(idir / "summary.json", {
        "incident_id": incident_id,
        "decision": decision,
        "corrected_room_id": corrected_room_id,
        "seconds_to_draft": round(seconds_to_draft, 2),
        "seconds_total": round(total, 2),
        "questions_asked": (location or {}).get("questions_asked", 0),
        "prior_corrections_read": len(corrections),
    })
    return {
        "incident_id": incident_id,
        "dir": str(idir),
        "decision": decision,
        "seconds_to_draft": round(seconds_to_draft, 2),
        "seconds_total": round(total, 2),
    }


def _alloc_incident_dir(intake: dict) -> tuple[str, Path]:
    incident_id = (intake or {}).get("incident_id") or time.strftime("inc-%Y%m%d-%H%M%S")
    idir = INCIDENTS / incident_id
    suffix = 0
    while idir.exists() and (intake or {}).get("incident_id") is None:
        suffix += 1
        incident_id = time.strftime("inc-%Y%m%d-%H%M%S") + f"-{suffix}"
        idir = INCIDENTS / incident_id
    idir.mkdir(parents=True, exist_ok=True)
    return incident_id, idir


def _simulate_exhausted(idir, intake, packet, brief, site, location, route_bundle,
                        corrections, mock_llm, building_id) -> str:
    # Test-only: two rejects, no correction → exhausted.
    trace.log("dispatcher", "approval",
              result_summary="reject (no correction)", phase="interrupt")
    blacklisted = [location["node_id"]] if location and location.get("node_id") else []
    try:
        location2 = run_locator(idir, intake, mock_llm, blacklist=set(blacklisted))
        packet.set_location(location2)
        packet.flush()
        route_bundle = run_router(idir, building_id, location2, site)
        packet.set_route(route_bundle)
        packet.flush()
        trace.log("commander", "replan_after_reject",
                  result_summary=f"blacklisted={blacklisted}", phase="recovery")
        location = location2
    except (MatterportUnavailable, EmptyCandidates) as exc:
        trace.log("commander", f"replan_failed:{exc.failure_mode}",
                  result_summary=str(exc), phase="recovery")

    trace.log("dispatcher", "approval",
              result_summary="reject again (no correction)", phase="interrupt")
    extra = apply_exhausted(packet, intake, blacklisted)
    audit = run_verifier(idir, packet, brief, location, site, route_bundle,
                         corrections, extra_sources=extra)
    audit["status"] = "UNABLE"
    packet.set_audit(audit)
    packet.flush()
    write_json(idir / "audit.json", audit)
    trace.log("commander", "failure_mode:exhausted_candidates",
              result_summary="two rejects, no corrections", phase="failure")
    return "exhausted"
