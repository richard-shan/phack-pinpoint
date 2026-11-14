#!/usr/bin/env python3
"""Eval skeleton — one command prints the metrics table from plan section 9. Reads every
agent/incidents/<id>/ (summary.json + location.json + route.json + trace.jsonl). Values that need
B's held-out photo set or hand-traced routes are marked TODO with WHO unblocks them.

    python agent/eval/run_eval.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCIDENTS = ROOT / "incidents"


def load_incidents():
    out = []
    if not INCIDENTS.exists():
        return out
    for d in sorted(INCIDENTS.iterdir()):
        if not d.is_dir():
            continue
        rec = {"id": d.name}
        for f in ("summary.json", "location.json", "route.json"):
            p = d / f
            if p.exists():
                rec[f.split(".")[0]] = json.loads(p.read_text())
        out.append(rec)
    return out


def main():
    incs = load_incidents()
    n = len(incs)
    print(f"\nPINPOINT EVAL — plan section 9   (n={n} stub incident(s))\n" + "=" * 70)

    rows = [
        ("Room accuracy (top-1 / top-5)", "TODO — needs Person B held-out degraded photos", "embed-only, random"),
        ("Median error (meters / hops)", "TODO — needs ground-truth positions", "—"),
        ("Accuracy after questions", _acc_after_questions(incs), "no questions, random questions"),
        ("Questions needed (mean)", _mean_questions(incs), "random question order"),
        ("Seconds: photo -> packet", _mean(incs, "summary", "seconds_to_draft"), "none"),
        ("Seconds saved by best door", _mean_route(incs, "seconds_saved_vs_front"), "front door"),
        ("Fastest vs shortest differs", "TODO — needs shortest-by-meters comparison", "shortest path"),
        ("Route correctness", "TODO — hand-trace 10 targets in viewer", "none"),
        ("Replan time (ms)", "TODO — wire a 'door locked' event", "none"),
        ("Cost of uncertainty (exp. s)", _cost_of_uncertainty(incs), "route to top guess"),
        ("Stretcher route differs", _stretcher_differs(incs), "none"),
        ("Packet claim precision", _claim_precision(incs), "verifier OFF"),
    ]
    w = max(len(r[0]) for r in rows)
    print(f"{'Metric'.ljust(w)} | {'Value'.ljust(42)} | Baseline")
    print("-" * (w + 3) + "+" + "-" * 44 + "+" + "-" * 20)
    for name, val, base in rows:
        print(f"{name.ljust(w)} | {str(val).ljust(42)} | {base}")
    print("\nState the sample size next to every number (plan 9). n is small by design.\n")


def _mean(incs, section, key):
    vals = [i[section][key] for i in incs if section in i and key in i[section]]
    return f"{sum(vals)/len(vals):.2f}" if vals else "—"


def _mean_questions(incs):
    vals = [i["summary"]["questions_asked"] for i in incs if "summary" in i]
    return f"{sum(vals)/len(vals):.2f}" if vals else "—"


def _mean_route(incs, key):
    vals = [i["route"][key] for i in incs if "route" in i and i["route"].get(key) is not None]
    return f"{sum(vals)/len(vals):.1f}s" if vals else "—"


def _acc_after_questions(incs):
    hit = [1 for i in incs if "location" in i and not i["location"].get("below_threshold")]
    return f"{len(hit)}/{len(incs)} above threshold" if incs else "—"


def _cost_of_uncertainty(incs):
    vals = []
    for i in incs:
        ub = i.get("route", {}).get("under_belief", {})
        if ub.get("spread_seconds") is not None:
            vals.append(ub["spread_seconds"])
    return f"spread ~{sum(vals)/len(vals):.1f}s" if vals else "—"


def _stretcher_differs(incs):
    d = [1 for i in incs if i.get("route", {}).get("stretcher_differs")]
    return f"{len(d)}/{len(incs)}" if incs else "—"


def _claim_precision(incs):
    sup = tot = 0
    for i in incs:
        a = (INCIDENTS / i["id"] / "audit.json")
        if a.exists():
            au = json.loads(a.read_text())
            sup += au["supported"]; tot += au["checked"]
    return f"{sup}/{tot} claims sourced" if tot else "—"


if __name__ == "__main__":
    main()
