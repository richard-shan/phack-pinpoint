"""Identify a photo among the explicitly supported demo buildings.

Scores are heuristic, not calibrated across buildings. Reject weak or ambiguous matches.
"""
import json
import sys
from pathlib import Path

DEMO_BUILDINGS = ("6550930", "5956257")
MIN_INLIERS = 60
MIN_GAP = 20


def choose(results):
    ranked = sorted(results, key=lambda x: x["inliers"], reverse=True)
    if len(ranked) < 2 or ranked[0]["inliers"] < MIN_INLIERS or ranked[0]["inliers"] - ranked[1]["inliers"] < MIN_GAP:
        return None
    return ranked[0]["building_id"]


def infer(path):
    from perception.match import match_photo
    from perception.index import index_dir
    missing = [b for b in DEMO_BUILDINGS if not (Path(index_dir(b)) / 'emb.npy').exists()]
    if missing:
        raise RuntimeError('Demo photo indexes missing: ' + ', '.join(missing))
    results = []
    for building in DEMO_BUILDINGS:
        matches = match_photo(building, str(path), k=1)
        results.append({"building_id": building, "inliers": matches[0]["inliers"] if matches else 0})
    return {"building_id": choose(results), "candidates": results}


if __name__ == '__main__':
    try:
        print(json.dumps(infer(sys.argv[1])), flush=True)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        sys.exit(1)
