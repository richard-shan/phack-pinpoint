"""Build lane B's photo index for the scan behind a lane A graph.

    .venv/bin/python -m live.index 6550930 [more building ids]

Lane B indexes every scan CoStar lists for a property; lane A's graph covers one scan.
This indexes that one scan, so every match has a route. Takes a few minutes per
building on a laptop CPU (52 views per scanned position).
"""
import json
import sys
import time

from env.graph import Graph

from . import keys


def build(building_id: str) -> dict:
    keys.export_env()
    from perception import index
    model = Graph.load(str(building_id)).data.get("matterport_model_id")
    if not model:
        raise ValueError(f"building {building_id} has no scan behind its graph")
    t0 = time.time()
    out = index.build(str(building_id), model_ids=[model])
    out["seconds"] = round(time.time() - t0, 1)
    return out


if __name__ == "__main__":
    for b in sys.argv[1:]:
        print(json.dumps(build(b), indent=1), flush=True)
