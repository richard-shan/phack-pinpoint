# Per-building AGENTS.md corrections from dispatcher rejects.
import re
from pathlib import Path
from datetime import datetime, timezone

from .config import BUILDINGS

ENTRY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*)\s+correction logged", re.M)


def agents_md_path(building_id: str) -> Path:
    return BUILDINGS / building_id / "AGENTS.md"


def append_correction(building_id: str, rejected_room_id, corrected_room_id,
                      caller_text: str = "", timestamp_iso: str = None) -> Path:
    # Append one correction; open('a') so a crash only loses this entry.
    path = agents_md_path(building_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    if timestamp_iso is None:
        timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rejected = _label(rejected_room_id)
    corrected = _label(corrected_room_id)
    caller = (caller_text or "").replace("\n", " ").strip()

    header = f"## {timestamp_iso} correction logged\n"
    body = (
        f"- Rejected: {rejected}\n"
        f"- Corrected to: {corrected}\n"
        f"- Caller text: {caller or '(none)'}\n"
        f"- Lesson: for calls matching this phrasing, prefer {corrected} over {rejected}.\n"
        "\n"
    )

    needs_prelude = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8") as f:
        if needs_prelude:
            f.write(f"# Dispatcher corrections for {building_id}\n\n"
                    "Append-only. Surfaced on the next incident at this building.\n\n")
        f.write(header)
        f.write(body)
    return path


def read_corrections(building_id: str, limit: int = 5) -> list:
    # Most recent N corrections (index 0 = newest).
    path = agents_md_path(building_id)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    entries = []
    chunks = re.split(r"^(## \d{4}-\d{2}-\d{2}T[^\n]+)$", text, flags=re.M)
    for i in range(1, len(chunks), 2):
        header = chunks[i]
        body = chunks[i + 1] if i + 1 < len(chunks) else ""
        m = ENTRY_RE.match(header)
        if not m:
            continue
        ts = m.group(1)
        entry = {"timestamp": ts, "rejected": None, "corrected": None,
                 "caller_text": None, "lesson": None}
        for line in body.splitlines():
            if line.startswith("- Rejected:"):
                entry["rejected"] = line.split(":", 1)[1].strip()
            elif line.startswith("- Corrected to:"):
                entry["corrected"] = line.split(":", 1)[1].strip()
            elif line.startswith("- Caller text:"):
                entry["caller_text"] = line.split(":", 1)[1].strip()
            elif line.startswith("- Lesson:"):
                entry["lesson"] = line.split(":", 1)[1].strip()
        entries.append(entry)
    entries.reverse()
    return entries[:limit]


def summarize_for_packet(corrections: list) -> list:
    # Markdown bullets with [src:corrections] for the packet.
    if not corrections:
        return []
    lines = []
    for c in corrections:
        date = (c.get("timestamp") or "")[:10]
        lesson = c.get("lesson") or (
            f"previous call corrected from {c.get('rejected')} to {c.get('corrected')}."
        )
        lines.append(f"- {date}: {lesson} [src:corrections]")
    return lines


def _label(room_id):
    if isinstance(room_id, dict):
        return room_id.get("room_label") or room_id.get("node_id") or str(room_id)
    return str(room_id) if room_id is not None else "(unknown)"
