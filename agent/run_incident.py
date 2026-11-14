#!/usr/bin/env python3
"""Single entrypoint — run ONE full stub incident end to end.

    python agent/run_incident.py                 # fallback orchestrator, mock LLM, auto-approve
    python agent/run_incident.py --interactive   # prompt at the approval gate
    python agent/run_incident.py --real           # use the real deepagents path (needs install+key)

Watch agent/incidents/<id>/ fill in: intake.json -> plan.md -> brief/location/site -> route ->
audit -> packet.md, with trace.jsonl logging every tool call + timing.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pinpoint import orchestrator
from pinpoint.config import FIXTURES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake", help="path to an intake.json (default: sample fixture)")
    ap.add_argument("--interactive", action="store_true", help="prompt at the approval gate")
    ap.add_argument("--real-llm", "--real", dest="real_llm", action="store_true",
                    help="use real deepagents (needs Python 3.11+ install and ANTHROPIC_API_KEY)")
    ap.add_argument("--simulate-rejection-all", action="store_true",
                    help="simulate the dispatcher rejecting all candidates: replan once with the top blacklisted, then end UNABLE/exhausted_candidates.")
    ap.add_argument("--simulate-rejection", metavar="CORRECTED_ROOM_ID",
                    help="auto-reject the packet with this room as the dispatcher's correction, "
                         "then log it to agent/buildings/<id>/AGENTS.md")
    args = ap.parse_args()

    intake = json.loads(Path(args.intake).read_text()) if args.intake \
        else json.loads((FIXTURES / "intake_sample.json").read_text())

    if args.real_llm:
        from pinpoint import deep_agent
        from pinpoint.config import INCIDENTS
        import time
        incident_id = intake.get("incident_id") or f"inc-{int(time.time())}"
        idir = INCIDENTS / incident_id
        try:
            summary = deep_agent.run_real_agent(intake, idir, auto_approve=not args.interactive)
        except deep_agent.DeepAgentsNotConfigured as e:
            print(f"real-llm mode unavailable: {e}")
            print("falling back to the deterministic orchestrator; pass --real-llm again once the "
                  "install and key are in place.")
            return
        print("\n=== real-llm incident complete ===")
        print(summary)
        print(f"\npacket:  {idir}/packet.md")
        print(f"trace:   {idir}/trace.jsonl")
        return

    approve_fn = None
    if args.simulate_rejection:
        _corrected = args.simulate_rejection
        def approve_fn(incident_id, packet_path):
            return {"decision": "reject", "corrected_room_id": _corrected}

    res = orchestrator.run_incident(intake, mock_llm=True,
                                    auto_approve=(not args.interactive) and approve_fn is None,
                                    approve_fn=approve_fn,
                                    simulate_rejection_all=args.simulate_rejection_all)
    print("\n=== incident complete ===")
    print(json.dumps(res, indent=2))
    print(f"\npacket:  {res['dir']}/packet.md")
    print(f"trace:   {res['dir']}/trace.jsonl")
    print(f"\nTime to DRAFT packet: {res['seconds_to_draft']}s   (total incl. approval: "
          f"{res['seconds_total']}s)")


if __name__ == "__main__":
    main()
