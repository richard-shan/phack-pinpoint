# UNABLE / DEGRADED packet builders.
from __future__ import annotations

from pathlib import Path

from . import trace
from .errors import MatterportUnavailable
from .packet import PacketWriter
from .pipeline import run_verifier, empty_location, front_door_fallback_route
from .util import write_json, or_missing, intake_source_block
from .gates import approval_gate, parse_decision


def apply_records_failure(packet, intake, exc) -> tuple[dict, dict]:
    # Address miss → wipe partials, UNABLE.
    packet.brief = packet.location = packet.site = packet.route = None
    packet.set_failure(
        status="UNABLE",
        mode=exc.failure_mode,
        sentence=(
            "Address not found in the property database. "
            "Dispatcher must verify the address and re-run."
        ),
        cite_tag="costar.lookup_miss",
        attempted={"address": intake.get("address")},
    )
    sources = {
        "costar": {"lookup_miss": or_missing(intake.get("address"))},
        "intake": intake_source_block(intake),
        "failure": {"mode": exc.failure_mode, "detail": str(exc)},
    }
    return empty_location(exc.failure_mode), sources


def apply_locator_failure(packet, intake, exc) -> tuple[dict, dict, dict]:
    # Photo match failed → DEGRADED, stage at front door.
    packet.location = None
    is_mp = isinstance(exc, MatterportUnavailable)
    cite = "matterport.unavailable" if is_mp else "locator.empty_result"
    src_block = (
        {"matterport": {"unavailable": str(exc)}} if is_mp
        else {"locator": {"empty_result": str(exc)}}
    )
    packet.set_failure(
        status="DEGRADED",
        mode=exc.failure_mode,
        sentence=(
            "Unable to match the caller photo to a position in this building. "
            "Dispatcher must direct responders to the main entrance and relay "
            "room info by voice."
        ),
        cite_tag=cite,
        attempted={"caller_photo_path": intake.get("caller_photo_path")},
    )
    route = front_door_fallback_route()
    packet.set_route(route)
    packet.flush()
    sources = {
        **src_block,
        "intake": intake_source_block(intake),
        "failure": {"mode": exc.failure_mode, "detail": str(exc)},
    }
    return empty_location(exc.failure_mode), route, sources


def apply_exhausted(packet, intake, blacklisted) -> dict:
    packet.set_failure(
        status="UNABLE",
        mode="exhausted_candidates",
        sentence=(
            "Dispatcher rejected the top candidate twice without providing a "
            "correction. The agent cannot propose a different room without more "
            "input. Hand off to the dispatcher for manual direction."
        ),
        cite_tag="candidates.exhausted",
        attempted={"blacklisted": ",".join(blacklisted) or "(none)"},
    )
    return {
        "candidates": {
            "exhausted": f"blacklisted after two rejections: {blacklisted}",
        },
        "intake": {
            "blacklisted": ",".join(blacklisted) or "(none)",
            **intake_source_block(intake),
        },
        "failure": {
            "mode": "exhausted_candidates",
            "detail": "two consecutive dispatcher rejections",
        },
    }


def unable_intake_incomplete(idir: Path, intake, missing, auto_approve, approve_fn) -> dict:
    # Missing intake fields → UNABLE without running subagents.
    packet = PacketWriter(idir / "packet.md", intake or {})
    packet.set_failure(
        status="UNABLE",
        mode="intake_incomplete",
        sentence=(
            "Required intake field(s) missing: " + ", ".join(missing)
            + ". Dispatcher must complete the intake and re-run."
        ),
        cite_tag="intake.incomplete",
        attempted={f: (intake or {}).get(f) for f in ("address", "caller_photo_path", "building_id")},
    )
    packet.flush()
    extra = {
        "intake": {"incomplete": ", ".join(missing), **intake_source_block(intake)},
        "failure": {"mode": "intake_incomplete", "detail": "intake missing: " + ", ".join(missing)},
    }
    audit = run_verifier(idir, packet, {}, {}, {}, None, [], extra_sources=extra)
    audit["status"] = "UNABLE"
    packet.set_audit(audit)
    packet.flush()
    write_json(idir / "audit.json", audit)
    trace.log("commander", "failure_mode:intake_incomplete",
              result_summary="missing: " + ", ".join(missing), phase="failure")

    decision, _ = parse_decision(
        approval_gate(intake.get("incident_id") or idir.name, packet, auto_approve, approve_fn)
    )
    trace.log("dispatcher", "approval", result_summary=decision, phase="interrupt")
    trace.log("commander", "done", result_summary="UNABLE/intake_incomplete", phase="end")
    secs = round(trace.elapsed_ms() / 1000.0, 2)
    write_json(idir / "summary.json", {
        "incident_id": idir.name,
        "status": "UNABLE",
        "failure_mode": "intake_incomplete",
        "missing_fields": missing,
        "seconds_to_draft": secs,
        "decision": decision,
    })
    return {
        "incident_id": idir.name,
        "dir": str(idir),
        "status": "UNABLE",
        "failure_mode": "intake_incomplete",
        "decision": decision,
        "seconds_to_draft": secs,
        "seconds_total": secs,
    }
