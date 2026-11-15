"""Run one incident end to end on real data, or watch the console for new ones.

    # one incident from a photo, shown in the console, dispatcher approves there
    .venv/bin/python -m live.run --building 6550930 --photo demo/6550930_gbqgw9.jpg --console

    # pick up incidents the dispatcher starts in the console (a caller photo is required)
    .venv/bin/python -m live.run --watch

    # the caller's position is already known (or photo matching is not installed):
    # skip matching and run everything else
    python3 -m live.run --building 6550930 --position 9nydrb --auto-approve

What runs: the agent's orchestrator (agent/pinpoint/orchestrator.py) with its tools
replaced by lane A's records and routing and lane B's photo matching (live/tools.py).
The console (app/) shows the packet filling in, the route in the 3D scan, the question
to the caller, and takes the dispatcher's approval.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import tools                                     # also puts agent/ on the path
from .console_api import Console, ConsoleError, save_photo

from env import tools as A                              # noqa: E402
from env.graph import Graph, list_buildings             # noqa: E402
from pinpoint import orchestrator                       # noqa: E402
from pinpoint.config import INCIDENTS                   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "cache" / "live"


def _usable_photo(path: str | None) -> str | None:
    """A phone photo in a form every lane can read. iPhones save HEIC, which the matcher
    reads only on macOS: convert it to JPEG here when a converter is available."""
    if not path or not str(path).lower().endswith((".heic", ".heif")):
        return path
    out = STATE / "converted" / (Path(path).stem + ".jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pillow_heif
        from PIL import Image, ImageOps
        pillow_heif.register_heif_opener()
        ImageOps.exif_transpose(Image.open(path)).convert("RGB").save(out, "JPEG", quality=92)
        return str(out)
    except ImportError:
        pass
    import shutil
    import subprocess
    for tool in (["magick", str(path), str(out)], ["heif-convert", str(path), str(out)],
                 ["sips", "-s", "format", "jpeg", str(path), "--out", str(out)]):
        if shutil.which(tool[0]) and subprocess.run(tool, capture_output=True).returncode == 0 and out.exists():
            return str(out)
    raise SystemExit(f"{path} is a HEIC photo and nothing here can convert it. Export it as JPEG from the phone "
                     "(or `uv pip install --python .venv/bin/python pillow-heif`) and run again.")


def _node(building_id: str, prefix: str | None) -> str | None:
    """A full node id from a unique prefix."""
    if not prefix:
        return None
    g = Graph.load(building_id)
    prefix = prefix.rsplit("/", 1)[-1]
    hits = [n for n in g.nodes if n.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f"{prefix!r} matches {len(hits)} positions in building {building_id}")
    return hits[0]


def run_incident(building_id: str, photo: str | None, text: str = "", *, console: Console | None = None,
                 console_incident: str | None = None, truth: str | None = None, position: str | None = None,
                 simulate_match: str | None = None, ask: str | None = None, auto_approve: bool = False,
                 approve_timeout_s: float | None = None, ask_timeout_s: float = 90.0,
                 address: str | None = None, leave_for_review: bool = False) -> dict:
    """-> the agent's summary, plus `console_incident` and `console_decision` when a console
    is attached, and `problem` when the packet is not a full one (no match, no record).

    position        the caller's position is known: no matching, no questions
    simulate_match  stand in for the matcher with an uncertain match around this position
    truth           where the photo was really taken, for a simulated caller's answers
    leave_for_review  draft the packet in the console and stop there, without waiting: the
                    incident stays "awaiting review" for someone to approve or reject later"""
    building_id = str(building_id)
    if building_id not in list_buildings():
        raise SystemExit(f"no graph for building {building_id}; lane A has: {', '.join(list_buildings())}")
    prop = A.get_property(building_id)
    if not prop.get("building_id"):                      # a scanned building that is not in the CoStar kit
        prop = tools.no_costar_record(building_id)
    address = address or ", ".join(x for x in (prop.get("address"), prop.get("city"), prop.get("state")) if x)
    address = " ".join(w.capitalize() if w.isalpha() and w.islower() and len(w) > 2 else w for w in address.split(" "))
    truth, position = _node(building_id, truth), _node(building_id, position)
    simulate_match = _node(building_id, simulate_match)
    photo = _usable_photo(photo)

    if console is not None and not console.alive():
        raise SystemExit(f"the console is not answering at {console.base}: start it with `python3 -m live.serve`")
    if console is not None and console_incident is None:
        said = text or ("Caller sent a photo." if photo else "Position given by the dispatcher; no photo.")
        console_incident = console.create(building_id, said, photo)["id"]
    if ask is None:
        ask = "console" if (console is not None and not truth and not auto_approve) else "simulate"

    tools.install()
    tools.reset(building_id=building_id, truth_node=truth or position or simulate_match,
                given_position=position, simulate_match=simulate_match,
                console=console, console_incident=console_incident, ask=ask, ask_timeout_s=ask_timeout_s,
                console_scan=bool(console and console.has_scan(building_id)))

    decision_box = {}

    def approve(incident_id, packet_path):
        """Gate 1 is the packet. Any later gate (contacting the building) is always declined:
        this run never contacts anyone."""
        if decision_box:
            return "reject"
        if leave_for_review and console is not None:
            decision_box["decision"] = "left for review"
            return "reject"                  # nothing is sent; the console still shows it awaiting review
        if console is None or auto_approve:
            decision_box["decision"] = "auto"
            tools.SESSION["auto_approved"] = True
            if console is not None:
                try:                     # the console files every approval under "Dispatcher": say what happened
                    notes = console.get(console_incident).get("notes") or ""
                    console.update(console_incident, updatedBy="agent",
                                   notes=("AUTO-APPROVED by the run (--auto-approve): no dispatcher reviewed "
                                          "this packet. " + notes)[:10000])
                    console.decide(console_incident, "approved")
                except ConsoleError:
                    pass
            return "approve"
        print(f"  waiting for the dispatcher to approve or reject in the console: "
              f"{console.base}/live.html#incident/{console_incident}", flush=True)
        d = console.wait_for_decision(console_incident, timeout_s=approve_timeout_s)
        decision_box.update(d)
        return "approve" if d["decision"] == "approved" else "reject"

    intake = {"address": address, "building_id": building_id, "caller_photo_path": photo or "(position given)",
              "caller_text": text, "ground_truth_node_id": truth or position or simulate_match,
              "console_incident": console_incident}
    t0 = time.time()
    out = orchestrator.run_incident(intake, mock_llm=True, auto_approve=False, approve_fn=approve)
    out.update(console_incident=console_incident, console_decision=decision_box or None,
               wall_seconds=round(time.time() - t0, 2))
    audit = Path(out["dir"]) / "audit.json"
    status = json.loads(audit.read_text()).get("status") if audit.exists() else out.get("status")
    if status and status != "OK":
        out["problem"] = f"{status}: " + (tools.SESSION.get("match_error") or "see packet.md")
        print(f"  NOT A FULL PACKET. {out['problem']}", file=sys.stderr, flush=True)
    return out


def run_llm_incident(building_id: str, photo: str | None, text: str = "", *, console: Console | None = None,
                     truth: str | None = None, position: str | None = None, auto_approve: bool = False) -> dict:
    """The same incident on the agent's LLM path (agent/pinpoint/deep_agent.py): a deepagents
    Commander with five subagents calling the real tools. Needs the packages in
    agent/requirements.txt and an Anthropic API key (ANTHROPIC_API_KEY, or an
    `anthropic_api_key:` line in api.txt). Approval is asked in the terminal.

    Not exercised end to end by the team yet: the agent is known to construct with the real
    tools (see live/tests), but no run with a real key has been made on real data."""
    building_id = str(building_id)
    prop = A.get_property(building_id)
    if not prop.get("building_id") or building_id not in list_buildings():
        raise SystemExit(f"no record or no graph for building {building_id}")
    truth, position = _node(building_id, truth), _node(building_id, position)
    tools.install()
    tools.reset(building_id=building_id, truth_node=truth or position, given_position=position,
                console=console, console_incident=None, ask="simulate" if (truth or position) else "none")
    from pinpoint import deep_agent
    address = ", ".join(x for x in (prop.get("address"), prop.get("city"), prop.get("state")) if x)
    intake = {"address": address, "building_id": building_id, "caller_photo_path": photo or "(position given)",
              "caller_text": text, "incident_id": time.strftime("inc-%Y%m%d-%H%M%S-llm")}
    idir = INCIDENTS / intake["incident_id"]
    try:
        out = deep_agent.run_real_agent(intake, idir, auto_approve=auto_approve)
    except deep_agent.DeepAgentsNotConfigured as exc:
        raise SystemExit(f"the agent's LLM path cannot run here: {exc}")
    packet = idir / "packet.md"
    if console is not None and packet.exists():
        cid = console.create(building_id, text or "Caller sent a photo.", photo)["id"]
        console.update(cid, updatedBy="agent", packetMarkdown=packet.read_text()[:102400],
                       notes="Written by the agent's LLM path (deepagents). Its lines were not checked by live/packet.py.")
        out["console_incident"] = cid
    return out


# ------------------------------------------------------------------ watch mode
def _seen() -> dict:
    try:
        return json.loads((STATE / "seen.json").read_text())
    except (OSError, ValueError):
        return {}


def _mark(seen: dict, key: str, value) -> None:
    seen[key] = value
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "seen.json").write_text(json.dumps(seen, indent=1))


def watch(console: Console, poll_s: float = 1.5, once: bool = False, **kw) -> None:
    """Run the agent on each console incident that has a caller photo and a building lane
    A has a graph for. An incident is run once per photo."""
    have = set(list_buildings())
    seen = _seen()
    print(f"watching {console.base} for incidents with a caller photo; buildings with a graph: "
          f"{', '.join(sorted(have - {'toy'}))}", flush=True)
    while True:
        try:
            rows = console.incidents()
        except (ConsoleError, OSError) as exc:
            print(f"  console not reachable ({exc}); retrying", flush=True)
            rows = []
        for row in rows:
            if row.get("propertyId") not in have or not row.get("photo"):
                continue
            inc = console.get(row["id"])
            photo_key = (inc.get("photo") or {}).get("name", "") + str(len((inc.get("photo") or {}).get("dataUrl", "")))
            if seen.get(inc["id"]) == photo_key:
                continue
            first_sight = inc["id"] not in seen
            _mark(seen, inc["id"], photo_key)
            if first_sight and (inc.get("packetMarkdown") or inc.get("status") != "review"):
                continue                # already handled by an earlier run; a new photo will rerun it
            path = save_photo(inc, STATE / inc["id"])
            if not path:
                continue
            print(f"\nincident {inc['id']} at building {inc['propertyId']}: running the agent", flush=True)
            try:
                res = run_incident(inc["propertyId"], path, inc.get("callerText") or "", console=console,
                                   console_incident=inc["id"], **kw)
                print("  " + json.dumps({k: res.get(k) for k in ("incident_id", "decision", "seconds_to_draft",
                                                                 "console_decision", "problem")}), flush=True)
            except SystemExit as exc:
                print(f"  skipped: {exc}", flush=True)
            except Exception as exc:                        # keep watching whatever one incident does
                print(f"  failed: {type(exc).__name__}: {exc}", flush=True)
        if once:
            return
        time.sleep(poll_s)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--building", help="CoStar PropertyId of a building lane A has a graph for")
    ap.add_argument("--photo", help="the caller's photo")
    ap.add_argument("--text", default="", help="what the caller said")
    ap.add_argument("--console", nargs="?", const="http://localhost:4175", metavar="URL",
                    help="show the incident in the console (default URL http://localhost:4175)")
    ap.add_argument("--watch", action="store_true", help="run on incidents started in the console")
    ap.add_argument("--once", action="store_true", help="with --watch: handle what is waiting now, then stop")
    ap.add_argument("--truth", metavar="NODE", help="where the photo was really taken: the simulated caller "
                    "answers from it, and the result says whether the match was right")
    ap.add_argument("--position", metavar="NODE", help="the caller's position is known: skip photo matching")
    ap.add_argument("--simulate-match", metavar="NODE", help="stand in for the matcher with an uncertain match "
                    "around this position, to exercise the agent's questions without the matcher installed")
    ap.add_argument("--ask", choices=("console", "simulate", "none"), help="who answers the agent's questions")
    ap.add_argument("--ask-timeout", type=float, default=90.0, help="seconds to wait for the caller's reply")
    ap.add_argument("--auto-approve", action="store_true", help="do not wait for the dispatcher")
    ap.add_argument("--llm", action="store_true", help="run the agent's LLM path (deepagents; needs an Anthropic "
                    "API key) instead of its deterministic orchestrator")
    a = ap.parse_args(argv)

    console = Console(a.console or "http://localhost:4175") if (a.console or a.watch) else None
    if a.watch:
        watch(console, once=a.once, ask=a.ask, auto_approve=a.auto_approve, ask_timeout_s=a.ask_timeout)
        return
    if not a.building or not (a.photo or a.position or a.simulate_match):
        ap.error("give --building and --photo (or --position), or --watch")
    if a.llm:
        print(json.dumps(run_llm_incident(a.building, a.photo, a.text, console=console, truth=a.truth,
                                          position=a.position, auto_approve=a.auto_approve), indent=1))
        return
    res = run_incident(a.building, a.photo, a.text, console=console, truth=a.truth, position=a.position,
                       simulate_match=a.simulate_match, ask=a.ask, auto_approve=a.auto_approve,
                       ask_timeout_s=a.ask_timeout)
    idir = Path(res["dir"])
    print(json.dumps(res, indent=1))
    loc = json.loads((idir / "location.json").read_text()) if (idir / "location.json").exists() else {}
    if a.truth and loc.get("node_id"):
        truth = _node(a.building, a.truth)
        g = Graph.load(a.building)
        hop = truth == loc["node_id"] or g.edge(truth, loc["node_id"]) is not None
        print(f"located at {loc['node_id'][:6]}; truth {truth[:6]}: "
              + ("exact" if truth == loc["node_id"] else "adjacent position" if hop else "WRONG"))
    print(f"packet: {idir / 'packet.md'}")
    if console is not None:
        print(f"console: {console.base}/live.html#incident/{res['console_incident']}")
    if res.get("problem"):
        sys.exit(2)


if __name__ == "__main__":
    main()
