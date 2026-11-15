# Approval gates for the stub path (same idea as deepagents interrupt_on).
from __future__ import annotations

from . import stubs, trace


def approval_gate(incident_id, packet, auto_approve=True, approve_fn=None):
    # Ask before send_packet.
    trace.log("commander", "interrupt:send_packet",
              result_summary="awaiting dispatcher", phase="interrupt")
    if approve_fn is not None:
        return approve_fn(incident_id, packet.path)
    if auto_approve:
        return "approve"
    ans = input(f"\n[APPROVAL GATE] Send packet for {incident_id}? [y/N] ").strip().lower()
    return "approve" if ans == "y" else "reject"


def parse_decision(raw):
    # → (decision, corrected_room_id).
    if isinstance(raw, dict):
        return raw.get("decision", "approve"), raw.get("corrected_room_id")
    return raw, None


def notify_contact_gate(incident_id, packet, brief, auto_approve=True, approve_fn=None):
    # Second gate: notify building contact after packet is approved.
    contact = (brief or {}).get("contact") if brief else None
    has_method = bool(
        contact and (contact.get("phone") or contact.get("email") or contact.get("method"))
    )
    if not (contact and contact.get("name") and has_method):
        reason = "no contact in brief" if not contact else "contact missing name or method"
        trace.log("commander", "notify_contact:skipped", result_summary=reason, phase="skip")
        return None

    trace.log("commander", "interrupt:notify_contact",
              result_summary="awaiting dispatcher", phase="interrupt")
    if approve_fn is not None:
        raw = approve_fn(incident_id, packet.path)
    elif auto_approve:
        raw = "approve"
    else:
        ans = input(f"\n[APPROVAL GATE 2] Notify contact for {incident_id}? [y/N] ").strip().lower()
        raw = "approve" if ans == "y" else "reject"

    decision, _ = parse_decision(raw)
    trace.log("dispatcher", "approval", result_summary=decision, phase="interrupt")
    if decision == "approve":
        res = stubs.notify_contact(incident_id, contact)
        trace.log("commander", "notify_contact", result_summary=res, phase="gate")
    return decision
