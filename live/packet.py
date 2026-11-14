"""The responder packet on real data, and its copy in the console.

Replaces the agent's `PacketWriter` (which renders one made-up building) while keeping
what the rest of the agent depends on: `packet.md` is rewritten on every `flush()`, and
every factual line carries a `[src:...]` tag that the agent's Verifier resolves against
the incident's files. A line whose tag does not resolve is listed under "Not confirmed".

When a console is attached (`tools.SESSION["console"]`), each flush also updates the
console incident, so the dispatcher watches the packet fill in.
"""
from __future__ import annotations

import hashlib
import re

from env import console as console_export
from env.graph import Graph
from pinpoint import trace
from pinpoint.packet import PENDING, PacketWriter

from . import tools

HAZARD_TEXT = {
    "unconfirmed_exterior_foot": "The walk from the staging spot to the door is assumed from the aerial image, not seen",
    "unconfirmed_door": "Door marked from the scan; nobody has confirmed where it leads",
    "locked_door": "Locked door on the route",
    "unconfirmed_locked_door": "Door expected to be locked",
    "stairs": "Stairs on the route",
}


def _secs(v) -> str:
    return "?" if v is None else f"{round(v)} s"


def _short(node_id: str) -> str:
    return str(node_id)[:6]


class LivePacketWriter(PacketWriter):
    def __init__(self, path, intake):
        super().__init__(path, intake)
        self._pushed = None
        self._route_posted = None

    # ------------------------------------------------------------ lookups
    def _g(self) -> Graph | None:
        try:
            return Graph.load(str(self.intake.get("building_id")))
        except Exception:
            return None

    def _label(self, node_id) -> str:
        g = self._g()
        if not g or node_id not in g.nodes:
            return str(node_id)
        return g.label(node_id) or g.room(node_id) or f"scan position {_short(node_id)}"

    def _place(self, node_id) -> str:
        """Where a scan position is, in words a crew can use."""
        g = self._g()
        if not g or node_id not in g.nodes:
            return str(node_id)
        n = g.nodes[node_id]
        parts = [n.get("floor_name") or f"Floor {(n.get('floor') or 0) + 1}"]
        if g.room(node_id):
            parts.append(g.room(node_id))
        if g.label(node_id):
            parts.append("at " + g.label(node_id))
        return ", ".join(parts)

    def _front_id(self):
        base = (self.route or {}).get("front_door_baseline") or {}
        return base.get("entrance_id")

    # ------------------------------------------------------------ render
    def render(self) -> str:
        L = []
        if self.status == "UNABLE":
            status = "UNABLE: dispatcher action required"
        elif self.status == "DEGRADED":
            status = "DEGRADED: partial information, see below"
        elif self.sent:
            status = ("APPROVED automatically for this run (no dispatcher reviewed it)"
                      if tools.SESSION.get("auto_approved") else "APPROVED by the dispatcher")
        else:
            status = "DRAFT: awaiting dispatcher approval"
        addr = (self.intake or {}).get("address") or "(no address supplied)"
        L += [f"# Responder packet: {addr}", "", f"**Status:** {status}", ""]
        if self.failure_sentence:
            L += ["## Status detail"]
            tag = f" [src:{self.failure_cite_tag}]" if self.failure_cite_tag else ""
            L += [f"- {self.failure_sentence}{tag}"]
            for k, v in sorted(self.attempted_fields.items()):
                L += [f"- Attempted {k}: `{v if v not in (None, '') else '(missing)'}` [src:intake.{k}]"]
            L += [""]
        if self.status == "UNABLE":
            return "\n".join(L)

        g = self._g()
        lo, rt, site, b = self.location, self.route, self.site, self.brief
        located = bool(lo and lo.get("node_id"))

        # ---- caller location
        L += ["## Caller location"]
        if located:
            L += [f"- {self._place(lo['node_id'])} (scan position `{_short(lo['node_id'])}`), "
                  f"confidence {lo['confidence'] * 100:.0f}% [src:location.node_id]"]
            if lo.get("below_threshold"):
                L += [f"- Below the confidence threshold: treat as \"{lo.get('floor')}\" only until the caller "
                      "confirms. [src:location.below_threshold]"]
            ru = lo.get("runner_up")
            if ru:
                L += [f"- Next most likely: {self._place(ru['node_id'])} (`{_short(ru['node_id'])}`), "
                      f"{ru['confidence'] * 100:.0f}% [src:location.runner_up]"]
            asked = tools.SESSION.get("asked") or []
            if lo.get("questions_asked"):
                for q in asked:
                    L += [f"- Asked: \"{q['question']}\" Answer: \"{q['reply']}\" ({q['source']}) "
                          "[src:location.question_log]"]
            elif lo.get("skip_reason"):
                L += [f"- No question asked: {lo['skip_reason']} [src:location.skip_reason]"]
            if tools.SESSION.get("simulate_match"):
                L += ["- The position was given to this run, not matched from the photo."]
            elif lo.get("evidence_path"):
                L += [f"- Photo match evidence: `{lo['evidence_path']}` [src:location.evidence_path]"]
        elif lo is None and not self.failure_sentence:
            L += [PENDING]
        else:
            L += ["- Not located."]
        L += [""]

        if self.correction_notes:
            L += ["## Dispatcher notes for this building"] + list(self.correction_notes) + [""]

        foot = (rt or {}).get("foot") or {}
        stretcher = (rt or {}).get("stretcher") or {}
        door = foot.get("entrance_id")
        if door == "ext_main" and g and "ext_main" not in g.nodes:      # the agent's fallback name
            front = tools.A.front_door(str(self.intake.get("building_id"))) or {}
            door = front.get("entrance_id") or front.get("node_id")

        # ---- stage + enter
        L += ["## Stage here"]
        if rt is None:
            L += [PENDING]
        elif foot.get("staging_id"):
            sid = foot["staging_id"]
            L += [f"- {self._label(sid).capitalize()} [src:route.foot.staging_id]"]
            if g and not g.nodes[sid].get("confirmed"):
                L += ["- Placed by eye on the aerial image; not confirmed on site."]
            if site and site.get("tile_path"):
                L += [f"- Aerial reference: `{site['tile_path']}` [src:site.tile_path]"]
        else:
            L += ["- No staging spot is marked for this door."]
        L += ["", "## Enter here"]
        if rt is None:
            L += [PENDING]
        elif door:
            tag = " [src:route.foot.entrance_id]" if foot.get("path") else ""
            L += [f"- {self._label(door).capitalize()}{tag}"]
            saved, front_id = rt.get("seconds_saved_vs_front"), self._front_id()
            if front_id and front_id != door and saved and saved > 0:
                L += [f"- This is not the street-address door. The front door ({self._label(front_id)}) would "
                      f"take about {round(saved)} s longer. [src:route.seconds_saved_vs_front]"]
            elif front_id and front_id == door:
                L += ["- This is the street-address door. [src:route.front_door_baseline]"]
            ru = foot.get("runner_up")
            if ru:
                gap = round(ru["delta_seconds"])
                L += [f"- Next best door: {ru.get('label') or ru['entrance_id']}, "
                      + (f"about {gap} s slower." if gap >= 1 else "about the same time.")
                      + " [src:route.foot.runner_up]"]
            node = g.nodes.get(door, {}) if g else {}
            ap = node.get("approach") or {}
            if ap.get("locked_entry"):
                L += ["- Expect this door to be locked from outside."]
            if ap.get("via"):
                L += [f"- Reached by {ap['via']} through parts of the building that are not scanned"
                      + (f" ({ap['floors']} floors up)." if ap.get("floors") else ".")]
            if node and not node.get("confirmed", True):
                L += ["- Marked from the scan; nobody has confirmed where this door leads."]
        else:
            L += ["- No usable entrance found."]
        L += [""]

        # ---- route on foot
        L += ["## Route: crew on foot"]
        if rt is None:
            L += [PENDING]
        elif rt.get("foot_directions"):
            for i, step in enumerate(rt["foot_directions"], 1):
                L += [f"{i}. {step['text']} [src:route.foot.path]"]
            total = f"- About {_secs(foot.get('seconds'))}"
            if foot.get("outside_seconds") is not None:
                total += f" ({_secs(foot.get('outside_seconds'))} outside, {_secs(foot.get('inside_seconds'))} inside)"
            L += [total + f", {round(foot.get('meters') or 0)} m [src:route.foot.seconds]"]
        else:
            L += ["- No route: the caller's position is not known."]
        L += [""]

        # ---- stretcher
        if rt is not None and located:
            L += ["## Stretcher"]
            if stretcher.get("reachable") is False or not stretcher.get("entrance_id"):
                c = stretcher.get("carry") or {}
                if c:
                    L += [f"- A stretcher cannot reach the caller ({c.get('blocked_by') or 'no step-free way'}). "
                          f"Take it in through {self._label(c['entrance_id'])} to {self._place(c['node_id'])} "
                          f"(about {_secs(c['seconds_to_carry_point'])}), then carry: about "
                          f"{_secs(c['carry_seconds_on_foot'])} on foot from there. [src:route.stretcher.carry]"]
                else:
                    L += ["- A stretcher cannot reach the caller, and no point close to them was found."]
            elif rt.get("stretcher_differs"):
                if stretcher.get("entrance_id") != foot.get("entrance_id"):
                    L += [f"- Use a different door: {self._label(stretcher['entrance_id'])}. "
                          "[src:route.stretcher.entrance_id]"]
                for i, step in enumerate(rt.get("stretcher_directions") or [], 1):
                    L += [f"{i}. {step['text']} [src:route.stretcher.path]"]
                L += [f"- About {_secs(stretcher.get('seconds'))} [src:route.stretcher.seconds]"]
            else:
                L += [f"- Same door and route, about {_secs(stretcher.get('seconds'))}. "
                      "[src:route.stretcher.seconds]"]
            L += [""]

        # ---- on the way + equipment
        L += ["## On the way"]
        if rt is None:
            L += [PENDING]
        else:
            hz = rt.get("hazards") or []
            for h in hz:
                text = HAZARD_TEXT.get(h["type"], h["type"].replace("_", " "))
                L += [f"- {text}: {self._label(h['node_id'])} [src:route.hazards]"]
            if not hz:
                L += ["- Nothing flagged on this route."]
            eq = rt.get("equipment") or []
            for e in eq[:3]:
                dist = f", {round(e['meters'])} m from the caller" if e.get("meters") is not None else ""
                L += [f"- AED: {e.get('room_label')}{dist} [src:route.equipment]"]
            if not eq:
                L += ["- No AED is marked in this building's graph."]
        L += [""]

        # ---- building brief
        L += ["## Building brief (CoStar)"]
        if b:
            use = b.get("use_type") or "use not on record"
            if b.get("use_subtype"):
                use += f" ({str(b['use_subtype']).replace('_', ' ').lower()})"
            sqft = f"{round(b['sqft']):,} sq ft" if b.get("sqft") else "area not on record"
            L += [f"- {b.get('name') or b.get('address')}: {use}, {b.get('stories') or '?'} stories, "
                  f"built {b.get('year_built') or 'year not on record'}, {sqft} [src:brief.use_type]"]
            floor_no = tools._floor_number(g, lo["node_id"]) if (g and located) else None
            here = [t for t in b.get("tenants") or [] if floor_no is not None and t.get("floor") == floor_no]
            if here:
                names = "; ".join(t["name"] + (f" (suite {t['suite']})" if t.get("suite") else "")
                                  + (": " + ", ".join(t["risk_flags"]) if t.get("risk_flags") else "")
                                  for t in here[:8])
                L += [f"- On the caller's floor ({floor_no}): {names} [src:brief.tenants]"]
            elif located and b.get("tenants") is not None:
                L += ["- No tenant is on record for the caller's floor."]
            risks = b.get("occupancy_risks") or []
            if risks:
                kinds = {}
                for r in risks:
                    kinds.setdefault(r["flag"], []).append(r.get("tenant") or "?")
                top = "; ".join(f"{flag} ({len(who)})" for flag, who in sorted(kinds.items(), key=lambda x: -len(x[1]))[:4])
                L += [f"- In the building: {top} [src:brief.occupancy_risks]"]
            el = b.get("elevators") or {}
            if el:
                L += ["- Elevators on record: " + ", ".join(f"{v} {k}" for k, v in el.items())
                      + " [src:brief.elevators]"]
            L += [f"- Record: `{b.get('evidence_path')}` [src:brief.evidence_path]"]
        else:
            L += [PENDING]
        L += [""]

        if site and site.get("scope"):
            L += ["## What the scan covers", f"- {site['scope']} [src:site.scope]", ""]

        # ---- not confirmed
        L += ["## Not confirmed"]
        for line in self.unconfirmed():
            L += [f"- {line}"]
        if self.audit is None:
            L += [PENDING]
        L += [""]
        return "\n".join(L)

    def unconfirmed(self) -> list[str]:
        """Everything the packet states without a source, or cannot know."""
        out = []
        for r in (self.audit or {}).get("unsupported", []):
            out.append("No source found for: " + re.sub(r"\s*\[src:[^\]]*\]", "", r["claim"])[:200])
        g = self._g()
        foot = (self.route or {}).get("foot") or {}
        if g and foot.get("staging_id") and not g.nodes[foot["staging_id"]].get("confirmed"):
            out.append("Staging spot: placed by eye on the aerial image.")
        if self.location and self.location.get("below_threshold"):
            out.append("Exact position: below the confidence threshold.")
        if any(q["source"].startswith("simulated") for q in tools.SESSION.get("asked") or []):
            out.append("Caller answers in this run were simulated from a known position.")
        if tools.SESSION.get("simulate_match"):
            out.append("Caller position was supplied to this run, not matched from the photo.")
        if self.route is not None:
            out.append("Travel times use assumed walking speeds and door delays; nobody has timed these routes.")
            out.append("Door states (locked, open, blocked) are as scanned, not current.")
        if self.brief is not None:
            if not self.brief.get("scan_date"):
                out.append("The scan has no date on record.")
            out.append("Tenant list is CoStar's record, not current occupancy.")
        return out

    # ------------------------------------------------------------ console copy
    def flush(self):
        super().flush()
        try:
            self._push()
        except Exception as exc:                 # the console is a view; never fail the incident on it
            trace.log("console", "push_failed", result_summary=str(exc)[:200], phase="console")

    def _summary_fields(self) -> dict:
        lo, rt = self.location, self.route
        foot = (rt or {}).get("foot") or {}
        fields = {}
        if lo and lo.get("node_id"):
            fields["location"] = (f"{self._place(lo['node_id'])} (scan position {_short(lo['node_id'])}). "
                                  f"Confidence {lo['confidence'] * 100:.0f}%"
                                  + (f" after {lo['questions_asked']} question(s) to the caller."
                                     if lo.get("questions_asked") else "."))[:1000]
        if rt and rt.get("foot_directions"):
            steps = " ".join(f"{i}. {s['text']}" for i, s in enumerate(rt["foot_directions"], 1))
            fields["route"] = (f"On foot, about {_secs(foot.get('seconds'))}: {steps}")[:10000]
        if self.audit:
            a = self.audit
            fields["notes"] = (f"Drafted by the Pinpoint agent in {trace.elapsed_ms() / 1000:.1f} s. "
                               f"{a.get('supported')} of {a.get('checked')} packet lines trace to a source. "
                               + self._activity())[:10000]
        else:
            fields["notes"] = "The Pinpoint agent is working on this incident."
        fields["unconfirmed"] = [u[:1000] for u in self.unconfirmed()][:50] or ["Nothing listed yet."]
        fields["packetMarkdown"] = self.render()[:102400]
        return fields

    def _activity(self) -> str:
        """The agent's steps and how long each took, from its trace."""
        import json
        steps = []
        try:
            for line in (self.path.parent / "trace.jsonl").read_text().splitlines():
                r = json.loads(line)
                if r.get("dt_ms") is not None and r.get("actor") not in ("console", "system"):
                    took = f"{r['dt_ms'] / 1000:.1f} s" if r["dt_ms"] >= 100 else f"{round(r['dt_ms'])} ms"
                    steps.append(f"{r['actor']}: {r['tool'].replace('_', ' ')} ({took})")
        except (OSError, ValueError):
            return ""
        return "Agent steps: " + "; ".join(steps) + "." if steps else ""

    def _push(self):
        console, cid = tools.SESSION.get("console"), tools.SESSION.get("console_incident")
        if not console or not cid or self.sent:     # a write after approval would void the approval
            return
        lo = self.location or {}
        target = lo.get("node_id")
        if self.route and target and self._route_posted != target and tools.SESSION.get("console_scan"):
            body = console_export.route_update(str(self.intake["building_id"]), target, "foot")
            if body.get("reachable") is not False:
                try:
                    console.route(cid, body)
                    self._route_posted = target
                    trace.log("console", "route", result_summary=body.get("reason"), phase="console")
                except Exception as exc:
                    trace.log("console", "route_failed", result_summary=str(exc)[:200], phase="console")
        fields = self._summary_fields()
        digest = hashlib.sha1(repr(sorted(fields.items())).encode()).hexdigest()
        if digest != self._pushed:
            console.update(cid, **fields)
            self._pushed = digest
