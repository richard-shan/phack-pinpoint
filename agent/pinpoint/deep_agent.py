# LLM path (--real-llm). Spec in agent_spec.py. Needs Python 3.11+ + ANTHROPIC_API_KEY.
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from . import stubs, trace, streaming, budget
from . import tool_wrappers as tw
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
    "_load_env_local_key",
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


def _load_env_local_key(path: Path) -> bool:
    """Parse a dotenv-style file for ANTHROPIC_API_KEY and promote to os.environ.

    Never returns, prints, or logs the key value. On success sets the env var and
    returns True. On any failure returns False and logs a generic message that
    does not name the file.
    """
    try:
        if not path.exists():
            return False
        text = path.read_text()
    except OSError:
        _LOG.info("env file not readable")
        return False
    pat = re.compile(
        r"""(?mx)
        ^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*
        (?:
            "(?P<dq>[^"\n]+)"    # double-quoted
          | '(?P<sq>[^'\n]+)'    # single-quoted
          | (?P<raw>\S+)         # bare
        )
        \s*$
        """
    )
    for m in pat.finditer(text):
        val = m.group("dq") or m.group("sq") or m.group("raw")
        if val:
            os.environ["ANTHROPIC_API_KEY"] = val
            return True
    return False


def _ensure_api_key() -> str:
    # Priority: existing env var, then api.txt, then .env.local at repo root.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        key = _load_api_key_from_file(AGENT_DIR.parent / "api.txt")
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _load_env_local_key(AGENT_DIR.parent / ".env.local")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise DeepAgentsNotConfigured(
            "ANTHROPIC_API_KEY not set (checked env, api.txt, .env.local)."
        )
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
        tools=[tw.send_packet, tw.notify_contact],
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

    # Pre-flight budget check: refuse to boot if cumulative spend crossed cap.
    remaining, exceeded = budget.check_budget()
    if exceeded:
        raise budget.BudgetExceeded(
            f"cumulative spend crossed cap of ${budget.BUDGET_USD:.2f}; "
            f"remaining: ${remaining:.4f}. Reset agent/.pinpoint_cost.json to continue."
        )
    _LOG.info("budget: $%.4f remaining of $%.2f cap", remaining, budget.BUDGET_USD)

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

    # Tally token usage from the final agent state and record against the cap.
    in_tok = out_tok = 0
    try:
        state = agent.get_state(config)
        messages = getattr(state, "values", {}).get("messages", []) if state else []
        for m in messages:
            usage = getattr(m, "usage_metadata", None) or (m.get("usage_metadata") if isinstance(m, dict) else None)
            if not usage:
                continue
            in_tok += int(usage.get("input_tokens", 0) or 0)
            out_tok += int(usage.get("output_tokens", 0) or 0)
    except Exception as e:  # pragma: no cover - best-effort accounting
        _LOG.info("budget: usage aggregation skipped (%s)", type(e).__name__)

    cost = budget.estimate_cost(in_tok, out_tok, COMMANDER_MODEL)
    try:
        state_after = budget.record_usage(in_tok, out_tok, COMMANDER_MODEL)
        print(f"[budget] run cost ${cost:.4f}; cumulative ${state_after.cumulative_usd:.4f} of ${budget.BUDGET_USD:.2f}")
    except Exception as e:  # pragma: no cover
        _LOG.warning("budget: record_usage failed (%s)", type(e).__name__)
        state_after = None

    trace.log("commander", "real_agent:done",
              args={
                  "interrupts": interrupt_count,
                  "elapsed_s": round(elapsed, 2),
                  "input_tokens": in_tok,
                  "output_tokens": out_tok,
                  "cost_usd": cost,
              },
              phase="milestone")
    return {
        "thread_id": thread_id,
        "elapsed_s": round(elapsed, 2),
        "interrupts": interrupt_count,
        "dir": str(incident_dir),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": cost,
        "cumulative_usd": state_after.cumulative_usd if state_after else None,
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
