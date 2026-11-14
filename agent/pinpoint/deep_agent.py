"""Real deepagents wiring for Pinpoint. Runs behind the `--real-llm` flag on
run_incident.py; the fallback orchestrator remains the default.

Fields verified against DEEPAGENTS_NOTES.md (overview, subagents, HITL docs).

Install (Python 3.11+ required by deepagents):
    pip install -r agent/requirements.txt
    export ANTHROPIC_API_KEY=...   # or put it in api.txt as `anthropic_api_key: sk-...`
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import stubs, routing, localization, trace, streaming
from .config import AGENT_DIR

DEFAULT_COMMANDER_MODEL = os.environ.get(
    "PINPOINT_COMMANDER_MODEL", "anthropic:claude-sonnet-5-5"
)
DEFAULT_WORKER_MODEL = os.environ.get(
    "PINPOINT_WORKER_MODEL", "anthropic:claude-haiku-5-5"
)
DEFAULT_LOCATOR_MODEL = os.environ.get(
    "PINPOINT_LOCATOR_MODEL", "anthropic:claude-sonnet-5-5"
)


class DeepAgentsNotConfigured(RuntimeError):
    """Raised when the real deepagents path cannot run (missing install or key)."""


COMMANDER_SYSTEM_PROMPT = """You are the Commander for Pinpoint, a 911 dispatch assistant.
You turn one caller photo plus a street address into a one-page responder packet and stop
for dispatcher approval before anything leaves the system.

Operating rules:

1. Write your plan BEFORE delegating. Call `write_todos` with the five steps: records,
   locator, site, router, verifier. Mark each one `in_progress` when you start and
   `completed` when its subagent returns.

2. Delegate `records`, `locator`, and `site` in parallel. Issue all three `task` tool
   calls in the SAME turn so the runtime can dispatch them concurrently. Do not wait for
   one to return before calling the next.

3. Only after records, locator, and site have ALL returned, delegate `router`.

4. After router returns, delegate `verifier`. The verifier audits that every claim in the
   packet cites a source. You do not send a packet whose audit did not pass.

5. Before you call `send_packet`, re-read the packet file and confirm the verifier's
   audit status is "OK". If the audit is DEGRADED or UNABLE, you still submit the packet,
   but the status field must reflect that honestly.

6. You never do graph math, image matching, or source lookups yourself. You delegate and
   read the JSON that subagents return. You never invent a fact a subagent did not
   provide.

7. `send_packet` and `notify_contact` will pause for dispatcher approval. That is by
   design. Do not try to work around it.

8. Keep your own context small. Write intermediate state to the incident folder via the
   filesystem tools and reference it by path.
"""


_VERIFIER_PROMPT = """You are the Verifier. For each sentence in the draft packet, confirm
it cites a source (a file path, record ID, or `src:<tag>`). If a sentence has no source,
flag it as unsupported. Write audit.json with the form {claims: N, sourced: M,
unsourced: K, status: "OK" | "DEGRADED" | "UNABLE"}. Status is OK iff K is zero and no
required section was dropped. Do not add new facts. Do not rewrite the packet. Report
what you see."""


def _load_api_key_from_file(path: Path) -> Optional[str]:
    """Scan api.txt for an Anthropic key. Supports two shapes:
      anthropic_api_key: sk-ant-...
      ANTHROPIC_API_KEY=sk-ant-...
    Returns the key if found, else None. Never prints the key.
    """
    if not path.exists():
        return None
    text = path.read_text()
    for pat in (
        r"(?im)^\s*anthropic[_\s-]*api[_\s-]*key\s*[:=]\s*(\S+)\s*$",
        r"(?im)^\s*ANTHROPIC_API_KEY\s*[:=]\s*(\S+)\s*$",
    ):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    m = re.search(r"\b(sk-ant-[A-Za-z0-9_\-]+)\b", text)
    return m.group(1) if m else None


def _ensure_api_key() -> str:
    """Return a usable Anthropic key or raise DeepAgentsNotConfigured. Sets os.environ as
    a side effect so langchain-anthropic picks it up. Never prints the key."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    api_txt = AGENT_DIR.parent / "api.txt"
    key = _load_api_key_from_file(api_txt)
    if not key:
        raise DeepAgentsNotConfigured(
            "ANTHROPIC_API_KEY not set and no `anthropic_api_key:` line found in "
            "api.txt. Set the env var or add the key to api.txt before using --real-llm."
        )
    os.environ["ANTHROPIC_API_KEY"] = key
    return key


def build_subagents() -> list[dict[str, Any]]:
    """Five dict-based subagents, fields per DEEPAGENTS_NOTES.md."""
    return [
        {
            "name": "records",
            "description": "Pull the CoStar property record for the caller's address.",
            "system_prompt": (
                "Call `get_property(address)` and return a brief JSON object with the "
                "building_id, use_type, stories, year_built, sqft, tenants, and contact. "
                "Flag any clinic, daycare, or lab tenant on the identified floor. Cite "
                "the CoStar record ID as the evidence_path for every field you return."
            ),
            "tools": [stubs.get_property],
            "model": DEFAULT_WORKER_MODEL,
            "mode": "isolated",
        },
        {
            "name": "locator",
            "description": (
                "Match the caller photo to a position inside the building; ask the "
                "caller follow-up questions only when they would change the answer."
            ),
            "system_prompt": (
                "Call `match_photo(building_id, image_path, k=10)` to get candidates. "
                "Normalize via `update_belief` with no answer to establish the prior. "
                "While the top candidate's belief is below 0.80 and you have questions "
                "left, call `best_question(candidates)` to pick the highest-info-gain "
                "attribute, phrase it naturally, call `ask_caller` with the phrasing, "
                "then call `update_belief(candidates, attribute, answer)` with the "
                "response. Stop early when the top candidate passes threshold. Return "
                "location.json with the predicted node_id, room_id, belief, and the "
                "evidence_path from the top candidate."
            ),
            "tools": [
                stubs.match_photo,
                stubs.look,
                localization.best_question,
                localization.update_belief,
                stubs.ask_caller,
            ],
            "model": DEFAULT_LOCATOR_MODEL,
            "mode": "isolated",
        },
        {
            "name": "site",
            "description": (
                "Read the aerial tile for this building's address; return entrances, "
                "staging candidates, and obstructions."
            ),
            "system_prompt": (
                "Call `detect_site_features(building_id, address=..., lat=..., "
                "lon=...)` and return site.json with entrances, staging areas, "
                "obstructions, and an `aerial_tile_path` evidence_path when Vexcel data "
                "is available. On Vexcel fallback, cite the fixture tile path instead."
            ),
            "tools": [stubs.detect_site_features],
            "model": DEFAULT_WORKER_MODEL,
            "mode": "isolated",
        },
        {
            "name": "router",
            "description": (
                "Pick the best responder staging door given the belief; produce the "
                "route and any hazards along it. Compare to the front-door baseline for "
                "the meters-saved number."
            ),
            "system_prompt": (
                "Call `best_entry_under_belief` with the candidate set to pick the "
                "staging door. Call `route` for the main path and `backup_route` for an "
                "alternative. Call `hazards_along(building_id, path)` to decorate "
                "hazards. Return route.json with the chosen door_id, the meters saved "
                "against the front-door baseline, and the hazard list. Every field "
                "cites the graph node or edge it came from."
            ),
            "tools": [
                routing.best_entry_under_belief,
                routing.route,
                stubs.hazards_along,
                routing.backup_route,
            ],
            "model": DEFAULT_WORKER_MODEL,
            "mode": "isolated",
        },
        {
            "name": "verifier",
            "description": "Audit the draft packet: every claim needs a source or it is flagged.",
            "system_prompt": _VERIFIER_PROMPT,
            "tools": [],
            "model": DEFAULT_WORKER_MODEL,
            "mode": "isolated",
        },
    ]


def build_agent(checkpointer=None):
    """Construct the deep agent. Raises DeepAgentsNotConfigured with a one-line
    remediation if deepagents or langgraph is not importable."""
    try:
        from deepagents import create_deep_agent
    except ImportError as e:
        raise DeepAgentsNotConfigured(
            "deepagents not installed. Run `pip install -r agent/requirements.txt` "
            f"(needs Python 3.11+). Import error: {e}"
        )
    try:
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError as e:
        raise DeepAgentsNotConfigured(
            "langgraph not installed. Run `pip install -r agent/requirements.txt`. "
            f"Import error: {e}"
        )
    TodoListMiddleware = None
    try:
        from deepagents.middleware import TodoListMiddleware as _T
        TodoListMiddleware = _T
    except ImportError:
        try:
            from deepagents import TodoListMiddleware as _T
            TodoListMiddleware = _T
        except ImportError:
            pass
    middleware = [TodoListMiddleware()] if TodoListMiddleware else []
    if checkpointer is None:
        checkpointer = MemorySaver()
    return create_deep_agent(
        model=DEFAULT_COMMANDER_MODEL,
        system_prompt=COMMANDER_SYSTEM_PROMPT,
        tools=[stubs.send_packet, stubs.notify_contact],
        subagents=build_subagents(),
        middleware=middleware,
        interrupt_on={
            "send_packet": {"allowed_decisions": ["approve", "reject"]},
            "notify_contact": {"allowed_decisions": ["approve", "reject"]},
        },
        checkpointer=checkpointer,
    )


def run_real_agent(intake: dict, incident_dir: Path, auto_approve: bool = True) -> dict:
    """Live end-to-end on the real deepagents runtime.

    Raises DeepAgentsNotConfigured if deepagents / langgraph are missing or no API key is
    reachable. In mock mode (auto_approve=True) resumes every interrupt with approve. In
    interactive mode prompts on stdin.

    Writes packet.md into incident_dir; returns a summary dict:
        {thread_id, elapsed_s, interrupts, dir}

    Streaming: iterates ``agent.stream(..., stream_mode=["updates", "messages"])`` so
    node deltas and LLM tokens flow to trace.jsonl + events.jsonl as they fire. D's
    console tails events.jsonl.
    """
    incident_dir = Path(incident_dir)
    incident_dir.mkdir(parents=True, exist_ok=True)

    _ensure_api_key()
    agent = build_agent()
    try:
        from langgraph.types import Command
    except ImportError as e:  # pragma: no cover
        raise DeepAgentsNotConfigured(f"langgraph.types.Command missing: {e}")

    trace.start(incident_dir)
    streaming.start_events_file(incident_dir)

    thread_id = intake.get("incident_id") or f"inc-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    user_msg = (
        f"Incident at {intake.get('address','UNKNOWN')}. "
        f"Caller photo: {intake.get('caller_photo_path','')}. "
        f"Caller said: {intake.get('caller_text','')}. "
        f"Produce the responder packet. Write it to "
        f"{incident_dir / 'packet.md'}."
    )

    t0 = time.time()
    trace.log("commander", "real_agent:start",
              args={"thread_id": thread_id}, phase="start")

    def _drain(stream_iter):
        """Forward every chunk; return the __interrupt__ payload if one was seen."""
        pending = None
        for item in stream_iter:
            if (isinstance(item, tuple) and len(item) == 2
                    and isinstance(item[0], str)):
                mode, chunk = item
            else:
                mode, chunk = "updates", item
            streaming.forward_chunk(mode, chunk, incident_dir)
            if mode == "updates" and streaming.chunk_is_interrupt(chunk):
                pending = chunk["__interrupt__"]
        return pending

    def _interrupt_requests(pending):
        """Pull the list of action_requests out of an Interrupt payload. Tolerates
        both the Interrupt object shape (`.value`) and the plain-dict shape used by
        test mocks."""
        if not pending:
            return []
        first = pending[0] if isinstance(pending, list) and pending else pending
        if hasattr(first, "value"):
            v = first.value or {}
        elif isinstance(first, dict):
            v = first.get("value", first)
        else:
            v = {}
        return v.get("action_requests", []) if isinstance(v, dict) else []

    stream_iter = agent.stream(
        {"messages": [{"role": "user", "content": user_msg}]},
        config=config,
        stream_mode=["updates", "messages"],
    )
    pending = _drain(stream_iter)

    interrupt_count = 0
    while pending:
        interrupt_count += 1
        req = _interrupt_requests(pending)
        actions = [r.get("action") or r.get("tool")
                   for r in req if isinstance(r, dict)]
        trace.log(
            "commander",
            f"interrupt:{','.join(str(a) for a in actions) or 'unknown'}",
            args={"n_requests": len(req)},
            phase="interrupt",
        )
        n_decisions = max(len(req), 1)
        if auto_approve:
            decisions = [{"type": "approve"} for _ in range(n_decisions)]
        else:
            ans = input(f"[APPROVAL GATE] approve {actions}? [y/N] ").strip().lower()
            dec = "approve" if ans == "y" else "reject"
            decisions = [{"type": dec} for _ in range(n_decisions)]
        stream_iter = agent.stream(
            Command(resume={"decisions": decisions}),
            config=config,
            stream_mode=["updates", "messages"],
        )
        pending = _drain(stream_iter)

    elapsed = time.time() - t0
    trace.log("commander", "real_agent:done",
              args={"interrupts": interrupt_count,
                    "elapsed_s": round(elapsed, 2)},
              phase="milestone")

    return {
        "thread_id": thread_id,
        "elapsed_s": round(elapsed, 2),
        "interrupts": interrupt_count,
        "dir": str(incident_dir),
    }


def run_incident(intake: dict):  # pragma: no cover - legacy entrypoint
    """Legacy entrypoint kept so the earlier `--real` CLI path still resolves."""
    from .config import INCIDENTS
    idir = INCIDENTS / (intake.get("incident_id") or f"inc-{int(time.time())}")
    return run_real_agent(intake, idir, auto_approve=False)
