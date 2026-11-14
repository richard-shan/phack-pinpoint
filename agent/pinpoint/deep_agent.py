"""REAL deepagents wiring — Commander + 5 subagents, verified against DEEPAGENTS_NOTES.md.

This is the production path. It needs: `pip install -r requirements.txt`, an ANTHROPIC_API_KEY,
and (for true parallel Records/Locator/Site) confirmation of the async-subagents API. Until then,
orchestrator.py is the runnable spine and proves the identical pipeline + filesystem + interrupt.

Both paths call the SAME tool layer (stubs.py / routing.py / localization.py), so switching is one
line in run_incident.py. The subagent tool assignments below mirror plan section 4's table exactly.
"""
import os

from . import stubs, routing, localization

MODEL = os.environ.get("PINPOINT_MODEL", "anthropic:claude-sonnet-4-6")

COMMANDER_PROMPT = """You are the Commander for Pinpoint, a 911 dispatch assistant. You turn one
caller photo + a street address into a one-page responder packet, then STOP for dispatcher approval.

Rules:
- You do NOT do graph math or image matching yourself. Delegate to subagents; read their JSON.
- Delegate Records, Locator and Site FIRST and let them run together. Router needs all three.
- The Locator keeps a belief over candidate rooms. Ask the caller a question ONLY when it changes
  the answer — if best_entry_under_belief says all candidates share the same best door, do not ask.
- Every line in the packet must cite a source file+field. Send the draft to the Verifier.
- NEVER call send_packet or notify_contact without dispatcher approval. The system will pause you.
- Write intermediate files into the incident folder. Keep your own context small.
"""


def build_subagents():
    """Dict-based subagents (fields confirmed in DEEPAGENTS_NOTES.md: name, description,
    system_prompt, tools)."""
    return [
        {"name": "records", "mode": "isolated",
         "description": "Pull the CoStar building brief for the caller's address.",
         "system_prompt": "Fetch the property record and write brief.json. Flag the located suite's "
                          "occupancy risk (clinic/daycare/lab).",
         "tools": [stubs.get_property, stubs.get_tenants]},
        {"name": "locator", "mode": "isolated",
         "description": "Match the caller photo to a position; disambiguate with questions.",
         "system_prompt": "Call match_photo, normalize to a belief. While top confidence < threshold "
                          "and you have questions left: call best_question, phrase it, ask_caller, "
                          "then update_belief. Stop early. Write location.json + candidates.json.",
         "tools": [stubs.match_photo, stubs.look, localization.best_question,
                   localization.update_belief, stubs.ask_caller]},
        {"name": "site", "mode": "isolated",
         "description": "Read the aerial tile for entrances, staging, obstructions.",
         "system_prompt": "Call detect_site_features and write site.json. Return pixel points so the "
                          "console can draw overlays.",
         "tools": [stubs.detect_site_features, stubs.entrances]},
        {"name": "router", "mode": "isolated",
         "description": "Best door + routes (foot + stretcher) once location and site exist.",
         "system_prompt": "Use best_entry per profile and best_entry_under_belief if the room is "
                          "uncertain. Produce directions, hazards_along, find_equipment. Compare to "
                          "the front-door baseline. Write route.json.",
         "tools": [routing.best_entry, routing.best_entry_under_belief, routing.route,
                   routing.backup_route, routing.replan, routing.directions, routing.exit_route,
                   stubs.hazards_along, stubs.find_equipment]},
        {"name": "verifier", "mode": "isolated",
         "description": "Audit the draft packet: every claim needs a source or it is flagged.",
         "system_prompt": "For each sentence in packet.md find the backing file+field via check_claim. "
                          "Drop or mark unsupported claims. Write audit.json.",
         "tools": [stubs.check_claim]},
    ]


def build_agent(checkpointer=None):
    """Construct the deep agent. Raises a clear error if deepagents isn't installed yet."""
    try:
        from deepagents import create_deep_agent
        from deepagents.middleware import TodoListMiddleware  # planning opt-in from v0.7
    except Exception as e:  # pragma: no cover - env dependent
        raise RuntimeError(
            "deepagents not installed. `pip install -r agent/requirements.txt` and set "
            "ANTHROPIC_API_KEY. Until then use orchestrator.run_incident (the fallback spine). "
            f"Import error: {e}")

    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    return create_deep_agent(
        model=MODEL,
        system_prompt=COMMANDER_PROMPT,
        tools=[stubs.ask_caller, stubs.send_packet, stubs.notify_contact],
        subagents=build_subagents(),
        middleware=[TodoListMiddleware()],
        interrupt_on={"send_packet": True, "notify_contact": True},  # approval gates
        checkpointer=checkpointer,
    )


def run_incident(intake: dict):  # pragma: no cover - needs API key
    """Real end-to-end run. Mirrors DEEPAGENTS_NOTES.md interrupt/resume flow."""
    agent = build_agent()  # raises the friendly install message if deepagents/langgraph missing
    from langgraph.types import Command
    config = {"configurable": {"thread_id": intake.get("incident_id", "inc-live")}}
    user = (f"Incident at {intake['address']}. Caller photo: {intake['caller_photo_path']}. "
            f"Caller said: {intake.get('caller_text','')}. Produce the responder packet.")
    result = agent.invoke({"messages": [{"role": "user", "content": user}]}, config=config)

    while result.get("__interrupt__"):
        req = result["__interrupt__"][0].value["action_requests"]
        print(f"[APPROVAL GATE] agent wants to call: {[r.get('action') for r in req]}")
        ans = input("approve? [y/N] ").strip().lower()
        decisions = [{"type": "approve" if ans == "y" else "reject"} for _ in req]
        result = agent.invoke(Command(resume={"decisions": decisions}), config=config)
    return result
