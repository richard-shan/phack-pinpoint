# Per-building preplan: load graph + property + site + entrances once, reuse across incidents.
#
# The underlying files are already lru_cached in routing.load_graph and stubs._load_cached;
# this module adds an explicit aggregation layer with visible hit/miss stats so we can
# prove the reuse, and it is the single place orchestrator.warmup_preplan goes to warm
# the caches before the parallel subagent fan-out.
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import routing, stubs


@dataclass
class Preplan:
    building_id: str
    graph: dict
    property: dict
    site: dict
    entrances: list


_cache: dict[str, Preplan] = {}
_stats = {"hits": 0, "misses": 0}


def _build(building_id: str) -> Preplan:
    # Loads underlying JSON via the existing lru_caches. May raise MatterportUnavailable.
    graph = routing.load_graph(building_id)
    # stubs._load is keyed by filename only; preplan exists mainly so this becomes
    # a per-building API with stats, even though the raw file content is shared.
    property_dict = stubs._load("property.json")
    site_dict = stubs._load("site.json")
    entrances = site_dict.get("entrances", [])
    return Preplan(
        building_id=building_id,
        graph=graph,
        property=property_dict,
        site=site_dict,
        entrances=entrances,
    )


def get_preplan(building_id: str) -> Preplan:
    if building_id in _cache:
        _stats["hits"] += 1
        return _cache[building_id]
    _stats["misses"] += 1
    pre = _build(building_id)
    _cache[building_id] = pre
    return pre


def warmup_preplan(building_id: str) -> None:
    # Fire-and-forget: ensures the building is in the cache. No-op on hit.
    try:
        get_preplan(building_id)
    except Exception:
        # Warmup is best-effort; routing will raise again at real use time if needed.
        pass


def cache_stats() -> dict[str, int]:
    return dict(_stats)


def _reset_for_tests() -> None:
    _cache.clear()
    _stats["hits"] = 0
    _stats["misses"] = 0
