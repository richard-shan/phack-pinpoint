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
    "unconfirmed_open": "A long link between scanned positions that the scan could not confirm is walkable",
    "unconfirmed_stairs": "Stairs inferred from the scan, not confirmed",
}


def _secs(v) -> str:
    return "?" if v is None else f"{round(v)} s"


def _short(node_id: str) -> str:
    return str(node_id)[:6]


def _cap(text: str) -> str:
    """First letter upper case, the rest untouched ("the curb on Broxton Ave" keeps its names)."""
    text = str(text)
    return text[:1].upper() + text[1:]


def _why_no_question(reason: str | None) -> str | None:
    """The agent's own skip reason, in words for a dispatcher."""
    if not reason:
        return None
    if reason.startswith("only one candidate"):
        return "the photo match left one likely position"
    if reason.startswith("all top candidates share room"):
        return "the likely positions are in the same room and are reached the same way"
    return reason


class LivePacketWriter(PacketWriter):
    def __init__(self, path, intake):
        super().__init__(path, intake)
        self._pushed = None
        self._route_posted = None
        self._route_image = None           # (target, where the picture is)

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
            if tools.SESSION.get("match_error"):
                L += [f"- Why: {tools.SESSION['match_error']}"]
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
                    if q["source"].startswith("not asked") or q["source"] == "none":
                        continue
                    L += [f"- Asked: \"{q['question']}\" Answer: \"{q['reply']}\" ({q['source']}) "
                          "[src:location.question_log]"]
            elif lo.get("skip_reason"):
                L += [f"- No question asked: {_why_no_question(lo['skip_reason'])}. [src:location.skip_reason]"]
            elif lo.get("below_threshold") and not tools.SESSION.get("given_position"):
                L += ["- No question asked: nothing a caller could answer separates the likely positions."]
            if tools.SESSION.get("given_position"):
                L += ["- The position was given by the dispatcher, not matched from a photo."]
            elif tools.SESSION.get("simulate_match"):
                L += ["- The match was simulated for this run: no photo was matched."]
            elif lo.get("evidence_path"):
                L += [f"- Photo match evidence: `{lo['evidence_path']}` [src:location.evidence_path]"]
            for line, key in lo.get("model_lines") or []:      # a run with a model in charge (live.run --llm)
                L += [f"- {line} [src:{key}]"]
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
            L += [f"- {_cap(self._label(sid))} [src:route.foot.staging_id]"]
            if g and not g.nodes[sid].get("confirmed"):
                L += ["- Placed by eye on the aerial image; not confirmed on site."]
            if site and site.get("tile_path"):
                L += [f"- Aerial reference: `{site['tile_path']}` [src:site.tile_path]"]
        elif ((g.nodes.get(door, {}) if (g and door) else {}).get("approach") or {}).get("via"):
            L += ["- At the street-address entrance. The scan starts upstairs: the street entrance, the "
                  "lobby and the way up are not in it, so no staging spot is marked."]
        else:
            L += ["- No staging spot is marked for this door."]
        aerial = (site or {}).get("aerial_features") or {}
        if rt is not None and aerial.get("vehicle_access"):
            L += [f"- Vehicle access, read from the aerial image: {aerial['vehicle_access']} "
                  "[src:site.aerial_features.vehicle_access]"]
        for o in (aerial.get("obstructions") or [])[:3] if rt is not None else []:
            if (o.get("confidence") or 0) >= 0.6 and o.get("description"):
                L += [f"- Seen from above: {o['description']} [src:site.aerial_features.obstructions]"]
        L += ["", "## Enter here"]
        if rt is None:
            L += [PENDING]
        elif door:
            tag = " [src:route.foot.entrance_id]" if foot.get("path") else ""
            L += [f"- {_cap(self._label(door))}{tag}"]
            saved, front_id = rt.get("seconds_saved_vs_front"), self._front_id()
            via = ((g.nodes.get(door, {}) if g else {}).get("approach") or {}).get("via")
            if via:
                pass                                   # a way onto the scanned floor, not a street door
            elif front_id and front_id != door and saved and saved > 0:
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
            at = node.get("attributes") or {}
            if node.get("note"):
                note = str(node["note"]).rstrip(".")
                L += ["- Noted when the scan was marked: " + (note if len(note) <= 320 else note[:317] + "...") + "."]
            if at.get("signs"):
                L += ["- Signs on it: " + "; ".join(f'"{x}"' for x in list(at["signs"])[:3]) + "."]
            for key, lead in (("hours", "Hours on the door"), ("lock_state", "Lock"), ("stretcher", "Stretcher")):
                if at.get(key):
                    L += [f"- {lead}: {str(at[key]).rstrip('.')}."]
            near = min((r for r in foot.get("ranked") or [] if r["entrance_id"] != door),
                       key=lambda r: r["inside_seconds"], default=None)
            if near and near["inside_seconds"] + 10 < (foot.get("inside_seconds") or 0) \
                    and near["inside_seconds"] <= 0.5 * (foot.get("inside_seconds") or 0):
                locked = ((g.nodes.get(near["entrance_id"], {}) if g else {}).get("approach") or {}).get("locked_entry")
                L += [f"- A door much closer to the caller: {near.get('label') or near['entrance_id']}, about "
                      f"{_secs(near['inside_seconds'])} from them. "
                      + ("It is assumed locked from outside, which is why it is not the first choice; someone "
                         "inside can open it." if locked else
                         f"Reaching it from outside takes about {_secs(near['outside_seconds'])}.")
                      + " [src:route.foot.ranked]"]
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
                ap = foot.get("approach") or {}
                if ap.get("via"):
                    first = (f"{_secs(foot.get('outside_seconds'))} to reach the scanned floor by {ap['via']}, an "
                             "estimate for the part of the building that is not scanned")
                elif ap.get("walk_m"):
                    first = (f"{_secs(foot.get('outside_seconds'))} through a part of the building that is not "
                             "scanned, an estimate")
                else:
                    first = f"{_secs(foot.get('outside_seconds'))} outside"
                total += f" ({first}; {_secs(foot.get('inside_seconds'))} inside the scan)"
            L += [total + f", {round(foot.get('meters') or 0)} m [src:route.foot.seconds]"]
            if self._route_image:
                L += [f"- Route on the floor plan: `{self._route_image[1]}`"]
        else:
            L += ["- No route: the caller's position is not known."]
        L += [""]

        # ---- stretcher
        if rt is not None and located:
            L += ["## Stretcher"]
            if stretcher.get("reachable") is False or not stretcher.get("entrance_id"):
                c = stretcher.get("carry") or {}
                if c:
                    kind, width = c.get("blocked_by"), c.get("blocked_width_m")
                    if kind in ("door", "locked_door") and width:
                        why = (f"a door measured at {width:.2f} m in the scan; a stretcher needs 0.75 m, and the "
                               "measurement could be a few centimeters off")
                    else:
                        why = {"stairs": "stairs (routes never use elevators)",
                               "door": "a door too narrow for a stretcher"}.get(kind, "no step-free way in the scan")
                    how = {"up": ", up the stairs", "down": ", down the stairs"}.get(c.get("carry_direction"), "") \
                        if c.get("carry_uses_stairs") else ""
                    L += [f"- A stretcher cannot reach the caller: {why}. "
                          f"Take it in through {self._label(c['entrance_id'])} to {self._place(c['node_id'])} "
                          f"(about {_secs(c['seconds_to_carry_point'])}), then carry: about "
                          f"{_secs(c['carry_seconds_on_foot'])} on foot from there{how}. [src:route.stretcher.carry]"]
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
            lines, seen = {}, {}
            for h in hz:
                if h.get("seen_in_scan"):              # lane B saw this in view here; not hand-checked
                    what = (h.get("note") or (h.get("type") or "").replace("_", " ")).strip().rstrip(".")
                    seen.setdefault(what[:1].lower() + what[1:], self._place(h["node_id"]))
                else:
                    text = f"{HAZARD_TEXT.get(h['type'], h['type'].replace('_', ' '))}: {self._label(h['node_id'])}"
                    lines[text] = lines.get(text, 0) + 1
            for text, n in lines.items():
                L += [f"- {text}" + (f" ({n} places)" if n > 1 else "") + " [src:route.hazards]"]
            for what, where in list(seen.items())[:4]:
                L += [f"- Seen in the scan along the way ({where}): {what} [src:route.hazards]"]
            if len(seen) > 4:
                L += [f"- {len(seen) - 4} more things were flagged in view along the way; see route.json. "
                      "[src:route.hazards]"]
            if not hz:
                L += ["- Nothing flagged on this route."]
            eq = rt.get("equipment") or []
            for e in eq[:3]:
                dist = f", {round(e['meters'])} m from the caller" if e.get("meters") is not None else ""
                note = f": {e['note'].rstrip('.')}" if e.get("note") else ""
                L += [f"- AED seen in the scan (read by a vision model, not checked): {e.get('room_label')}{dist}{note} "
                      "[src:route.equipment]"]
            if not eq:
                L += ["- No AED is marked in this building's graph."]
        L += [""]

        # ---- building brief
        L += ["## Building brief (CoStar)"]
        if b and b.get("not_in_costar"):
            L += [f"- {b.get('name')}: not in the CoStar kit, so there is no record of its use, tenants, "
                  "occupancy risks or contact. [src:brief.note]"]
        elif b:
            use = b.get("use_type") or "use not on record"
            if b.get("use_subtype") and str(b["use_subtype"]).lower() != "unknown":
                use += f" ({str(b['use_subtype']).replace('_', ' ').lower()})"
            sqft = f"{round(b['sqft']):,} sq ft" if b.get("sqft") else "area not on record"
            n_st = b.get("stories")
            stories = "stories not on record" if not n_st else f"{n_st} stor{'y' if n_st == 1 else 'ies'}"
            L += [f"- {b.get('name') or b.get('address')}: {use}, {stories}, "
                  f"built {b.get('year_built') or 'year not on record'}, {sqft} [src:brief.use_type]"]
            floor_no = tools._floor_number(g, lo["node_id"]) if (g and located) else None
            if n_st and floor_no and floor_no > n_st:
                L += [f"- CoStar lists {stories}, but the scan places the caller on floor {floor_no}. "
                      "One of the two records is wrong. [src:brief.stories]"]
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
            out.append("Unverified: " + re.sub(r"\s*\[src:[^\]]*\]", "", r["claim"])[:200])
        g = self._g()
        foot = (self.route or {}).get("foot") or {}
        if g and foot.get("staging_id") and not g.nodes[foot["staging_id"]].get("confirmed"):
            out.append("Staging spot")
        if self.location and self.location.get("below_threshold"):
            out.append("Exact position — confirm with caller")
        if any(q["source"].startswith("simulated") for q in tools.SESSION.get("asked") or []):
            out.append("Caller answers")
        if tools.SESSION.get("given_position"):
            out.append("Caller position (entered manually)")
        elif tools.SESSION.get("simulate_match"):
            out.append("Photo match")
        if ((self.site or {}).get("aerial_features") or {}).get("vehicle_access"):
            out.append("Vehicle access and obstructions (from aerial imagery)")
        if any(h.get("seen_in_scan") for h in (self.route or {}).get("hazards") or []):
            out.append("Hazards noted in the scan")
        if self.route is not None:
            out.append("Travel times are estimates")
            out.append("Door states (locked / open) as last scanned")
        if self.brief is not None:
            out.append("Current occupancy")
        return out

    # ------------------------------------------------------------ console copy
    def _draw_route(self):
        """route.jpg in the incident folder: the route on the floor plan, with the caller's
        photo. With a console attached it is also copied to where the console serves it."""
        target = (self.location or {}).get("node_id")
        foot = (self.route or {}).get("foot") or {}
        if not target or not foot.get("path") or (self._route_image and self._route_image[0] == target):
            return
        try:
            from . import route_image
            out = route_image.for_incident(self.path.parent)
            where = str(out)
            try:
                where = str(out.resolve().relative_to(tools.ROOT))
            except ValueError:
                pass
            if tools.SESSION.get("console") and tools.SESSION.get("console_incident"):
                where = tools.publish(out, "route.jpg")
            self._route_image = (target, where)
            self._write_route3d(foot)
        except Exception as exc:                 # a picture is a convenience, never a reason to fail
            trace.log("router", "route_image_failed", result_summary=str(exc)[:200], phase="render")

    def _write_route3d(self, foot):
        """route3d.json: the same route for the console's 3D model page."""
        import json
        from . import model3d
        lo, b = self.location or {}, str(self.intake["building_id"])
        st = (self.route or {}).get("stretcher") or {}
        lines = [f"On foot: about {_secs(foot.get('seconds'))}, {round(foot.get('meters') or 0)} m."]
        if st.get("entrance_id") and st.get("seconds"):
            lines.append(f"Stretcher: about {_secs(st['seconds'])}"
                         + ("." if st["entrance_id"] == foot.get("entrance_id") else f", through {self._label(st['entrance_id'])}."))
        elif st:
            lines.append("A stretcher cannot reach this position.")
        data = model3d.route_json(b, foot.get("path") or [], title="Route to the caller: " + str(self.intake.get("address") or b),
                                  lines=lines, caller=f"{self._place(lo['node_id'])} (confidence {lo.get('confidence', 0) * 100:.0f}%)")
        out = self.path.parent / "route3d.json"
        out.write_text(json.dumps(data))
        if tools.SESSION.get("console") and tools.SESSION.get("console_incident"):
            tools.publish(out, "route3d.json")

    def flush(self):
        self._draw_route()
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
            answered = sum(1 for q in tools.SESSION.get("asked") or [] if str(q.get("source", "")).startswith("caller"))
            fields["location"] = (f"{self._place(lo['node_id'])}. Confidence {lo['confidence'] * 100:.0f}%"
                                  + (f" after {answered} caller answer(s)." if answered else "."))[:1000]
        if rt and rt.get("foot_directions"):
            steps = " ".join(f"{i}. {s['text']}" for i, s in enumerate(rt["foot_directions"], 1))
            fields["route"] = (f"On foot, about {_secs(foot.get('seconds'))}: {steps}")[:10000]
        if self.audit:
            a = self.audit
            who = tools.SESSION.get("drafted_by") or "the Pinpoint agent"
            fields["notes"] = (f"Drafted by {who} in {trace.elapsed_ms() / 1000:.1f} s. "
                               f"{a.get('supported')} of {a.get('checked')} packet lines trace to a source. "
                               + self._activity())[:10000]
        else:
            fields["notes"] = "The Pinpoint agent is working on this incident."
        fields["unconfirmed"] = [u[:1000] for u in self.unconfirmed()][:50] or ["Nothing listed yet."]
        body = "\n".join(ln for ln in self.render().splitlines() if not ln.startswith("**Status:**"))
        fields["packetMarkdown"] = body[:102400]      # the console shows its own review status
        return fields

    def _activity(self) -> str:
        """The agent's steps and how long each took, from its trace."""
        import json
        steps = []                                # [what, total ms, times in a row]
        try:
            for line in (self.path.parent / "trace.jsonl").read_text().splitlines():
                r = json.loads(line)
                if r.get("dt_ms") is not None and r.get("actor") not in ("console", "system"):
                    what = f"{r['actor']}: {r['tool'].replace('_', ' ')}"
                    if steps and steps[-1][0] == what:         # a model may call one tool many times in a row
                        steps[-1][1] += r["dt_ms"]
                        steps[-1][2] += 1
                    else:
                        steps.append([what, r["dt_ms"], 1])
        except (OSError, ValueError):
            return ""
        took = lambda ms: f"{ms / 1000:.1f} s" if ms >= 100 else f"{round(ms)} ms"
        return ("Agent steps: " + "; ".join(f"{w}{f' x{n}' if n > 1 else ''} ({took(ms)})" for w, ms, n in steps) + "."
                if steps else "")

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
            console.update(cid, updatedBy="agent", **fields)
            self._pushed = digest
