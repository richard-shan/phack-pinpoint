"""PacketWriter — renders packet.md and rewrites it on every flush() so Person D's console sees
the one-page packet FILL IN section-by-section as subagents return (plan 11: 'the packet filling
in'). Claims that must be source-checked carry a [src:...] tag the Verifier reads (check_claim).

Packet sections follow plan 3 ('the responder packet').
"""
from pathlib import Path

PENDING = "_⏳ pending…_"


class PacketWriter:
    def __init__(self, path: Path, intake: dict):
        self.path = Path(path)
        self.intake = intake
        self.brief = self.location = self.site = self.route = self.audit = None
        self.correction_notes = []  # markdown lines from learning.summarize_for_packet
        self.sent = False
        self.status = "OK"             # OK | DEGRADED | UNABLE
        self.failure_mode = None
        self.failure_sentence = None
        self.failure_cite_tag = None   # resolves under sources
        self.attempted_fields = {}     # {field: value} cited by [src:intake.*]

    def set_brief(self, b): self.brief = b
    def set_location(self, l): self.location = l
    def set_site(self, s): self.site = s
    def set_route(self, r): self.route = r
    def set_audit(self, a): self.audit = a
    def set_correction_notes(self, lines): self.correction_notes = list(lines or [])
    def mark_sent(self): self.sent = True
    def set_failure(self, status, mode, sentence, cite_tag, attempted=None):
        """Record a failure so render() produces an UNABLE/DEGRADED packet with a
        sourced claim. cite_tag is the dotted [src:...] key (e.g. 'costar.lookup_miss'),
        which the orchestrator must also populate in the verifier's sources dict."""
        self.status = status
        self.failure_mode = mode
        self.failure_sentence = sentence
        self.failure_cite_tag = cite_tag
        if attempted is not None:
            self.attempted_fields = dict(attempted)

    def flush(self):
        self.path.write_text(self.render())

    # ---- claims the verifier checks (every line with a [src:] tag) ----
    def tagged_claims(self):
        return [ln.strip("- ").strip() for ln in self.render().splitlines() if "[src:" in ln]

    def render(self):
        L = []
        if self.status == "UNABLE":
            status = "UNABLE - dispatcher action required"
        elif self.status == "DEGRADED":
            status = "DEGRADED - partial information; see detail below"
        elif self.sent:
            status = "✅ APPROVED & SENT"
        else:
            status = "🟡 DRAFT - awaiting dispatcher approval"
        addr = (self.intake or {}).get("address") or "(no address supplied)"
        L += [f"# Responder Packet - {addr}", "", f"**Status:** {status}", ""]
        if self.failure_sentence:
            L += ["## Status detail"]
            if self.failure_cite_tag:
                L += [f"- {self.failure_sentence} [src:{self.failure_cite_tag}]"]
            else:
                L += [f"- {self.failure_sentence}"]
            for k, v in sorted(self.attempted_fields.items()):
                shown = v if v not in (None, "") else "(missing)"
                L += [f"- Attempted {k}: `{shown}` [src:intake.{k}]"]
            L += [""]
        if self.status == "UNABLE":
            # Short-circuit: suppress the normal body sections on UNABLE packets so the
            # dispatcher sees only the Status detail. Keeps the audit clean and the UI
            # focused on the decision they have to make.
            return "\n".join(L)

        # Location
        L += ["## Location"]
        if self.location:
            lo = self.location
            conf = f"{lo['confidence']*100:.0f}%"
            flag = "  ⚠️ below threshold — floor & wing only" if lo.get("below_threshold") else ""
            L += [f"- Floor {lo.get('floor')}, {lo.get('room_type')}, node `{lo['node_id']}` "
                  f"(confidence {conf}){flag} [src:location.node_id]",
                  f"- Questions asked to disambiguate: {lo.get('questions_asked',0)} "
                  f"[src:location.questions_asked]",
                  f"- Matched panorama vs caller photo: `{lo.get('evidence_path')}`"]
        else:
            L += [PENDING]
        L += [""]

        # Dispatcher notes for this building (from AGENTS.md learned corrections)
        if self.correction_notes:
            L += ["## Dispatcher notes for this building"]
            L += list(self.correction_notes)
            L += [""]

        # Stage + Enter
        L += ["## Stage here"]
        if self.site and self.site.get("staging"):
            s = self.site["staging"][0]
            L += [f"- {s['label']} (`{s['node_id']}`) [src:site.staging]"]
            if self.site.get("aerial_tile_path"):
                L += [f"- Aerial reference: `{self.site['aerial_tile_path']}` [src:site.aerial_tile_path]"]
            elif self.site.get("tile_path"):
                L += [f"- Aerial reference: `{self.site['tile_path']}` (fallback) [src:site.tile_path]"]
        else:
            L += [PENDING]
        L += ["", "## Enter here"]
        if self.route and self.route.get("foot", {}).get("entrance_id"):
            ent = self.route["foot"]["entrance_id"]
            saved = self.route.get("seconds_saved_vs_front")
            note = ""
            if ent != "ext_main":
                note = f"  — NOT the street-address door; saves ~{saved}s vs front"
            L += [f"- Door `{ent}`{note} [src:route.foot.entrance_id]"]
        else:
            L += [PENDING]
        L += [""]

        # Route
        L += ["## Route (crew on foot)"]
        if self.route and self.route.get("foot_directions"):
            for i, step in enumerate(self.route["foot_directions"], 1):
                L += [f"{i}. {step['text']} ({step['meters']} m) [src:route.foot.path]"]
            f = self.route["foot"]
            L += [f"- Total ~{f.get('seconds')}s [src:route.foot.seconds]"]
        else:
            L += [PENDING]
        L += [""]

        # Stretcher route if different
        if self.route and self.route.get("stretcher_differs"):
            L += ["## Stretcher route (differs — uses elevator)"]
            for i, step in enumerate(self.route.get("stretcher_directions", []), 1):
                L += [f"{i}. {step['text']} ({step['meters']} m) [src:route.stretcher.path]"]
            st = self.route["stretcher"]
            L += [f"- Total ~{st.get('seconds')}s [src:route.stretcher.seconds]", ""]

        # On the way
        L += ["## On the way"]
        if self.route is not None:
            hz = self.route.get("hazards", [])
            if hz:
                for h in hz:
                    L += [f"- {h['type']} at `{h['node_id']}` [src:route.hazards]"]
            else:
                L += ["- No locked doors or stairs flagged on the chosen route"]
        else:
            L += [PENDING]
        L += [""]

        # Equipment
        L += ["## Nearby equipment"]
        if self.route and self.route.get("equipment"):
            for e in self.route["equipment"]:
                L += [f"- AED at {e['room_label']} (`{e['node_id']}`) [src:route.equipment]"]
        else:
            L += ["- none found" if self.route is not None else PENDING]
        L += [""]

        # Building brief
        L += ["## Building brief"]
        if self.brief:
            b = self.brief
            t = b.get("located_tenant") or {}
            L += [f"- {b['use_type']}, {b['stories']} stories, built {b['year_built']}, "
                  f"{b['sqft']} sqft [src:brief.use_type]",
                  f"- Located suite: {t.get('name','?')} (suite {t.get('suite','?')}) — "
                  f"**{t.get('occupancy_risk','')}** [src:brief.located_tenant]",
                  f"- Scan date: {b.get('scan_date')} [src:brief.scan_date]",
                  f"- Contact: {b['contact']['name']} {b['contact']['phone']} [src:brief.contact]"]
        else:
            L += [PENDING]
        L += [""]

        # Point of contact (building manager / tenant contact from the records brief)
        if self.brief and (self.brief.get("contact") or {}).get("name"):
            c = self.brief["contact"]
            method = c.get("phone") or c.get("email") or c.get("method") or ""
            role = f" ({c.get('role')})" if c.get("role") else ""
            L += ["## Point of contact",
                  f"- {c['name']}{role}: {method} [src:records.contact]",
                  ""]

        # Not confirmed
        L += ["## Not confirmed"]
        if self.audit:
            un = self.audit.get("unsupported", [])
            if un:
                for r in un:
                    L += [f"- UNSUPPORTED: {r['claim'][:80]}"]
            else:
                L += ["- All packet claims trace to a source."]
            if self.location and self.location.get("below_threshold"):
                L += ["- Exact room below confidence threshold; stated as floor & wing."]
        else:
            L += [PENDING]
        L += [""]
        return "\n".join(L)
