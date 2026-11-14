"""Turn match evidence into a probability, and decide "confirmed" from that.

    python -m perception.calibrate records/*.json            # leave-one-building-out report
    python -m perception.calibrate records/*.json --save     # fit on all, write calibrator.json

A fixed matched-point threshold fails where it matters: in buildings with repeated
structure a wrong place can collect as many points as the right one, and a photo
taken exactly at a scanned spot collects far more than one taken between spots. So
instead of one cutoff, a small logistic regression combines several signals about
the top candidate and is fitted on photos taken BETWEEN scanned spots (the hard,
realistic case), then tested on a building it was not fitted on.

Signals (all computed from what match_photo already has):
  log_inliers      matched points that survive the geometry check, log scale
  inlier_ratio     share of raw matches that survive
  sim              retrieval similarity of the top position
  sim_margin       gap to the best position that is not next to the top one
  inlier_margin    same gap in log matched points
  neighbours_ok    adjacent scan positions that also verify (support, not competition)
  same_scan_top5   how many of the top five retrieved positions are in the top one's scan
"""
import glob
import json
import math
import os
import sys

import numpy as np

FEATURES = ("log_inliers", "inlier_ratio", "sim", "sim_margin", "inlier_margin",
            "neighbours_ok", "same_scan_top5")
VERIFY = 30            # matched points for a neighbouring position to count as support
ALPHA = 0.08           # weight of log matched points against retrieval similarity when ranking
CONFIRM_AT = 0.9
HERE = os.path.dirname(os.path.abspath(__file__))
SAVED = os.path.join(HERE, "calibrator.json")


def rank(cands, alpha=ALPHA):
    """Order shortlisted positions by retrieval similarity plus a bonus for matched
    points. Retrieval keeps the lead when point matching finds little, which is the
    guard against a noisy point count overriding a confident retrieval."""
    return sorted(cands, key=lambda c: c["sim_top3"] + alpha * math.log1p(c["inliers"]), reverse=True)


def features(ranked, nodes):
    """Signals about the top candidate. ranked: output of rank(); nodes: {node: {model,
    neighbors}} for the building."""
    top = ranked[0]
    adjacent = set(nodes[top["node"]]["neighbors"])
    rivals = [c for c in ranked[1:] if c["node"] not in adjacent]
    support = [c for c in ranked[1:] if c["node"] in adjacent]
    li = math.log1p(top["inliers"])
    return {
        "log_inliers": li,
        "inlier_ratio": top["inliers"] / max(top["matches"], 1),
        "sim": top["sim_top3"],
        "sim_margin": top["sim_top3"] - (max(c["sim_top3"] for c in rivals) if rivals else 0.0),
        "inlier_margin": li - (max(math.log1p(c["inliers"]) for c in rivals) if rivals else 0.0),
        "neighbours_ok": float(sum(c["inliers"] >= VERIFY for c in support)),
        "same_scan_top5": float(sum(nodes[c["node"]]["model"] == nodes[top["node"]]["model"]
                                    for c in sorted(ranked, key=lambda c: -c["sim_top3"])[:5])),
    }


def fit(X, y, l2=1.0, steps=3000, lr=0.1):
    """Logistic regression by gradient descent on standardised features."""
    X, y = np.asarray(X, float), np.asarray(y, float)
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1]), 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        w -= lr * (Z.T @ (p - y) / len(y) + l2 * w / len(y))
        b -= lr * float((p - y).mean())
    return {"features": list(FEATURES), "mean": mu.tolist(), "std": sd.tolist(),
            "weights": w.tolist(), "bias": b}


def probability(model, f):
    x = np.array([f[k] for k in model["features"]], float)
    z = float(((x - np.array(model["mean"])) / np.array(model["std"])) @ np.array(model["weights"]) + model["bias"])
    return 1 / (1 + math.exp(-z))


def load_saved():
    return json.load(open(SAVED)) if os.path.exists(SAVED) else None


def _rows(paths, alpha=ALPHA):
    rows = []
    for p in paths:
        d = json.load(open(p))
        for r in d["records"]:
            ranked = rank(r["cands"], alpha)
            top = ranked[0]["node"]
            rooms = d["nodes"]
            rows.append({
                "building": d["building"], "set": r["set"], "held_out": r["held_out"],
                "f": features(ranked, d["nodes"]),
                "hit": top in r["good"],
                "hit5": any(c["node"] in r["good"] for c in ranked[:5]),
                "room_answerable": r["room_answerable"],
                "room_hit": bool(r["room"]) and rooms[top]["room"] == r["room"],
                "retrieval_hit": max(r["cands"], key=lambda c: c["sim_top3"])["node"] in r["good"],
                "inlier_hit": max(r["cands"], key=lambda c: c["inliers"])["node"] in r["good"],
                "inliers": ranked[0]["inliers"],
            })
    return rows


def _pct(a):
    return "%3.0f%%" % (100 * sum(a) / len(a)) if len(a) else "  - "


def report(paths):
    rows = _rows(paths)
    buildings = sorted({r["building"] for r in rows})
    print("%d records from %d buildings" % (len(rows), len(buildings)))
    print("\nRanking (position hit, first guess): retrieval only | most matched points | combined | combined top-5 | room (answerable)")
    for held in (False, True):
        for s in ("tuned", "fresh"):
            g = [r for r in rows if r["held_out"] == held and r["set"] == s]
            ra = [r["room_hit"] for r in g if r["room_answerable"]]
            print("  %-9s %-6s n=%4d   %s | %s | %s | %s | %s" % (
                "held out" if held else "same spot", s, len(g), _pct([r["retrieval_hit"] for r in g]),
                _pct([r["inlier_hit"] for r in g]), _pct([r["hit"] for r in g]),
                _pct([r["hit5"] for r in g]), _pct(ra)))
    print("\nPer building, held out, combined ranking (position hit, first guess):")
    for b in buildings:
        g = [r for r in rows if r["building"] == b and r["held_out"]]
        print("  %-9s n=%4d  tuned %s  fresh %s" % (b, len(g), _pct([r["hit"] for r in g if r["set"] == "tuned"]),
                                                   _pct([r["hit"] for r in g if r["set"] == "fresh"])))
    print("\n'Confirmed' on a building the calibrator never saw (fit on the other four, held-out photos only):")
    print("  %-22s %-28s %s" % ("", "old rule: 60+ points", "calibrated: p >= %.1f" % CONFIRM_AT))
    tot = {}
    for b in buildings:
        train = [r for r in rows if r["building"] != b and r["held_out"]]
        m = fit([[r["f"][k] for k in FEATURES] for r in train], [r["hit"] for r in train])
        for r in rows:
            if r["building"] == b:
                r["p"] = probability(m, r["f"])
    for held in (False, True):
        for s in ("tuned", "fresh"):
            g = [r for r in rows if r["held_out"] == held and r["set"] == s]
            old = [r for r in g if r["inliers"] >= 60]
            new = [r for r in g if r["p"] >= CONFIRM_AT]
            print("  %-9s %-6s n=%4d  confirms %s, right %s        confirms %s, right %s" % (
                "held out" if held else "same spot", s, len(g),
                _pct([r["inliers"] >= 60 for r in g]), _pct([r["hit"] for r in old]),
                _pct([r["p"] >= CONFIRM_AT for r in g]), _pct([r["hit"] for r in new])))
    held = [r for r in rows if r["held_out"]]
    print("\nCalibration check, held out, all buildings (predicted vs actual):")
    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, .9), (.9, 1.01)):
        g = [r for r in held if lo <= r["p"] < hi]
        print("  p %.1f-%.1f  n=%4d  actually right %s" % (lo, min(hi, 1), len(g), _pct([r["hit"] for r in g])))
    return rows


def save(paths):
    rows = [r for r in _rows(paths) if r["held_out"]]
    m = fit([[r["f"][k] for k in FEATURES] for r in rows], [r["hit"] for r in rows])
    m.update(alpha=ALPHA, confirm_at=CONFIRM_AT, verify=VERIFY, fitted_on=len(rows),
             buildings=sorted({r["building"] for r in rows}))
    json.dump(m, open(SAVED, "w"), indent=1)
    print("saved", SAVED)
    for k, w in zip(FEATURES, m["weights"]):
        print("  %-16s %+.2f" % (k, w))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    paths = sorted(p for a in args for p in glob.glob(a))
    report(paths)
    if "--save" in sys.argv:
        save(paths)
