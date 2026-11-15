# Small shared helpers.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str))


def or_missing(value: Any) -> Any:
    return value if value not in (None, "") else "(missing)"


def intake_source_block(intake: dict | None) -> dict:
    i = intake or {}
    return {
        "address": or_missing(i.get("address")),
        "caller_photo_path": or_missing(i.get("caller_photo_path")),
        "building_id": or_missing(i.get("building_id")),
    }
