"""Fill the console with a set of ready-made incidents to click through.

    .venv/bin/python -m live.examples            # needs the console running (python -m live.serve)
    .venv/bin/python -m live.examples --clear    # only remove the examples made earlier

Each example is a stand-in caller photo from `demo/` run through the whole chain and
left "awaiting review" in the console, so the approve and reject buttons still work.
Several are in buildings with more than one level, where the route climbs stairs or
takes an elevator and the stretcher route differs from the route on foot.

A building without a photo index on this machine is run with the caller's position
given instead of matched, and its packet says so.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import run
from .console_api import Console, ConsoleError

ROOT = Path(__file__).resolve().parent.parent
MADE = ROOT / "cache" / "live" / "examples.json"

# (building, scan position the photo was taken at, what the caller says)
EXAMPLES = [
    ("6550930", "gbqgw9", "Someone fainted near the restrooms, I can see the shops down the hall."),
    ("6550930", "ugmz8k", "My mom fell by the elevators."),
    ("10352309", "mqpn20", "He is on the floor in the upstairs kitchen and not responding."),          # 2 floors, stairs only
    ("10352309", "nb4ti7", "Upstairs office. She is having trouble breathing."),
    ("princeton-rabinowitz", "ih6tue", "Someone collapsed upstairs by the glass railing."),         # stair on foot, elevator for a stretcher
    ("princeton-rabinowitz", "umf4ku", "First floor hallway, outside one of the offices."),
    ("259587", "0t15m4", "We are on the roof terrace on the third floor."),                          # 5 levels, elevator
    ("259587", "5stm18", "I am in the lower parking level, my husband has chest pain."),
    ("248351", "iddc5b", "Second floor of the film school, a student passed out."),                  # 2 floors
    ("5956257", "5pbgr8", "I am in the empty lobby by the taped-off elevator."),
]


def _photo(building: str, node: str) -> str | None:
    p = ROOT / "demo" / f"{building}_{node}.jpg"
    if not p.exists():
        try:
            from . import demo_photo
            demo_photo.make(building, node, out=str(p))
        except (SystemExit, Exception):
            return None
    return str(p)


def clear(console: Console) -> int:
    """Remove the example incidents this script made before (files in the console's local store)."""
    try:
        ids = json.loads(MADE.read_text())
    except (OSError, ValueError):
        return 0
    store = ROOT / "app" / ".local-state"
    gone = 0
    for cid in ids:
        f = store / f"{cid}.json"
        if f.exists() and cid.startswith("inc-"):
            f.unlink()
            gone += 1
    MADE.unlink(missing_ok=True)
    return gone


def make(console: Console) -> list[dict]:
    index = ROOT / "cache" / "index"
    out, ids = [], []
    for building, node, text in EXAMPLES:
        photo = _photo(building, node)
        matched = (index / building / "emb.npy").exists() and photo is not None
        try:
            res = run.run_incident(building, photo, text, console=console, truth=node,
                                   position=None if matched else node, leave_for_review=True)
        except SystemExit as exc:
            print(f"{building} {node}: skipped ({exc})", flush=True)
            continue
        ids.append(res["console_incident"])
        MADE.parent.mkdir(parents=True, exist_ok=True)
        MADE.write_text(json.dumps(ids))
        loc = {}
        try:
            loc = json.loads((Path(res["dir"]) / "location.json").read_text())
        except (OSError, ValueError):
            pass
        row = {"building": building, "taken_at": node[:6], "located_at": (loc.get("node_id") or "")[:6],
               "how": "matched from the photo" if matched else "position given (no photo index here)",
               "url": f"{console.base}/live.html#incident/{res['console_incident']}"}
        out.append(row)
        print(json.dumps(row), flush=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--console", default="http://localhost:4175")
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args()
    c = Console(a.console)
    if not c.alive():
        raise SystemExit(f"the console is not answering at {c.base}: start it with `python -m live.serve`")
    print(f"removed {clear(c)} earlier example(s)")
    if not a.clear:
        make(c)
