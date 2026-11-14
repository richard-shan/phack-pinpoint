"""Paths, keys and routing assumptions for lane A.

Keys come from environment variables first, then from `api.txt` at the repo root.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
BUILDINGS = ROOT / "buildings"      # buildings/<building_id>/graph.json (the plan's layout)
CACHE = PKG / "cache"               # external responses and images, gitignored
DATA = PKG / "data"                 # small derived files that are committed

MATTERPORT_URL = "https://api.matterport.com/api/models/graph"
VEXCEL_URL = "https://api.vexcelgroup.com/v2"

PROFILES = ("foot", "stretcher")
INTERIOR_TYPES = ("open", "door", "locked_door", "stairs", "elevator")
EXTERIOR_TYPES = ("exterior_vehicle", "exterior_foot")
EDGE_TYPES = INTERIOR_TYPES + EXTERIOR_TYPES

# Routing assumptions. Every number here is a tunable starting value, not a measurement.
# Replace them with the interviewee's numbers and rerun `python -m env.costs`.
ROUTING = {
    "walk_mps": {"foot": 1.4, "stretcher": 1.0},
    "door_delay_s": {"foot": 3.0, "stretcher": 8.0},
    "locked_delay_s": {"foot": 90.0, "stretcher": 90.0},     # forcing entry or waiting for a key
    "stairs_s_per_floor": {"foot": 25.0, "stretcher": None},  # None: this profile cannot pass
    "floor_height_m": 3.5,              # converts a measured rise into floors for stair time
    "elevator_wait_s": 30.0,
    "elevator_s_per_floor": 5.0,
    "elevator_load_s": {"foot": 0.0, "stretcher": 10.0},
    "vehicle_mps": 4.5,                 # about 10 mph inside a lot
    "min_door_width_m": {"foot": 0.0, "stretcher": 0.75},     # narrower doors block the stretcher
    "backup_penalty": 3.0,              # cost multiplier on the first route's edges
    "blocked_detour_s": 60.0,           # assumed cost of meeting a blocked edge and turning back
    "ask_threshold_s": 15.0,            # ask the caller again if knowing the room saves more than this
}


@lru_cache(maxsize=1)
def _api_txt() -> dict[str, str]:
    """Parse api.txt: each value sits on the line after its label."""
    path = ROOT / "api.txt"
    if not path.exists():
        return {}
    lines = [ln.strip() for ln in path.read_text().splitlines()]
    wanted = {"API key": "MATTERPORT_KEY", "API token": "MATTERPORT_TOKEN",
              "SDK key": "MATTERPORT_SDK_KEY", "api_key": "VEXCEL_API_KEY"}
    found = {}
    for i, ln in enumerate(lines[:-1]):
        if ln in wanted:
            found[wanted[ln]] = lines[i + 1]
    return found


def key(name: str) -> str:
    value = os.environ.get(name) or _api_txt().get(name)
    if not value:
        raise RuntimeError(f"{name} is not set (env var or api.txt)")
    return value


def matterport_auth() -> tuple[str, str]:
    return key("MATTERPORT_KEY"), key("MATTERPORT_TOKEN")
