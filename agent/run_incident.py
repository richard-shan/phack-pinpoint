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
    ap.add_argument("--real", action="store_true", help="use real deepagents (needs install+key)")
    args = ap.parse_args()

    intake = json.loads(Path(args.intake).read_text()) if args.intake \
        else json.loads((FIXTURES / "intake_sample.json").read_text())

    if args.real:
        from pinpoint import deep_agent
        print(deep_agent.run_incident(intake))
        return

    res = orchestrator.run_incident(intake, mock_llm=True,
                                    auto_approve=not args.interactive)
    print("\n=== incident complete ===")
    print(json.dumps(res, indent=2))
    print(f"\npacket:  {res['dir']}/packet.md")
    print(f"trace:   {res['dir']}/trace.jsonl")
    print(f"\nTime to DRAFT packet: {res['seconds_to_draft']}s   (total incl. approval: "
          f"{res['seconds_total']}s)")


if __name__ == "__main__":
    main()
