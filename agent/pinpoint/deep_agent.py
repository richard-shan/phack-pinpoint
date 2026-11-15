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
from . import packet as packet_mod
from . import util
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


def run_real_agent(intake: dict, incident_dir: Path, auto_approve: bool = True, approve_fn=None) -> dict:
    # Stream a live deepagents run into trace + events.jsonl.
    # approve_fn(incident_id, actions) -> "approve" | "reject": the same hook
    # orchestrator.run_incident takes, so a console can answer the gates.
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
        actions = []
        for r in req:
            if isinstance(r, dict):
                a = r.get("name") or r.get("action") or r.get("tool") or r.get("action_name")
                if a:
                    actions.append(str(a))
        tag = ",".join(actions) if actions else "unknown"
        trace.log(
            "commander",
            f"interrupt:{tag}",
            args={"n_requests": len(req), "actions": actions},
            phase="interrupt",
        )
        n = max(len(req), 1)
        if approve_fn is not None:
            try:  # whoever answers the gate reads the draft from disk, as on the fallback path
                _persist_artifacts(agent, config, intake, incident_dir)
            except Exception as e:
                _LOG.info("artifact persist before gate skipped: %s", type(e).__name__)
            ok = approve_fn(thread_id, actions) == "approve"
            decisions = [{"type": "approve"} if ok else
                         {"type": "reject", "message": "The dispatcher declined this action. Do not retry it."}
                         for _ in range(n)]
        elif auto_approve:
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

    # Persist the final packet and intermediate artifacts so the eval + D console
    # can read them from disk just like the fallback produces.
    try:
        _persist_artifacts(agent, config, intake, incident_dir)
    except Exception as e:  # best-effort; never crash the run
        _LOG.warning("artifact persist failed: %s", type(e).__name__)
        trace.log("commander", "artifact_write_failed:all", args={"error": str(e)[:200]},
                  phase="post")

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




# ------------------------------------------------------------------
# Artifact persistence (real-llm end-of-run)
# ------------------------------------------------------------------

def _extract_report(messages, model_name):
    """Scan messages for a tool result whose content looks like model_name's schema.

    Returns the first matching dict (parsed from JSON) or None. Tolerates several
    carriers: structured response_format on ai messages, tool-message content strings,
    plain dicts in message.additional_kwargs. Shape match is heuristic: we require that
    the required fields of each model are present as keys.
    """
    required_by_model = {
        "RecordsReport":  {"building_id", "address"},
        "LocationReport": {"node_id", "room_id", "belief"},
        "SiteReport":     {"entrances"},
        "RouteReport":    {"best_entry_id", "path_nodes"},
        "VerifierReport": {"status", "claims_total"},
    }
    needed = required_by_model.get(model_name, set())

    def _candidates(m):
        # try multiple carriers
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
        if isinstance(content, str) and content.strip().startswith("{"):
            try:
                import json as _j
                yield _j.loads(content)
            except Exception:
                pass
        if isinstance(content, dict):
            yield content
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    yield part
                    inner = part.get("input") or part.get("output") or part.get("content")
                    if isinstance(inner, dict):
                        yield inner
                    if isinstance(inner, str) and inner.strip().startswith("{"):
                        try:
                            import json as _j
                            yield _j.loads(inner)
                        except Exception:
                            pass
        ak = getattr(m, "additional_kwargs", None) or (m.get("additional_kwargs") if isinstance(m, dict) else None)
        if isinstance(ak, dict):
            yield ak
            if isinstance(ak.get("tool_input"), dict):
                yield ak["tool_input"]

    for m in messages or []:
        for c in _candidates(m):
            if isinstance(c, dict) and needed.issubset(set(c.keys())):
                return c
    return None


def _extract_plan(messages):
    """Pull write_todos content or a first substantive assistant plan from the stream."""
    import json as _j
    for m in messages or []:
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
        tool_calls = getattr(m, "tool_calls", None) or (m.get("tool_calls") if isinstance(m, dict) else None)
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else None
                args = tc.get("args") if isinstance(tc, dict) else None
                if name == "write_todos" and isinstance(args, dict):
                    todos = args.get("todos") or []
                    if todos:
                        lines = ["# Plan", ""]
                        for t in todos:
                            label = t.get("content") or t.get("task") or str(t)
                            status = t.get("status", "pending")
                            mark = {"completed": "[x]", "in_progress": "[~]"}.get(status, "[ ]")
                            lines.append(f"- {mark} {label}")
                        return "\n".join(lines) + "\n"
    # fallback: first assistant text that looks like a plan (numbered or bulleted)
    for m in messages or []:
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
        if isinstance(content, str) and len(content) > 60:
            low = content.lower()
            if "step" in low or "first" in low or "plan" in low or "\n1." in content:
                return "# Plan (from Commander)\n\n" + content.strip() + "\n"
    return "# Plan (not captured)\n\nThe Commander did not emit a distinct plan line; see trace.jsonl for the live tool-call sequence.\n"


def _extract_packet_from_fs(state_values):
    """Look for a packet.md the Commander wrote into the deepagents virtual filesystem.

    State shape in deepagents: state.get("files", {}) may map path -> content. Older
    versions use "virtual_files" or embed files in a dedicated field. We probe
    heuristically.
    """
    if not isinstance(state_values, dict):
        return None
    for key in ("files", "virtual_files", "fs", "filesystem"):
        files = state_values.get(key)
        if isinstance(files, dict):
            for path, content in files.items():
                if isinstance(path, str) and path.endswith("packet.md") and isinstance(content, str) and content.strip():
                    return content
    return None


def _render_packet_fallback(intake, brief, location, site, route, audit):
    """Minimal markdown stitcher for when the Commander did not write packet.md itself.

    Works directly from the LLM report dicts (pydantic model_dump shape). Does NOT
    reuse PacketWriter because that class is coupled to the fallback orchestrator's
    internal field names. Every claim carries a src tag so audit stays clean.
    """
    i = intake or {}
    addr = i.get("address") or "(no address supplied)"
    L = [f"# Responder Packet - {addr}", ""]
    L += ["**Status:** DRAFT - assembled from captured subagent reports (packet.md was not written to the virtual filesystem by the Commander)", ""]

    L += ["## Property (CoStar)"]
    if brief:
        L += [
            f"- Building id: `{brief.get('building_id', 'unknown')}` [src:records.building_id]",
            f"- Address: {brief.get('address', addr)} [src:records.address]",
        ]
        contact = brief.get("contact")
        if contact:
            name = contact.get("name", "(unnamed)")
            method = contact.get("method", "?")
            L += [f"- Point of contact: {name} via {method} [src:records.contact]"]
        tenants = brief.get("tenants") or []
        if tenants:
            L += [f"- Tenants on record: {len(tenants)} [src:records.tenants]"]
    else:
        L += ["- Records subagent did not return a RecordsReport [src:records.missing]"]
    L += [""]

    L += ["## Location (Matterport)"]
    if location:
        L += [
            f"- Node: `{location.get('node_id', 'unknown')}` [src:location.node_id]",
            f"- Room: {location.get('room_label') or location.get('room_id', '(unknown)')} [src:location.room_id]",
            f"- Belief: {float(location.get('belief', 0.0) or 0.0):.2f} [src:location.belief]",
            f"- Caller questions asked: {int(location.get('questions_asked', 0) or 0)} [src:location.questions_asked]",
        ]
        ep = location.get("evidence_path")
        if ep:
            L += [f"- Matched pano evidence: `{ep}` [src:location.evidence_path]"]
    else:
        L += ["- Locator subagent did not return a LocationReport [src:location.missing]"]
    L += [""]

    L += ["## Staging (Vexcel)"]
    if site:
        entrances = site.get("entrances") or []
        L += [f"- Entrances considered: {len(entrances)} [src:site.entrances]"]
        for e in entrances[:3]:
            label = e.get("label") or e.get("id") or "(unlabeled)"
            L += [f"  - {label} [src:site.entrance]"]
        tile = site.get("aerial_tile_path")
        if tile:
            L += [f"- Aerial reference: `{tile}` [src:site.aerial_tile_path]"]
    else:
        L += ["- Site subagent did not return a SiteReport [src:site.missing]"]
    L += [""]

    L += ["## Route"]
    if route and route.get("best_entry_id"):
        L += [
            f"- Stage at: `{route.get('best_entry_id')}` [src:route.best_entry_id]",
            f"- Path meters: {route.get('meters', '?')} [src:route.meters]",
            f"- Estimated seconds: {route.get('seconds', '?')} [src:route.seconds]",
        ]
        hazards = route.get("hazards") or []
        if hazards:
            L += [f"- Hazards along the path: {len(hazards)} [src:route.hazards]"]
    else:
        L += ["- Router did not produce a route (best_entry_under_belief / route calls were not made) [src:route.missing]"]
    L += [""]

    L += ["## Audit"]
    if audit:
        total = audit.get("claims_total", 0)
        sourced = audit.get("claims_sourced", 0)
        unsup = audit.get("unsupported") or []
        L += [
            f"- Status: {audit.get('status', '?')} [src:verifier.status]",
            f"- Claims checked: {total}, sourced: {sourced}, unsupported: {len(unsup)} [src:verifier.tally]",
        ]
        if unsup:
            L += [f"- Unsupported claims: {len(unsup)} [src:verifier.unsupported]"]
    else:
        L += ["- Verifier did not run; packet is not audit-approved [src:verifier.missing]"]
    L += [""]

    return "\n".join(L)


def _audit_from_trace(trace_path: Path):
    """Build an audit tally from trace.jsonl verify_section events if VerifierReport missing."""
    import json as _j
    if not trace_path.exists():
        return None
    claims_total = claims_sourced = 0
    unsupported = []
    for line in trace_path.read_text().splitlines():
        try:
            r = _j.loads(line)
        except Exception:
            continue
        tool = r.get("tool", "")
        if "verify_section" in tool:
            rs = r.get("result_summary")
            if isinstance(rs, str) and rs.startswith("dict"):
                continue
            if isinstance(rs, dict):
                claims_total += int(rs.get("claims_total", 0) or 0)
                claims_sourced += int(rs.get("claims_sourced", 0) or 0)
                unsupported.extend(rs.get("unsupported", []) or [])
    if claims_total == 0:
        return None
    return {
        "status": "OK" if not unsupported else "DEGRADED",
        "claims_total": claims_total,
        "claims_sourced": claims_sourced,
        "unsupported": unsupported,
    }


def _persist_artifacts(agent, config, intake, incident_dir):
    """Write the 9 incident artifacts to incident_dir, best-effort."""
    incident_dir = Path(incident_dir)

    # Collect messages from final state.
    state_values = {}
    messages = []
    try:
        state = agent.get_state(config)
        state_values = getattr(state, "values", {}) or {}
        messages = state_values.get("messages", []) or []
    except Exception as e:
        _LOG.info("could not read agent state for persist: %s", type(e).__name__)

    # Extract each subagent's report from the message history.
    brief    = _extract_report(messages, "RecordsReport")
    location = _extract_report(messages, "LocationReport")
    site     = _extract_report(messages, "SiteReport")
    route    = _extract_report(messages, "RouteReport")
    audit    = _extract_report(messages, "VerifierReport")

    def _safe_write(name, writer):
        try:
            writer()
        except Exception as e:
            _LOG.info("artifact %s not written: %s", name, type(e).__name__)
            trace.log("commander", f"artifact_write_failed:{name}",
                      args={"error": str(e)[:200]}, phase="post")

    _safe_write("intake.json",     lambda: util.write_json(incident_dir / "intake.json", intake or {}))
    _safe_write("plan.md",         lambda: (incident_dir / "plan.md").write_text(_extract_plan(messages)))
    _safe_write("brief.json",      lambda: util.write_json(incident_dir / "brief.json", brief or {"status": "not_captured"}))
    if location:
        _safe_write("location.json",   lambda: util.write_json(incident_dir / "location.json", location))
        _safe_write("candidates.json", lambda: util.write_json(incident_dir / "candidates.json", location.get("candidates", [])))
    else:
        _safe_write("location.json",   lambda: util.write_json(incident_dir / "location.json", {"status": "not_captured"}))
        _safe_write("candidates.json", lambda: util.write_json(incident_dir / "candidates.json", []))
    _safe_write("site.json",       lambda: util.write_json(incident_dir / "site.json", site or {"status": "not_captured"}))
    if route:
        _safe_write("route.json",  lambda: util.write_json(incident_dir / "route.json", route))
    else:
        _safe_write("route.json",  lambda: util.write_json(incident_dir / "route.json",
            {"status": "not_computed", "reason": "router subagent not invoked"}))
    if audit:
        _safe_write("audit.json",  lambda: util.write_json(incident_dir / "audit.json", audit))
    else:
        derived = _audit_from_trace(incident_dir / "trace.jsonl")
        _safe_write("audit.json",  lambda: util.write_json(incident_dir / "audit.json",
            derived or {"status": "not_captured", "claims_total": 0, "claims_sourced": 0, "unsupported": []}))

    # packet.md: prefer what the Commander wrote to the virtual fs; else render from reports.
    packet_text = _extract_packet_from_fs(state_values)
    if not packet_text:
        packet_text = _render_packet_fallback(intake, brief, location, site, route, audit)
    _safe_write("packet.md", lambda: (incident_dir / "packet.md").write_text(packet_text))

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
    first = pending[0] if isinstance(pending, (list, tuple)) and pending else pending
    if hasattr(first, "value"):
        v = first.value or {}
    elif isinstance(first, dict):
        v = first.get("value", first)
    else:
        v = {}
    return v.get("action_requests", []) if isinstance(v, dict) else []
