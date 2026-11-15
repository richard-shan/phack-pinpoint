# LLM path (--real-llm). Spec in agent_spec.py. Needs Python 3.11+ + ANTHROPIC_API_KEY.
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from . import stubs, trace, streaming
from .agent_spec import (
    COMMANDER_SYSTEM_PROMPT,
    COMMANDER_SYSTEM_PROMPT_NO_TODOS,
    COMMANDER_MODEL,
    INTERRUPT_ON,
    build_subagents,
)
from .config import AGENT_DIR, INCIDENTS

__all__ = [
    "DeepAgentsNotConfigured",
    "COMMANDER_SYSTEM_PROMPT",
    "COMMANDER_SYSTEM_PROMPT_NO_TODOS",
    "build_subagents",
    "build_agent",
    "run_real_agent",
    "run_incident",
    "_load_api_key_from_file",
]

_LOG = logging.getLogger("pinpoint.deep_agent")


class DeepAgentsNotConfigured(RuntimeError):
    pass


def _load_api_key_from_file(path: Path) -> Optional[str]:
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
    key = os.environ.get("ANTHROPIC_API_KEY") or _load_api_key_from_file(AGENT_DIR.parent / "api.txt")
    if not key:
        raise DeepAgentsNotConfigured(
            "ANTHROPIC_API_KEY not set and no anthropic_api_key in api.txt."
        )
    os.environ["ANTHROPIC_API_KEY"] = key
    return key


def _probe_todo_middleware():
    """Return TodoListMiddleware class or None if not importable."""
    for mod_name, attr in (
        ("deepagents.middleware", "TodoListMiddleware"),
        ("deepagents", "TodoListMiddleware"),
    ):
        try:
            return getattr(__import__(mod_name, fromlist=[attr]), attr)
        except (ImportError, AttributeError):
            continue
    return None


def build_agent(checkpointer=None, create_agent_fn=None):
    """Build the real deepagents agent.

    When TodoListMiddleware is not available, swap the Commander's system prompt to a
    variant that does not instruct `write_todos`. The model would otherwise call a tool
    that does not exist and crash on first real run. Graceful degradation is better here
    than a hard raise: the fallback orchestrator remains default, and real-llm mode
    still works without live todos.
    """
    if create_agent_fn is None:
        try:
            from deepagents import create_deep_agent as create_agent_fn
        except ImportError as e:
            raise DeepAgentsNotConfigured(f"deepagents not installed (needs Python 3.11+): {e}")
    try:
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError as e:
        raise DeepAgentsNotConfigured(f"langgraph not installed: {e}")

    TodoListMiddleware = _probe_todo_middleware()

    if TodoListMiddleware is not None:
        middleware = [TodoListMiddleware()]
        system_prompt = COMMANDER_SYSTEM_PROMPT
    else:
        _LOG.warning(
            "TodoListMiddleware not found; falling back to COMMANDER_SYSTEM_PROMPT_NO_TODOS "
            "so the model does not call a missing write_todos tool."
        )
        middleware = []
        system_prompt = COMMANDER_SYSTEM_PROMPT_NO_TODOS

    if checkpointer is None:
        checkpointer = MemorySaver()

    return create_agent_fn(
        model=COMMANDER_MODEL,
        system_prompt=system_prompt,
        tools=[stubs.send_packet, stubs.notify_contact],
        subagents=build_subagents(),
        middleware=middleware,
        interrupt_on=INTERRUPT_ON,
        checkpointer=checkpointer,
        memory=["AGENTS.md"],
    )


def run_real_agent(intake: dict, incident_dir: Path, auto_approve: bool = True) -> dict:
    # Stream a live deepagents run into trace + events.jsonl.
    incident_dir = Path(incident_dir)
    incident_dir.mkdir(parents=True, exist_ok=True)
    _ensure_api_key()
    agent = build_agent()

    try:
        from langgraph.types import Command
    except ImportError as e:
        raise DeepAgentsNotConfigured(f"langgraph.types.Command missing: {e}")

    trace.start(incident_dir)
    streaming.start_events_file(incident_dir)

    thread_id = intake.get("incident_id") or f"inc-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    user_msg = (
        f"Incident at {intake.get('address', 'UNKNOWN')}. "
        f"Caller photo: {intake.get('caller_photo_path', '')}. "
        f"Caller said: {intake.get('caller_text', '')}. "
        f"Produce the responder packet. Write it to {incident_dir / 'packet.md'}."
    )

    t0 = time.time()
    trace.log("commander", "real_agent:start", args={"thread_id": thread_id}, phase="start")

    pending = _drain(
        agent.stream(
            {"messages": [{"role": "user", "content": user_msg}]},
            config=config,
            stream_mode=["updates", "messages"],
        ),
        incident_dir,
    )

    interrupt_count = 0
    while pending:
        interrupt_count += 1
        req = _interrupt_requests(pending)
        actions = [r.get("action") or r.get("tool") for r in req if isinstance(r, dict)]
        trace.log(
            "commander",
            f"interrupt:{','.join(str(a) for a in actions) or 'unknown'}",
            args={"n_requests": len(req)},
            phase="interrupt",
        )
        n = max(len(req), 1)
        if auto_approve:
            decisions = [{"type": "approve"} for _ in range(n)]
        else:
            ans = input(f"[APPROVAL GATE] approve {actions}? [y/N] ").strip().lower()
            decisions = [{"type": "approve" if ans == "y" else "reject"} for _ in range(n)]
        pending = _drain(
            agent.stream(
                Command(resume={"decisions": decisions}),
                config=config,
                stream_mode=["updates", "messages"],
            ),
            incident_dir,
        )

    elapsed = time.time() - t0
    trace.log("commander", "real_agent:done",
              args={"interrupts": interrupt_count, "elapsed_s": round(elapsed, 2)},
              phase="milestone")
    return {
        "thread_id": thread_id,
        "elapsed_s": round(elapsed, 2),
        "interrupts": interrupt_count,
        "dir": str(incident_dir),
    }


def run_incident(intake: dict):  # pragma: no cover
    idir = INCIDENTS / (intake.get("incident_id") or f"inc-{int(time.time())}")
    return run_real_agent(intake, idir, auto_approve=False)


def _drain(stream_iter, incident_dir: Path):
    # Forward stream chunks; return interrupt payload if any.
    pending = None
    for item in stream_iter:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
            mode, chunk = item
        else:
            mode, chunk = "updates", item
        streaming.forward_chunk(mode, chunk, incident_dir)
        if mode == "updates" and streaming.chunk_is_interrupt(chunk):
            pending = chunk["__interrupt__"]
    return pending


def _interrupt_requests(pending):
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
