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


def _gate(console: Console | None, console_incident: str | None, auto_approve: bool,
          leave_for_review: bool = False, approve_timeout_s: float | None = None):
    """The dispatcher's decision on a packet, as the function the agent's approval hook
    calls. -> (approve, decision_box)"""
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

    return approve, decision_box


def _address(prop: dict) -> str:
    address = ", ".join(x for x in (prop.get("address"), prop.get("city"), prop.get("state")) if x)
    return " ".join(w.capitalize() if w.isalpha() and w.islower() and len(w) > 2 else w for w in address.split(" "))


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
        made = console.create(building_id, said, photo)
        console_incident = made["id"]
        _mark(_seen(), console_incident, _photo_key(made))     # a watcher must not run this one too
    if ask is None:
        ask = "console" if (console is not None and not truth and not auto_approve) else "simulate"

    tools.install()
    tools.reset(building_id=building_id, truth_node=truth or position or simulate_match,
                given_position=position, simulate_match=simulate_match,
                console=console, console_incident=console_incident, ask=ask, ask_timeout_s=ask_timeout_s,
                console_scan=bool(console and console.has_scan(building_id)))

    gate, decision_box = _gate(console, console_incident, auto_approve, leave_for_review, approve_timeout_s)

    def approve(incident_id, packet_path):
        decision = gate(incident_id, packet_path)
        if decision == "reject" and decision_box.get("decision") == "rejected":
            return {"decision": "reject", "corrected_room_id": decision_box.get("corrected_room_id")}
        return decision

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


MODEL_FILES = ("packet.md", "plan.md", "brief.json", "location.json", "candidates.json", "site.json",
               "route.json", "audit.json")


def _set_aside(idir: Path) -> dict:
    """Move what the model itself reported (written by the agent's LLM path) into
    <incident>/llm/ and return it. The incident folder then holds only what the tools
    produced. Safe to call again: it returns what is already set aside."""
    kept = idir / "llm"
    kept.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        if (idir / name).exists():
            (idir / name).replace(kept / name)
    said = {}
    for name in ("location", "route", "audit"):
        try:
            v = json.loads((kept / f"{name}.json").read_text())
        except (OSError, ValueError):
            v = None
        said[name] = v if isinstance(v, dict) else {}
    return said


def _packet_from_tools(idir: Path, intake: dict, building_id: str, model: str, said: dict) -> dict:
    """The packet for a run with a model in charge, written from what the tools returned.

    The model decides where the caller is. Nothing it wrote goes into the packet: its
    position is used only if the photo match returned that position, lane A routes to
    it, and live/packet.py writes every line from records, the scan and the graph,
    checked by the agent's verifier as on the deterministic path. Where the model's
    report differs from the tools, the packet says so.

    -> the pieces of the packet, or {"problem": why there is none}"""
    from pinpoint import localization, pipeline
    from pinpoint.config import CONFIDENCE_THRESHOLD
    from pinpoint.util import write_json
    from .packet import LivePacketWriter

    g = Graph.load(building_id)
    cands = [dict(c) for c in tools.SESSION.get("candidates") or []]
    if not cands:
        return {"problem": tools.SESSION.get("match_error") or "the model never got a photo match from match_photo"}
    localization.normalize_belief(cands)
    asked, scored = tools.SESSION.get("asked") or [], False
    for q in asked:                              # the answers the tools can score, applied as the agent's loop would
        if q.get("raw") is not None and all(q["attribute"] in c["attributes"] for c in cands):
            cands, scored = localization.update_belief(cands, q["attribute"], q["raw"]), True
    ranked = sorted(cands, key=lambda c: c["belief"], reverse=True)
    by_id = {c["node_id"]: c for c in ranked}

    loc_said, route_said = said.get("location") or {}, said.get("route") or {}
    named = str(loc_said.get("node_id") or "").rsplit("/", 1)[-1]
    lines = []
    if named in by_id:
        chosen = named
        lines.append((f"Position chosen by the agent's model ({model})"
                      + (f" from {len(ranked)} photo matches" if len(ranked) > 1 else "")
                      + "; the door, route and directions are the routing code's.", "location.chosen_by"))
    else:
        chosen = ranked[0]["node_id"]
        what = ("no position" if not named else
                f"`{named[:6]}` ({g.label(named) or g.room(named) or 'unlabelled position'})" if named in g.nodes
                else f"\"{named[:40]}\"")
        lines.append((f"The agent's model ({model}) reported {what}, which the photo match did not return. "
                      "This packet uses the best photo match instead.", "location.chosen_by"))
    top = by_id[chosen]
    others = [c for c in ranked if c["node_id"] != chosen]
    put_to_caller = [q for q in asked if not q["source"].startswith("not asked") and q["source"] != "none"]
    location = {
        "node_id": chosen, "confidence": round(top["belief"], 3),
        "floor": top["attributes"].get("floor"), "room_type": top["attributes"].get("room_type"),
        "questions_asked": len(put_to_caller), "question_skipped": False,
        "skip_reason": None if put_to_caller else "the model did not put a question to the caller",
        "question_log": [{"q": q["question"], "answer": q["reply"], "source": q["source"]} for q in put_to_caller],
        "runner_up": ({"node_id": others[0]["node_id"], "confidence": round(others[0]["belief"], 3)}
                      if others else None),
        "evidence_path": top.get("evidence_path"),
        "below_threshold": top["belief"] < CONFIDENCE_THRESHOLD,
        "chosen_by": f"model:{model}" if named in by_id else "photo match (the model's report was not usable)",
        "model_report": {k: loc_said[k] for k in ("node_id", "room_label", "belief", "questions_asked")
                         if k in loc_said} or {"node_id": None},
    }
    belief = loc_said.get("belief")
    if (named in by_id and isinstance(belief, (int, float)) and not tools.SESSION.get("given_position")
            and abs(belief - top["belief"]) >= 0.1):
        lines.append((f"The model put its own confidence at {belief * 100:.0f}%; the figure above comes from the "
                      "match scores" + (" and the caller's answers" if scored else "") + ".", "location.model_report"))

    packet = LivePacketWriter(idir / "packet.md", intake)
    brief = tools.records(idir, intake)
    site = pipeline.run_site(idir, building_id, address=intake.get("address"))
    write_json(idir / "candidates.json", {"candidates": ranked})
    route = pipeline.run_router(idir, building_id, location, site)
    door, model_door = (route.get("foot") or {}).get("entrance_id"), route_said.get("best_entry_id")
    if model_door:
        same = model_door in (door, (route.get("stretcher") or {}).get("entrance_id"))
        route["model_report"] = {"best_entry_id": model_door, "agrees": same}
        label = g.label(model_door) if model_door in g.nodes else None
        lines.append(("The model's report names the same door." if same else
                      f"The model's report names a different door ({label or model_door}); the door and route "
                      "in this packet are the routing code's.", "route.model_report"))
        write_json(idir / "route.json", route)
    location["model_lines"] = lines
    write_json(idir / "location.json", location)
    packet.set_brief(brief)
    packet.set_site(site)
    packet.set_location(location)
    packet.set_route(route)
    audit = pipeline.run_verifier(idir, packet, brief, location, site, route, [])
    audit["status"] = "OK"
    packet.set_audit(audit)
    packet.flush()
    write_json(idir / "audit.json", audit)
    return {"packet": packet, "brief": brief, "site": site, "location": location, "candidates": ranked,
            "route": route, "audit": audit, "position": chosen, "door": door,
            "model_position": named or None, "model_door": model_door}


def _write_pieces(idir: Path, built: dict) -> None:
    from pinpoint.util import write_json
    for name in ("brief", "site", "location", "route", "audit"):
        write_json(idir / f"{name}.json", built[name])
    write_json(idir / "candidates.json", {"candidates": built["candidates"]})
    built["packet"].flush()


def run_llm_incident(building_id: str, photo: str | None, text: str = "", *, console: Console | None = None,
                     console_incident: str | None = None, truth: str | None = None, position: str | None = None,
                     ask: str | None = None, auto_approve: bool = False, approve_timeout_s: float | None = None,
                     ask_timeout_s: float = 90.0, **_ignored) -> dict:
    """The same incident with a model in charge (agent/pinpoint/deep_agent.py): a deepagents
    Commander and five subagents decide which tools to call, on the same real tools. Needs
    the packages in agent/requirements.txt and an Anthropic API key (ANTHROPIC_API_KEY in
    the environment). Lane C caps its spend (agent/pinpoint/budget.py).

    The model decides where the caller is and when the packet is ready. It does not write
    the packet: when it asks to send, `_packet_from_tools` writes the packet from the
    tools' results, and that is what the dispatcher approves or rejects, in the console
    when one is attached. What the model itself reported is kept in <incident>/llm/."""
    building_id = str(building_id)
    if building_id not in list_buildings():
        raise SystemExit(f"no graph for building {building_id}; lane A has: {', '.join(list_buildings())}")
    prop = A.get_property(building_id)
    if not prop.get("building_id"):
        prop = tools.no_costar_record(building_id)
    address = _address(prop)
    truth, position = _node(building_id, truth), _node(building_id, position)
    photo = _usable_photo(photo)
    from pinpoint import agent_spec, budget, deep_agent
    from pinpoint.util import write_json
    tools.install()
    try:                                     # find out now, before an incident appears in the console
        deep_agent._ensure_api_key()
        deep_agent.build_agent()
    except deep_agent.DeepAgentsNotConfigured as exc:
        raise SystemExit(f"the agent's LLM path cannot run here: {exc}")
    if budget.check_budget()[1]:
        raise SystemExit(f"the agent's LLM path is over its spending cap of ${budget.BUDGET_USD:.2f} "
                         "(agent/.pinpoint_cost.json; PINPOINT_BUDGET_USD raises it)")
    model = agent_spec.COMMANDER_MODEL.split(":")[-1]

    if console is not None and not console.alive():
        raise SystemExit(f"the console is not answering at {console.base}: start it with `python3 -m live.serve`")
    if console is not None and console_incident is None:
        said = text or ("Caller sent a photo." if photo else "Position given by the dispatcher; no photo.")
        made = console.create(building_id, said, photo)
        console_incident = made["id"]
        _mark(_seen(), console_incident, _photo_key(made))     # a watcher must not run this one too
    if ask is None:
        ask = "console" if (console is not None and not truth and not auto_approve) else "simulate"
    tools.reset(building_id=building_id, truth_node=truth or position, given_position=position,
                console=console, console_incident=console_incident, ask=ask, ask_timeout_s=ask_timeout_s,
                console_scan=bool(console and console.has_scan(building_id)), llm=True,
                drafted_by=f"the Pinpoint agent with a model in charge ({model})")
    if console is not None:
        try:
            console.update(console_incident, updatedBy="agent",
                           notes=f"The Pinpoint agent's model ({model}) is working on this incident: it decides "
                                 "which tools to call. A packet takes one to four minutes this way.")
        except ConsoleError:
            pass

    incident_id = time.strftime("inc-%Y%m%d-%H%M%S-llm")
    idir = INCIDENTS / incident_id
    intake = {"address": address, "building_id": building_id, "caller_photo_path": photo or "(position given)",
              "caller_text": text, "ground_truth_node_id": truth or position, "console_incident": console_incident,
              "incident_id": incident_id}
    # The model's kickoff message carries only the address, the photo and what the caller said.
    # Its tools need the building id, so that rides along with the address.
    kick = dict(intake, address=f"{address} (building_id {building_id})",
                caller_photo_path=photo or "(none: the dispatcher gave the position; call match_photo once to get it)")
    gate, decision_box = _gate(console, console_incident, auto_approve, approve_timeout_s=approve_timeout_s)
    built: dict = {}

    def build() -> None:
        tools.SESSION["llm"] = False             # the agent's own steps expect tools that raise
        try:
            built.update(_packet_from_tools(idir, intake, building_id, model, _set_aside(idir)))
        except Exception as exc:                 # reported below; the model's run goes on
            built["problem"] = f"the packet could not be written from the tools ({type(exc).__name__}: {exc})"
        finally:
            tools.SESSION["llm"] = True

    def decide() -> str:
        if "packet" not in built:
            return "reject"                      # nothing to approve
        built["decision"] = gate(incident_id, built["packet"].path)
        if built["decision"] == "approve":
            built["packet"].mark_sent()
            built["packet"].flush()
        return built["decision"]

    def at_gate(_incident_id, actions):
        """The model asks to send the packet, or to contact the building (always declined)."""
        if "send_packet" not in actions or "decision" in built or "problem" in built:
            return "reject"
        build()
        return decide()

    t0 = time.time()
    out: dict = {}
    try:
        out = deep_agent.run_real_agent(kick, idir, approve_fn=at_gate)
    except (deep_agent.DeepAgentsNotConfigured, budget.BudgetExceeded) as exc:
        raise SystemExit(f"the agent's LLM path cannot run here: {exc}")
    except Exception as exc:                     # an API failure part way: keep what the tools returned
        out = {"model_error": f"{type(exc).__name__}: {exc}"[:300]}
        print(f"  the model's run stopped early: {out['model_error']}", file=sys.stderr, flush=True)
    finally:
        tools.SESSION["llm"] = False
    _set_aside(idir)                             # the run ends by writing the model's reports again
    if "packet" not in built and "problem" not in built:
        build()                                  # the model stopped without asking to send
        tools.SESSION["llm"] = False
        built["model_never_asked_to_send"] = True
        decide()
    elif "packet" in built:
        _write_pieces(idir, built)

    audit = built.get("audit") or {}
    res = {"incident_id": incident_id, "dir": str(idir), "decision": built.get("decision"), "path": "llm",
           "model": model, "position": built.get("position"), "model_position": built.get("model_position"),
           "door": built.get("door"), "model_door": built.get("model_door"),
           "lines_sourced": f"{audit.get('supported')}/{audit.get('checked')}" if audit else None,
           "questions_asked": (built.get("location") or {}).get("questions_asked", 0),
           "model_never_asked_to_send": built.get("model_never_asked_to_send", False),
           "console_incident": console_incident, "console_decision": decision_box or None,
           "wall_seconds": round(time.time() - t0, 2),
           **{k: out[k] for k in ("interrupts", "input_tokens", "output_tokens", "cost_usd", "cumulative_usd",
                                  "model_error") if k in out}}
    if "packet" not in built:
        res["problem"] = built.get("problem") or "no packet"
        print(f"  NOT A FULL PACKET. {res['problem']}", file=sys.stderr, flush=True)
        if console is not None:
            try:
                console.update(console_incident, updatedBy="agent",
                               notes=f"The model-driven run produced no packet: {res['problem']}"[:10000])
            except ConsoleError:
                pass
    write_json(idir / "summary.json", res)
    return res


# ------------------------------------------------------------------ watch mode
def _photo_key(incident: dict) -> str:
    photo = incident.get("photo") or {}
    return photo.get("name", "") + str(len(photo.get("dataUrl", "")))


def _seen() -> dict:
    try:
        return json.loads((STATE / "seen.json").read_text())
    except (OSError, ValueError):
        return {}


def _mark(seen: dict, key: str, value) -> None:
    seen[key] = value
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "seen.json").write_text(json.dumps(seen, indent=1))


def watch(console: Console, poll_s: float = 1.5, once: bool = False, llm: bool = False, **kw) -> None:
    """Run the agent on each console incident that has a caller photo and a building lane
    A has a graph for. An incident is run once per photo. `llm` puts a model in charge."""
    run = run_llm_incident if llm else run_incident
    have = set(list_buildings())
    print(f"watching {console.base} for incidents with a caller photo; buildings with a graph: "
          f"{', '.join(sorted(have - {'toy'}))}", flush=True)
    while True:
        seen = _seen()                       # other runs on this machine record what they are handling
        try:
            rows = console.incidents()
        except (ConsoleError, OSError) as exc:
            print(f"  console not reachable ({exc}); retrying", flush=True)
            rows = []
        for row in rows:
            if row.get("propertyId") not in have or not row.get("photo"):
                continue
            inc = console.get(row["id"])
            photo_key = _photo_key(inc)
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
                res = run(inc["propertyId"], path, inc.get("callerText") or "", console=console,
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
    ap.add_argument("--llm", action="store_true", help="put a model in charge of the tool calls (the agent's "
                    "deepagents path; needs ANTHROPIC_API_KEY) instead of its deterministic orchestrator")
    a = ap.parse_args(argv)

    console = Console(a.console or "http://localhost:4175") if (a.console or a.watch) else None
    if a.watch:
        watch(console, once=a.once, llm=a.llm, ask=a.ask, auto_approve=a.auto_approve, ask_timeout_s=a.ask_timeout)
        return
    if not a.building or not (a.photo or a.position or a.simulate_match):
        ap.error("give --building and --photo (or --position), or --watch")
    if a.llm:
        if a.simulate_match:
            ap.error("--simulate-match is for the deterministic path")
        res = run_llm_incident(a.building, a.photo, a.text, console=console, truth=a.truth, position=a.position,
                               ask=a.ask, auto_approve=a.auto_approve, ask_timeout_s=a.ask_timeout)
    else:
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
