"""trace.jsonl writer — logs every tool call with timing. Plan 4: 'that folder is the trace
for the demo and the input for the eval script'. Doubles as the latency profiler.

One JSON object per line:
  {"t": 1731600000.12, "dt_ms": 4.1, "actor": "locator", "tool": "match_photo",
   "args": {...}, "result_summary": "...", "phase": "subagent"}
"""
import json
import time
import functools
from pathlib import Path
from contextlib import contextmanager

_ACTIVE = {"path": None, "t0": None}


def start(incident_dir: Path):
    """Begin a trace for one incident. Records wall-clock zero (photo arrival)."""
    _ACTIVE["path"] = Path(incident_dir) / "trace.jsonl"
    _ACTIVE["t0"] = time.time()
    _ACTIVE["path"].write_text("")  # truncate
    log("system", "intake_received", phase="start")


def log(actor: str, tool: str, args=None, result_summary=None, dt_ms=None, phase="tool"):
    if _ACTIVE["path"] is None:
        return
    rec = {
        "t": round(time.time(), 3),
        "elapsed_ms": round((time.time() - _ACTIVE["t0"]) * 1000, 1),
        "dt_ms": dt_ms,
        "actor": actor,
        "tool": tool,
        "phase": phase,
        "args": _trim(args),
        "result_summary": _trim(result_summary),
    }
    with _ACTIVE["path"].open("a") as f:
        f.write(json.dumps(rec) + "\n")


def elapsed_ms() -> float:
    return round((time.time() - _ACTIVE["t0"]) * 1000, 1) if _ACTIVE["t0"] else 0.0


@contextmanager
def span(actor: str, tool: str, args=None, phase="tool"):
    """Time a block and log it. Returns a dict you can set 'summary' on."""
    box = {"summary": None}
    t = time.time()
    try:
        yield box
    finally:
        log(actor, tool, args=args, result_summary=box["summary"],
            dt_ms=round((time.time() - t) * 1000, 2), phase=phase)


def traced(actor: str):
    """Decorator: time a tool fn and log a compact result summary."""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            t = time.time()
            out = fn(*a, **k)
            log(actor, fn.__name__, args={"args": _trim(a), "kwargs": _trim(k)},
                result_summary=_summarize(out), dt_ms=round((time.time() - t) * 1000, 2))
            return out
        return wrap
    return deco


def _trim(obj, limit=240):
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    except Exception:
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def _summarize(out):
    if isinstance(out, dict):
        keys = list(out.keys())
        return f"dict keys={keys}"
    if isinstance(out, list):
        return f"list len={len(out)}"
    return _trim(out)
