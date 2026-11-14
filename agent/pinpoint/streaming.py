"""Live event streaming for the deepagents --real-llm path.

Fans out events to two places:

1. trace.jsonl via the existing `trace.log` helper (same file every tool call writes to).
2. A leaner `events.jsonl` next to it, optimized for a dashboard consumer (D's console).
   Each line is a self-contained JSON object with `t_ms` (elapsed from incident start),
   `kind` ("update" or "token"), `source` (node name), and `payload` (compact dict).

`events.jsonl` is append-only, flushed per line, and stays valid JSONL even if the
stream is interrupted mid-run. D's console can tail it like a log.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Tuple

from . import trace

_TOKEN_LIMIT = 200
_UPDATE_SUMMARY_LIMIT = 200


def _events_path(incident_dir: Path) -> Path:
    return Path(incident_dir) / "events.jsonl"


def start_events_file(incident_dir: Path) -> None:
    """Truncate `events.jsonl` and write a header event (t_ms from trace)."""
    p = _events_path(incident_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    _append(p, {
        "t_ms": trace.elapsed_ms(),
        "kind": "header",
        "source": "system",
        "payload": {"message": "stream started"},
    })


def forward_chunk(stream_mode: str, chunk: Any, incident_dir: Path) -> None:
    """Dispatch one streamed chunk from `agent.stream(..., stream_mode=[...])` to
    both trace.jsonl and events.jsonl.

    Accepted shapes:
    - stream_mode == "updates": `chunk` is a dict `{node_name: state_delta}`. A
      `__interrupt__` key is forwarded with `interrupt=True` in the payload so D's
      console can surface the gate.
    - stream_mode == "messages": `chunk` is either `(msg_chunk, metadata)` or the raw
      message. The content delta is extracted; empty deltas are skipped; longer than
      `_TOKEN_LIMIT` are truncated with an ellipsis.
    """
    p = _events_path(incident_dir)

    if stream_mode == "updates":
        if not isinstance(chunk, dict):
            return
        for node, delta in chunk.items():
            if node == "__interrupt__":
                payload = {"interrupt": True, "delta": _compact(delta)}
            else:
                payload = _compact(delta)
            trace.log(
                actor=str(node),
                tool="node_update",
                result_summary=payload,
                phase="stream_update",
            )
            _append(p, {
                "t_ms": trace.elapsed_ms(),
                "kind": "update",
                "source": str(node),
                "payload": payload,
            })
        return

    if stream_mode == "messages":
        msg, meta = _unpack_message(chunk)
        content_delta = _extract_content(msg)
        if not content_delta:
            return
        if len(content_delta) > _TOKEN_LIMIT:
            content_delta = content_delta[: _TOKEN_LIMIT - 3] + "..."
        node = "commander"
        if isinstance(meta, dict):
            node = meta.get("langgraph_node") or meta.get("node") or node
        trace.log(
            actor=str(node),
            tool="llm_token",
            args={"content_delta": content_delta},
            phase="stream_token",
        )
        _append(p, {
            "t_ms": trace.elapsed_ms(),
            "kind": "token",
            "source": str(node),
            "payload": {"content_delta": content_delta},
        })
        return


def tail_events(incident_dir: Path) -> Iterator[dict]:
    """Yield every parsed event currently on disk in `events.jsonl`, oldest first.
    Skips lines that are not valid JSON (partial writes should not happen because of
    `_append`, but we tolerate them anyway)."""
    p = _events_path(incident_dir)
    if not p.exists():
        return
    with p.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def chunk_is_interrupt(chunk: Any) -> bool:
    """True iff a stream_mode='updates' chunk carries an __interrupt__ payload."""
    return isinstance(chunk, dict) and "__interrupt__" in chunk


def _append(p: Path, obj: dict) -> None:
    line = json.dumps(obj, default=str)
    with p.open("a") as f:
        f.write(line + "\n")


def _compact(delta: Any, limit: int = _UPDATE_SUMMARY_LIMIT) -> Any:
    if isinstance(delta, dict):
        keys = list(delta.keys())
        return {"keys": keys[:10], "n_keys": len(keys)}
    if isinstance(delta, list):
        return {"n": len(delta)}
    s = str(delta)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _unpack_message(chunk: Any) -> Tuple[Any, Any]:
    if isinstance(chunk, tuple) and len(chunk) == 2:
        return chunk[0], chunk[1]
    return chunk, None


def _extract_content(msg: Any) -> str:
    if msg is None:
        return ""
    if hasattr(msg, "content"):
        c = msg.content
    elif isinstance(msg, dict):
        c = msg.get("content", "")
    else:
        c = str(msg)
    if isinstance(c, list):
        parts = []
        for block in c:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return c if isinstance(c, str) else str(c)
