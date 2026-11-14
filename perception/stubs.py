"""Fixed, valid responses for the perception tools that are not built yet, so the
other lanes can call them. Shapes follow section 5 of pinpoint_plan.md.
match_photo is real: see perception/match.py."""


def node_attributes(node_id):
    """The `attributes` part of look(node_id). STUB."""
    return {
        "node_id": node_id, "stub": True,
        "room_type": "corridor", "has_window": False, "floor_surface": "carpet",
        "signage_text": ["Suite 205"], "near_elevator": False, "has_stairs": False,
        "equipment": [{"type": "extinguisher", "evidence_path": None}],
        "hazards": [], "confidence": 0.0, "evidence_path": None,
    }


def detect_site_features(building_id):
    """Entrances, staging spots and obstructions from the aerial tile.
    Uses the real tool (perception/site.py); falls back to the fixed response
    below, marked "stub": True, if it cannot run (no key, no network, no imagery).
    Pixel points are [x, y] in the image at tile_path."""
    try:
        from .site import detect_site_features as real
        return real(building_id)
    except Exception as e:
        out = _stub_site_features(building_id)
        out["stub_reason"] = str(e)[:200]
        return out


def _stub_site_features(building_id):
    return {
        "building_id": str(building_id), "stub": True,
        "tile_path": "cache/vexcel/240517/ortho_180m.jpg", "meters_per_pixel": 0.065,
        "entrances": [
            {"entrance_id": "ent_se", "side": "southeast", "pixel": [1530, 1640],
             "latlon": [30.02242, -95.44690], "kind": "main, under canopy", "confidence": 0.0},
            {"entrance_id": "ent_nw", "side": "northwest", "pixel": [1180, 1010],
             "latlon": [30.02275, -95.44725], "kind": "side door", "confidence": 0.0},
        ],
        "staging": [{"staging_id": "stg_1", "pixel": [1700, 1800], "latlon": [30.02232, -95.44675],
                     "kind": "parking aisle", "nearest_entrance": "ent_se"}],
        "obstructions": [{"type": "covered parking", "pixel": [2050, 950]}],
    }
