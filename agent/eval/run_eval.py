#!/usr/bin/env python3
"""Pinpoint Part C eval harness.

Labeled-incident input schema (one JSON file per incident, from agent/eval/synth.py or hand):
    {
      "incident_id": "...",
      "address": "...",
      "building_id": "...",
      "caller_photo_path": "...",
      "caller_text": "...",
      "ground_truth_node_id": "...",       # the correct position node in graph.json
      "ground_truth_room_id": "...",        # the room/suite that node belongs to
      "ground_truth_best_entry_id": "..."   # the correct responder staging door
    }
The harness runs run_incident on each, then reads the resulting incidents/<id>/ folder and scores.

Scoring:
  * Room accuracy  : top-1 match on room_id (predicted node -> room_label -> matches truth)
  * Meters saved   : (shortest graph path from front_door -> predicted_room_centroid_node)
                     minus (shortest from best_entry_under_belief -> that node).
                     Counted ONLY on incidents where room accuracy = correct.
  * Seconds to draft: elapsed_ms at commander/draft_ready (before approval gate) / 1000.
  * Secondaries    : questions asked (ask+update_belief entries), % incidents that skipped
                     entirely, # claims in audit.json that failed the source check.

    python3 agent/eval/run_eval.py --fixtures agent/eval/fixtures/synth_bayou
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(ROOT))
from pinpoint import orchestrator, routing
from pinpoint.config import FIXTURES, INCIDENTS

BLDG_DIR = FIXTURES / "building_bayou"


# ---------------- room lookup ----------------
def room_map():
    g = json.loads((BLDG_DIR / "graph.json").read_text())
    return {n["id"]: (n.get("room_label") or n["id"]) for n in g["nodes"]}


def front_door_node():
    """The civic-address door. By convention in site.json: ext_main."""
    s = json.loads((BLDG_DIR / "site.json").read_text())
    for e in s["entrances"]:
        if "front" in e["label"].lower() or e["entrance_id"] == "ext_main":
            return e["node_id"]
    return s["entrances"][0]["node_id"]


# ---------------- one incident ----------------
def run_one(incident_file):
    label = json.loads(Path(incident_file).read_text())
    intake = {
        "incident_id": label["incident_id"],
        "address": label["address"],
        "building_id": label["building_id"],
        "caller_photo_path": label["caller_photo_path"],
        "caller_text": label.get("caller_text", ""),
        "ground_truth_node_id": label["ground_truth_node_id"],
    }
    res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
    idir = Path(res["dir"])
    return label, res, idir


def score_one(label, idir, rm):
    """Return a dict of per-incident metrics."""
    loc = json.loads((idir / "location.json").read_text())
    summary = json.loads((idir / "summary.json").read_text())
    audit = json.loads((idir / "audit.json").read_text())
    route_bundle = json.loads((idir / "route.json").read_text())

    predicted_node = loc["node_id"]
    predicted_room = rm.get(predicted_node, predicted_node)
    truth_room = label["ground_truth_room_id"]
    room_correct = (predicted_room == truth_room)

    # meters saved: front-door -> predicted node vs best_entry_under_belief -> predicted node
    bld = label["building_id"]
    front = front_door_node()
    r_front = routing.route(bld, front, predicted_node, "foot")
    ub_door = route_bundle.get("under_belief", {}).get("entrance_id") or route_bundle["foot"]["entrance_id"]
    r_ours = routing.route(bld, ub_door, predicted_node, "foot")
    front_m = r_front.get("meters")
    ours_m = r_ours.get("meters")
    meters_saved = None
    if room_correct and front_m is not None and ours_m is not None:
        meters_saved = round(front_m - ours_m, 1)

    # seconds to draft from trace.jsonl: the commander/draft_ready milestone
    trace_path = idir / "trace.jsonl"
    draft_ms = None
    for line in trace_path.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("actor") == "commander" and rec.get("tool") == "draft_ready":
            draft_ms = rec.get("elapsed_ms")
            break
    seconds_to_draft = (draft_ms / 1000.0) if draft_ms is not None else summary.get("seconds_to_draft")

    # unsupported claims (should be 0)
    unsupported = len(audit.get("unsupported", []))

    return {
        "incident_id": label["incident_id"],
        "truth_room": truth_room,
        "predicted_room": predicted_room,
        "room_correct": room_correct,
        "meters_saved": meters_saved,
        "seconds_to_draft": seconds_to_draft,
        "questions_asked": loc.get("questions_asked", 0),
        "question_skipped": bool(loc.get("question_skipped")),
        "unsupported_claims": unsupported,
        "best_entry_ours": ub_door,
        "best_entry_truth": label.get("ground_truth_best_entry_id"),
    }


# ---------------- table rendering ----------------
def _median(vs):
    return round(statistics.median(vs), 2) if vs else None


def _p95(vs):
    if not vs:
        return None
    s = sorted(vs)
    k = max(0, int(round(0.95 * (len(s) - 1))))
    return round(s[k], 2)


def _pct(num, denom):
    return f"{(100.0 * num / denom):.1f}%" if denom else "—"


def render(rows):
    n = len(rows)
    correct = [r for r in rows if r["room_correct"]]
    ms_vals = [r["meters_saved"] for r in correct if r["meters_saved"] is not None]
    draft_vals = [r["seconds_to_draft"] for r in rows if r["seconds_to_draft"] is not None]
    acc = 100.0 * len(correct) / n if n else 0.0

    # Headline
    out = []
    out.append("")
    out.append("PINPOINT EVAL — headline")
    out.append("=" * 72)
    out.append(f"{'metric':<34} | {'median':>8} | {'p95':>8} | {'n':>4}")
    out.append("-" * 72)
    out.append(f"{'room accuracy (room top-1)':<34} | {acc:>7.1f}% | {'---':>8} | {n:>4}")
    out.append(f"{'meters saved vs front door':<34} | {str(_median(ms_vals)):>8} | {str(_p95(ms_vals)):>8} | {len(ms_vals):>4}")
    out.append(f"{'seconds to draft packet':<34} | {str(_median(draft_vals)):>8} | {str(_p95(draft_vals)):>8} | {len(draft_vals):>4}")
    out.append("")
    out.append("PINPOINT EVAL — secondaries")
    out.append("=" * 72)
    qa = [r["questions_asked"] for r in rows]
    skipped = sum(1 for r in rows if r["question_skipped"])
    unsup_total = sum(r["unsupported_claims"] for r in rows)
    out.append(f"{'questions asked per incident (mean)':<40} | {sum(qa)/n:>6.2f}")
    out.append(f"{'questions asked per incident (median)':<40} | {_median(qa):>6}")
    out.append(f"{'% incidents where question skipped':<40} | {_pct(skipped, n):>6}")
    out.append(f"{'unsupported claims total (should be 0)':<40} | {unsup_total:>6}")
    out.append("")
    # Best-entry correctness (not headline but useful): how often our entrance == truth entrance
    be_correct = sum(1 for r in rows if r["best_entry_ours"] == r["best_entry_truth"])
    out.append(f"{'best_entry matches truth':<40} | {be_correct}/{n} ({_pct(be_correct, n)})")
    out.append("")

    # Question-skip latency delta: compare draft seconds on skipped vs non-skipped
    sk_vals = [r["seconds_to_draft"] for r in rows if r["question_skipped"] and r["seconds_to_draft"] is not None]
    ask_vals = [r["seconds_to_draft"] for r in rows if not r["question_skipped"] and r["seconds_to_draft"] is not None]
    if sk_vals and ask_vals:
        out.append(f"skip-vs-ask latency: median {_median(sk_vals)}s (skipped, n={len(sk_vals)}) "
                   f"vs {_median(ask_vals)}s (asked, n={len(ask_vals)}); "
                   f"delta {round(_median(ask_vals) - _median(sk_vals), 3)}s")
    elif sk_vals:
        out.append(f"skip-vs-ask latency: all {len(sk_vals)} incidents skipped, median {_median(sk_vals)}s")
    else:
        out.append(f"skip-vs-ask latency: no incidents skipped; median {_median(ask_vals)}s")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True,
                    help="directory of labeled-incident .json files")
    ap.add_argument("--out", default=None,
                    help="optional path to write per-incident JSON scores")
    args = ap.parse_args()

    fixtures = sorted(Path(args.fixtures).glob("*.json"))
    assert fixtures, f"no .json files under {args.fixtures}"
    rm = room_map()
    rows = []
    for i, f in enumerate(fixtures, 1):
        label, res, idir = run_one(f)
        row = score_one(label, idir, rm)
        rows.append(row)
        print(f"  [{i:>3}/{len(fixtures)}] {label['incident_id']:<32} "
              f"room={'OK' if row['room_correct'] else 'MISS'} "
              f"qs={row['questions_asked']} "
              f"skip={'Y' if row['question_skipped'] else 'N'} "
              f"draft={row['seconds_to_draft']:.3f}s")

    print(render(rows))

    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2, default=str))
        print(f"per-incident scores -> {args.out}")


if __name__ == "__main__":
    main()
