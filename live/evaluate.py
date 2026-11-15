"""How often the whole chain lands on the right position and the right door.

    .venv/bin/python -m live.evaluate 6550930 --n 20
    .venv/bin/python -m live.evaluate 6550930 --n 20 --held-out

For n scanned positions it renders a stand-in caller photo (live/demo_photo.py), runs
the incident with a simulated caller, and compares the result with where the photo was
taken.

The plain run is biased in the matcher's favour: the photo is cut from the same
panorama that the index holds views of, so the right answer is a near-copy of the
question. `--held-out` removes that: the position the photo was taken at is deleted
from the index before matching, so the matcher has only ever "seen" the place from the
neighbouring positions a few meters away, as it would for a caller standing between
scanned spots. The exact position can no longer be the answer; what counts is how far
off the answer is and whether the crew is still sent through the right door.

Still not covered by either run: a different camera, different lighting, people and
furniture that moved since the scan. Only real photos test those.
"""
import argparse
import json
import random
import statistics
import time
from pathlib import Path

from . import demo_photo, run, tools

from env import tools as A
from env.graph import Graph

ROOT = Path(__file__).resolve().parent.parent


_HIDE: dict = {"node": None}


def _hide_from_index() -> None:
    """Make lane B's matcher load its index without the position named in _HIDE."""
    import numpy as np
    from perception import match as B
    if getattr(B.load, "_held_out", False):
        return
    real = B.load

    def load(building_id):
        emb, meta = real(building_id)
        hide = _HIDE["node"]
        if not hide:
            return emb, meta
        keep = [i for i, r in enumerate(meta["rows"]) if r["node_id"].rsplit("/", 1)[-1] != hide]
        rows = []
        for i in keep:                       # neighbours named in a row must not point at the hidden one
            r = dict(meta["rows"][i])
            r["neighbors"] = [x for x in r.get("neighbors") or [] if x != hide]
            rows.append(r)
        return emb[np.array(keep)], dict(meta, rows=rows)

    load._held_out = True
    B.load = load


def evaluate(building_id: str, n: int = 20, seed: int = 1, held_out: bool = False) -> dict:
    if held_out:
        tools.install()
        _hide_from_index()
    g = Graph.load(building_id)
    sweeps = sorted(x for x in g.nodes if g.kind(x) == "pano")
    rng = random.Random(seed)
    picks = rng.sample(sweeps, min(n, len(sweeps)))
    rows = []
    for i, node in enumerate(picks):
        photo, _, heading = demo_photo.make(building_id, node, heading=rng.uniform(0, 360), pitch=rng.uniform(-12, 3),
                                            fov=rng.uniform(60, 74), out=str(ROOT / "cache" / "live" / "eval" / f"{building_id}_{node[:8]}.jpg"),
                                            seed=seed)
        t0 = time.time()
        _HIDE["node"] = node if held_out else None
        res = run.run_incident(building_id, str(photo), truth=node, auto_approve=True)
        _HIDE["node"] = None
        d = Path(res["dir"])
        loc = json.loads((d / "location.json").read_text()) if (d / "location.json").exists() else {}
        cands = json.loads((d / "candidates.json").read_text())["candidates"] if (d / "candidates.json").exists() else []
        first = max(cands, key=lambda c: c.get("score", 0))["node_id"] if cands else None
        got = loc.get("node_id")
        route = json.loads((d / "route.json").read_text()) if (d / "route.json").exists() else {}
        want_door = A.best_entry(building_id, node, "foot").get("entrance_id")
        rows.append({
            "truth": node, "heading": round(heading), "located": got,
            "match_top1_exact": first == node,
            "exact": got == node,
            "adjacent": bool(got) and (got == node or g.edge(node, got) is not None),
            "meters_off": round(g.straight_line_m(node, got), 1) if got and got in g.nodes else None,
            "nearest_scanned_m": round(min(g.straight_line_m(node, m) for m in sweeps if m != node
                                           and g.nodes[m].get("floor") == g.nodes[node].get("floor")), 1),
            "same_floor": bool(got) and g.nodes[got].get("floor") == g.nodes[node].get("floor"),
            "right_door": (route.get("foot") or {}).get("entrance_id") == want_door,
            "confidence": loc.get("confidence"), "questions": loc.get("questions_asked"),
            "seconds": round(time.time() - t0, 1), "status": json.loads((d / "audit.json").read_text()).get("status"),
            "unsupported": len(json.loads((d / "audit.json").read_text()).get("unsupported", [])),
        })
        r = rows[-1]
        print(f"{i + 1:>3}/{len(picks)} {node[:6]} -> {str(got)[:6]} "
              f"{'exact' if r['exact'] else 'adjacent' if r['adjacent'] else 'MISS'} {r['meters_off']} m off "
              f"(nearest scanned {r['nearest_scanned_m']} m) "
              f"door {'ok' if r['right_door'] else 'DIFFERENT'} conf {r['confidence']} q {r['questions']} {r['seconds']} s",
              flush=True)
    ok = [r for r in rows if r["located"]]
    pct = lambda k: round(100 * sum(bool(r[k]) for r in rows) / len(rows))
    within = lambda m: round(100 * sum(r["meters_off"] is not None and r["meters_off"] <= m and r["same_floor"]
                                       for r in rows) / len(rows))
    out = {"building_id": building_id, "incidents": len(rows),
           "mode": "held out: the photo's own position is removed from the index" if held_out
                   else "plain: the photo's own position is in the index (biased in the matcher's favour)",
           "photo_source": "rendered from the scan on a scanned position (easier than a real photo)",
           "within_3m_pct": within(3.0), "within_5m_pct": within(5.0), "same_floor_pct": pct("same_floor"),
           "median_nearest_scanned_m": statistics.median(r["nearest_scanned_m"] for r in rows),
           "matcher_top1_exact_pct": pct("match_top1_exact"),
           "located_exact_pct": pct("exact"), "located_exact_or_adjacent_pct": pct("adjacent"),
           "right_door_pct": pct("right_door"),
           "median_meters_off": statistics.median(r["meters_off"] for r in ok) if ok else None,
           "median_questions": statistics.median(r["questions"] or 0 for r in rows),
           "median_seconds_per_incident": statistics.median(r["seconds"] for r in rows),
           "packets_with_an_unsourced_line": sum(r["unsupported"] > 0 for r in rows),
           "rows": rows}
    path = ROOT / "cache" / "live" / f"eval_{building_id}{'_held_out' if held_out else ''}.json"
    path.write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("building_id")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--held-out", action="store_true",
                    help="remove the photo's own position from the index before matching")
    a = ap.parse_args()
    res = evaluate(a.building_id, a.n, a.seed, a.held_out)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
