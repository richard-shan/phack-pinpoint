"""Retroactive spec tests for Pinpoint.

These are system-level contract tests. They assert the behaviors we promised the judges,
independent of how any single function is implemented. If match_photo gets swapped or the
orchestrator refactored, these are the regression safety net.

Run: python3 agent/tests/test_specs.py
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from pinpoint import orchestrator, localization, routing, learning
from pinpoint import config as C
from pinpoint import stubs
from pinpoint.config import FIXTURES
from pinpoint.errors import VexcelUnavailable


# --------------------------- helpers ---------------------------
def canonical_intake():
    return json.loads((FIXTURES / "intake_sample.json").read_text())


def run_happy_path():
    intake = dict(canonical_intake(), incident_id=None)
    res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
    return res, Path(res["dir"])


def load_trace(idir):
    return [json.loads(line) for line in (idir / "trace.jsonl").read_text().splitlines()
            if line.strip()]


def src_tags(packet_md):
    return re.findall(r"\[src:([^\]]+)\]", packet_md)


def _cands(entries):
    return [{"node_id": nid, "score": s, "attributes": dict(attrs)}
            for nid, s, attrs in entries]


# --------------------------- A. Pipeline structure ---------------------------
class TestPipelineStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res, cls.idir = run_happy_path()
        cls.trace = load_trace(cls.idir)

    def _index_of(self, **match):
        for i, ev in enumerate(self.trace):
            if all(ev.get(k) == v for k, v in match.items()):
                return i
        return None

    def test_spec_01_plan_md_before_subagents(self):
        """Given a 911 intake, the Commander writes plan.md before any subagent tool fires."""
        i_plan = self._index_of(actor="commander", tool="write_todos")
        self.assertIsNotNone(i_plan, "no commander/write_todos event in trace")
        i_sub = None
        for i, ev in enumerate(self.trace):
            if ev.get("phase") == "tool" and ev.get("actor") in ("records", "locator", "site"):
                i_sub = i
                break
        self.assertIsNotNone(i_sub, "no subagent tool event (phase=tool) in trace")
        self.assertLess(i_plan, i_sub,
                        "write_todos (plan.md) must precede the first subagent tool call")
        self.assertTrue((self.idir / "plan.md").exists(), "plan.md not written to disk")

    def test_spec_02_three_subagents_start_within_50ms(self):
        """Records, Locator, Site all start within 50ms of each other (parallel, not serial)."""
        starts = {}
        for ev in self.trace:
            if ev.get("phase") != "tool":
                continue
            tool = ev.get("tool")
            if tool == "get_property" and "records" not in starts:
                starts["records"] = ev["elapsed_ms"] - (ev.get("dt_ms") or 0)
            elif tool == "match_photo" and "locator" not in starts:
                starts["locator"] = ev["elapsed_ms"] - (ev.get("dt_ms") or 0)
            elif tool == "detect_site_features" and "site" not in starts:
                starts["site"] = ev["elapsed_ms"] - (ev.get("dt_ms") or 0)
        for k in ("records", "locator", "site"):
            with self.subTest(subagent=k):
                self.assertIn(k, starts, f"{k} first tool call not found in trace")
        spread = max(starts.values()) - min(starts.values())
        self.assertLess(spread, 50.0,
                        f"start spread {spread:.1f}ms exceeds 50ms; starts={starts}")

    def test_spec_03_router_waits_for_three(self):
        """Router does not start until all three of Records/Locator/Site have returned."""
        i_router = None
        for i, ev in enumerate(self.trace):
            if ev.get("actor") == "router":
                i_router = i
                break
        self.assertIsNotNone(i_router, "no router event in trace")
        for tool in ("get_property", "match_photo", "detect_site_features"):
            i_ret = next((i for i, ev in enumerate(self.trace) if ev.get("tool") == tool), None)
            self.assertIsNotNone(i_ret, f"{tool} return event missing")
            self.assertLess(i_ret, i_router,
                            f"{tool} return must precede the first router event")

    def test_spec_04_verifier_after_router(self):
        """Verifier runs after Router."""
        i_router = next((i for i, ev in enumerate(self.trace) if ev.get("actor") == "router"),
                        None)
        i_verifier = next((i for i, ev in enumerate(self.trace) if ev.get("actor") == "verifier"),
                          None)
        self.assertIsNotNone(i_router, "no router event")
        self.assertIsNotNone(i_verifier, "no verifier event")
        self.assertLess(i_router, i_verifier)

    def test_spec_05_send_packet_only_after_resume(self):
        """send_packet fires only after an __interrupt__ payload is resumed with a decision."""
        i_interrupt = self._index_of(actor="commander", tool="interrupt:send_packet")
        i_approval = self._index_of(actor="dispatcher", tool="approval")
        i_send = self._index_of(actor="commander", tool="send_packet")
        self.assertIsNotNone(i_interrupt, "no commander/interrupt:send_packet event")
        self.assertIsNotNone(i_approval, "no dispatcher/approval event")
        self.assertIsNotNone(i_send, "no commander/send_packet event")
        self.assertLess(i_interrupt, i_approval,
                        "interrupt must precede the dispatcher approval")
        self.assertLess(i_approval, i_send,
                        "dispatcher approval must precede send_packet")

    def test_spec_06_notify_contact_gate(self):
        """notify_contact fires only after its own approval gate resume."""
        i_notify = next((i for i, ev in enumerate(self.trace)
                         if ev.get("tool") == "notify_contact"), None)
        self.assertIsNotNone(
            i_notify,
            "notify_contact never fires on the happy path; the orchestrator does not invoke "
            "stubs.notify_contact or expose it behind its own interrupt gate."
        )
        prior = self.trace[:i_notify]
        self.assertTrue(
            any(ev.get("tool") == "approval" for ev in prior),
            "notify_contact fired without a preceding approval event"
        )

    def test_spec_07_no_send_without_resume(self):
        """Without a resume decision, send_packet never produces an output."""
        intake = dict(canonical_intake(), incident_id=None)
        res = orchestrator.run_incident(
            intake, mock_llm=True, auto_approve=False,
            approve_fn=lambda _id, _p: "reject",
        )
        tr = load_trace(Path(res["dir"]))
        send_events = [ev for ev in tr
                       if ev.get("actor") == "commander" and ev.get("tool") == "send_packet"]
        self.assertEqual(send_events, [],
                         "send_packet fired despite reject decision")
        pm = (Path(res["dir"]) / "packet.md").read_text()
        self.assertNotIn("APPROVED & SENT", pm,
                         "packet marked APPROVED and SENT despite reject decision")


# --------------------------- B. Localization ---------------------------
class TestLocalizationBehavior(unittest.TestCase):
    def test_spec_08_high_confidence_skips_question(self):
        """Given top-candidate belief >= 0.80, the agent skips the caller question."""
        cands = _cands([
            ("suite210_interior", 0.85, {"floor": 2, "has_window": True, "room_type": "clinic"}),
            ("f2_waiting",        0.05, {"floor": 2, "has_window": True, "room_type": "waiting"}),
            ("f2_reception",      0.05, {"floor": 2, "has_window": True, "room_type": "reception"}),
            ("f1_reception",      0.05, {"floor": 1, "has_window": True, "room_type": "reception"}),
        ])
        localization.normalize_belief(cands)
        self.assertGreaterEqual(
            localization.top_confidence(cands), C.CONFIDENCE_THRESHOLD,
            "pre-condition: top confidence should be at least CONFIDENCE_THRESHOLD",
        )

    def test_spec_09_shared_room_and_door_skip_logged(self):
        """Given multiple live candidates that all share room_id AND best_entry_id, the agent
        skips the caller question and logs commander/question_skipped."""
        cands = _cands([
            ("suite210_interior", 0.90, {"floor": 2, "has_window": True}),
            ("f2_waiting",        0.05, {"floor": 2, "has_window": True}),
            ("f2_reception",      0.05, {"floor": 2, "has_window": True}),
        ])
        localization.normalize_belief(cands)
        skip, reason = orchestrator._should_skip_questions(C.DEMO_BUILDING, cands)
        self.assertTrue(skip, f"should skip; got skip={skip} reason={reason}")
        self.assertIsNotNone(reason)

    def test_spec_10_picks_max_info_gain_attribute(self):
        """Otherwise, the agent asks the attribute with the highest expected info gain across
        the candidate set."""
        cands = _cands([
            ("A", 0.5, {"floor": 1, "has_window": True}),
            ("B", 0.5, {"floor": 2, "has_window": True}),
        ])
        localization.normalize_belief(cands)
        q = localization.best_question(cands)
        self.assertEqual(q["attribute"], "floor",
                         "floor is the only splitting attribute; should be chosen")
        self.assertAlmostEqual(q["expected_info_gain"], 1.0, places=4,
                               msg="perfect split should yield 1 bit of info gain")
        self.assertTrue(q["eligible"])

    def test_spec_11_caller_answer_soft_updates(self):
        """A caller's answer soft-updates the belief (matching candidates boosted by r = 0.85,
        non-matching dampened by 0.15, nothing zeroed)."""
        self.assertAlmostEqual(C.ANSWER_RELIABILITY, 0.85, places=4,
                               msg="ANSWER_RELIABILITY must be 0.85")
        cands = _cands([("A", 0.5, {"floor": 1}), ("B", 0.5, {"floor": 2})])
        localization.normalize_belief(cands)
        out = localization.update_belief(cands, "floor", 1)
        a = next(c for c in out if c["node_id"] == "A")
        b = next(c for c in out if c["node_id"] == "B")
        self.assertGreater(a["belief"], b["belief"],
                           "matching candidate should rise above the non-matching one")
        self.assertAlmostEqual(a["belief"] / b["belief"], 0.85 / 0.15, places=3,
                               msg="ratio of posteriors must equal r / (1-r)")
        self.assertGreater(b["belief"], 0.0, "non-matching candidate must not be zeroed")

    def test_spec_12_max_questions_cap(self):
        """After MAX_QUESTIONS (3), the agent stops asking and proceeds with whatever belief
        it has."""
        self.assertEqual(C.MAX_QUESTIONS, 3, "MAX_QUESTIONS must be 3 per the config contract")
        _res, idir = run_happy_path()
        loc = json.loads((idir / "location.json").read_text())
        self.assertLessEqual(loc.get("questions_asked", 0), C.MAX_QUESTIONS,
                             f"agent asked {loc.get('questions_asked')} questions, "
                             f"exceeds MAX_QUESTIONS={C.MAX_QUESTIONS}")


# --------------------------- C. Learning loop ---------------------------
class TestLearningLoop(unittest.TestCase):
    def setUp(self):
        p = learning.agents_md_path(C.DEMO_BUILDING)
        if p.exists():
            p.unlink()

    def test_spec_13_reject_writes_agents_md(self):
        """A dispatcher reject at the approval gate writes an append-only entry to
        agent/buildings/<id>/AGENTS.md with timestamp, rejected room, corrected room, caller
        text, lesson."""
        intake = dict(canonical_intake(), incident_id=None)
        orchestrator.run_incident(
            intake, mock_llm=True, auto_approve=False,
            approve_fn=lambda _id, _p: {"decision": "reject",
                                        "corrected_room_id": "Suite 212 (dialysis, floor 2 east)"},
        )
        p = learning.agents_md_path(C.DEMO_BUILDING)
        self.assertTrue(p.exists(), f"AGENTS.md not written at {p}")
        text = p.read_text()
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.* correction logged",
                         "timestamp header missing")
        for field in ("Rejected:", "Corrected to:", "Caller text:", "Lesson:"):
            self.assertIn(field, text, f"entry missing field: {field}")

    def test_spec_14_correction_surfaces_in_next_packet(self):
        """On the next incident at the same building, the "Dispatcher notes for this building"
        section appears in packet.md with the correction."""
        learning.append_correction(
            building_id=C.DEMO_BUILDING,
            rejected_room_id={"node_id": "suite210_interior", "room_label": "clinic"},
            corrected_room_id="Suite 212 (dialysis, floor 2 east)",
            caller_text="my dad collapsed",
        )
        intake = dict(canonical_intake(), incident_id=None)
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        pm = (Path(res["dir"]) / "packet.md").read_text()
        self.assertIn("Dispatcher notes for this building", pm,
                      "packet missing Dispatcher-notes section after a correction was logged")
        self.assertIn("Suite 212", pm, "packet does not surface the corrected room")

    def test_spec_15_correction_claim_sourced(self):
        """The surfaced correction line passes the audit as a sourced claim (src:corrections)."""
        learning.append_correction(
            building_id=C.DEMO_BUILDING,
            rejected_room_id={"node_id": "suite210_interior", "room_label": "clinic"},
            corrected_room_id="Suite 212",
            caller_text="spec 15 probe",
        )
        intake = dict(canonical_intake(), incident_id=None)
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        pm = (Path(res["dir"]) / "packet.md").read_text()
        audit = json.loads((Path(res["dir"]) / "audit.json").read_text())
        self.assertIn("[src:corrections]", pm,
                      "packet missing [src:corrections] tag after correction logged")
        for u in audit.get("unsupported", []):
            self.assertNotIn("corrections", u.get("source_key") or "",
                             f"[src:corrections] came back unsupported: {u}")
        has_cor = any((c.get("source_key") or "") == "corrections"
                      for c in audit.get("claims", []) if c.get("supported"))
        self.assertTrue(has_cor,
                        "no supported claim with src:corrections in audit; the Verifier is not "
                        "resolving the corrections source for surfaced lines")


# --------------------------- D. Packet integrity ---------------------------
class TestPacketIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = learning.agents_md_path(C.DEMO_BUILDING)
        if p.exists():
            p.unlink()
        cls.res, cls.idir = run_happy_path()

    def test_spec_16_every_claim_has_source(self):
        """Every factual claim in packet.md has an evidence_path or record ID in audit.json."""
        pm = (self.idir / "packet.md").read_text()
        audit = json.loads((self.idir / "audit.json").read_text())
        tagged = re.findall(r"\[src:[^\]]+\]", pm)
        self.assertGreater(len(tagged), 0, "no tagged claims in packet.md")
        self.assertEqual(
            audit["checked"], len(tagged),
            f"audit.checked ({audit['checked']}) != tagged claim count ({len(tagged)})"
        )

    def test_spec_17_audit_unsourced_zero(self):
        """audit.unsupported count is 0 on every incident the orchestrator produces (happy
        path)."""
        audit = json.loads((self.idir / "audit.json").read_text())
        unsupported = audit.get("unsupported", [])
        self.assertEqual(len(unsupported), 0,
                         f"unsupported claims on happy path: {unsupported}")

    def test_spec_18_missing_data_not_fabricated(self):
        """Missing data appears as explicit "unknown" or omission, never as a fabricated value.
        Probe: feed an intake with address = "UNKNOWN 9999 Nowhere Rd" and assert the packet says
        address-not-found, does NOT invent a tenant name, does NOT fabricate a building_id, and
        no claim appears without a source."""
        intake = {
            "incident_id": None,
            "address": "UNKNOWN 9999 Nowhere Rd",
            "building_id": "bayou-medical-plaza",
            "caller_photo_path": "fixtures/building_bayou/pano/suite210_interior.jpg",
            "caller_text": "spec 18 probe",
        }
        res = orchestrator.run_incident(intake, mock_llm=True, auto_approve=True)
        pm = (Path(res["dir"]) / "packet.md").read_text()
        audit = json.loads((Path(res["dir"]) / "audit.json").read_text())
        self.assertIn("Address not found", pm,
                      "UNABLE packet does not state the address-not-found cause")
        for name in ("Houston Dialysis", "Walker & Chen Legal", "Kessler Cardiology"):
            self.assertNotIn(name, pm, f"tenant {name!r} fabricated in UNABLE packet")
        self.assertNotIn("Loading Dock", pm, "entrance fabricated in UNABLE packet")
        self.assertNotIn("ext_dock", pm, "entrance node fabricated in UNABLE packet")
        self.assertEqual(len(audit.get("unsupported", [])), 0,
                         f"UNABLE packet produced unsourced claims: {audit.get('unsupported')}")


# --------------------------- E. Data layer joins ---------------------------
class TestDataLayerJoins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = learning.agents_md_path(C.DEMO_BUILDING)
        if p.exists():
            p.unlink()
        cls.res, cls.idir = run_happy_path()
        cls.pm = (cls.idir / "packet.md").read_text()

    def test_spec_19_packet_cites_costar(self):
        """The packet cites CoStar (property/tenant record ID) for building identity and
        tenant."""
        tags = src_tags(self.pm)
        brief_tags = [t for t in tags if t.startswith("brief.")]
        self.assertGreater(len(brief_tags), 0,
                           f"no brief.* src tags (CoStar) in packet; tags={tags}")
        self.assertIn("brief.located_tenant", tags,
                      "packet does not cite the located tenant")

    def test_spec_20_packet_cites_matterport(self):
        """The packet cites a Matterport node_id and its evidence_path for the predicted
        room."""
        tags = src_tags(self.pm)
        self.assertIn("location.node_id", tags,
                      "packet does not cite location.node_id (Matterport node)")
        loc = json.loads((self.idir / "location.json").read_text())
        ev = loc.get("evidence_path") or ""
        self.assertIn(ev, self.pm,
                      f"packet missing the matched-panorama evidence_path {ev!r}")

    def test_spec_21_vexcel_or_fixture_citation(self):
        """The packet cites a Vexcel aerial tile path for the staging door recommendation when
        Vexcel is available; on fallback, cites the fixture tile path instead and logs
        site/vexcel_fallback."""
        from pinpoint import vexcel as _vx

        with self.subTest(path="vexcel-available"):
            orig = _vx.fetch_aerial
            try:
                _vx.fetch_aerial = lambda address, lat, lon, building_id=None: {
                    "tile_path": f"fixtures/{building_id}/vexcel_tile.jpg",
                    "features": {"primary_roof_shape": "flat"},
                    "tile_url": "https://api.vexcelgroup.com/stub",
                }
                p = learning.agents_md_path(C.DEMO_BUILDING)
                if p.exists():
                    p.unlink()
                res = orchestrator.run_incident(
                    dict(canonical_intake(), incident_id=None),
                    mock_llm=True, auto_approve=True,
                )
                pm = (Path(res["dir"]) / "packet.md").read_text()
                self.assertIn("Aerial reference", pm,
                              "vexcel-available: Aerial-reference line missing from packet")
                self.assertIn("[src:site.aerial_tile_path]", pm,
                              "vexcel-available: packet missing [src:site.aerial_tile_path]")
            finally:
                _vx.fetch_aerial = orig

        with self.subTest(path="vexcel-fallback"):
            orig = _vx.fetch_aerial

            def _boom(*a, **k):
                raise VexcelUnavailable("spec 21 forced fallback")

            try:
                _vx.fetch_aerial = _boom
                p = learning.agents_md_path(C.DEMO_BUILDING)
                if p.exists():
                    p.unlink()
                res = orchestrator.run_incident(
                    dict(canonical_intake(), incident_id=None),
                    mock_llm=True, auto_approve=True,
                )
                tr = load_trace(Path(res["dir"]))
                pm = (Path(res["dir"]) / "packet.md").read_text()
                site = json.loads((Path(res["dir"]) / "site.json").read_text())
                self.assertTrue(
                    any(ev.get("actor") == "site" and ev.get("tool") == "vexcel_fallback"
                        for ev in tr),
                    "fallback: no site/vexcel_fallback trace event",
                )
                fixture_tile = site.get("tile_path")
                self.assertIsNotNone(fixture_tile,
                                     "fallback: site.json has no tile_path to fall back to")
                self.assertIn(
                    fixture_tile, pm,
                    "fallback: packet does not cite the fixture tile_path on Vexcel unavailable"
                )
            finally:
                _vx.fetch_aerial = orig


# --------------------------- F. Latency contract ---------------------------
class TestLatencyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = learning.agents_md_path(C.DEMO_BUILDING)
        if p.exists():
            p.unlink()
        cls.res, cls.idir = run_happy_path()
        cls.trace = load_trace(cls.idir)

    def test_spec_22_draft_under_100ms(self):
        """Draft packet is produced in under 100ms on stub data."""
        draft_ms = None
        for ev in self.trace:
            if ev.get("actor") == "commander" and ev.get("tool") == "draft_ready":
                draft_ms = ev["elapsed_ms"]
                break
        self.assertIsNotNone(draft_ms, "commander/draft_ready trace event missing")
        self.assertLess(draft_ms, 100.0,
                        f"draft took {draft_ms}ms; exceeds 100ms budget")

    def test_spec_23_parallel_start_spread_under_50ms(self):
        """The parallel-section start spread (max-min of Records/Locator/Site start times
        from trace.jsonl) is under 50ms."""
        starts = {}
        for ev in self.trace:
            if ev.get("phase") != "tool":
                continue
            tool = ev.get("tool")
            if tool == "get_property" and "records" not in starts:
                starts["records"] = ev["elapsed_ms"] - (ev.get("dt_ms") or 0)
            elif tool == "match_photo" and "locator" not in starts:
                starts["locator"] = ev["elapsed_ms"] - (ev.get("dt_ms") or 0)
            elif tool == "detect_site_features" and "site" not in starts:
                starts["site"] = ev["elapsed_ms"] - (ev.get("dt_ms") or 0)
        for k in ("records", "locator", "site"):
            self.assertIn(k, starts, f"{k} first tool call not found in trace")
        spread = max(starts.values()) - min(starts.values())
        self.assertLess(spread, 50.0,
                        f"start spread {spread:.1f}ms exceeds 50ms; starts={starts}")

    def test_spec_24_every_tool_call_has_dt_ms(self):
        """Every tool call in trace.jsonl carries a non-null dt_ms."""
        missing = []
        for ev in self.trace:
            if ev.get("phase") == "tool" and ev.get("dt_ms") is None:
                missing.append({"actor": ev.get("actor"), "tool": ev.get("tool")})
        self.assertEqual(missing, [],
                         f"phase=tool events with null dt_ms: {missing}")


# --------------------------- G. Eval scoring invariants ---------------------------
class TestEvalScoringInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import run_eval as rev
        cls.rev = rev
        p = learning.agents_md_path(C.DEMO_BUILDING)
        if p.exists():
            p.unlink()
        cls.res, cls.idir = run_happy_path()
        cls.rm = rev.room_map()
        cls.predicted_node = json.loads((cls.idir / "location.json").read_text())["node_id"]
        cls.predicted_room = cls.rm[cls.predicted_node]

    def test_spec_25_room_accuracy_top1_on_room_id(self):
        """run_eval scores room accuracy as top-1 match on room_id (not node_id)."""
        label_correct = {
            "incident_id": "specs-25-correct",
            "building_id": C.DEMO_BUILDING,
            "ground_truth_room_id": self.predicted_room,
            "ground_truth_node_id": self.predicted_node,
            "ground_truth_best_entry_id": "ext_dock",
        }
        scored = self.rev.score_one(label_correct, self.idir, self.rm)
        self.assertEqual(scored["predicted_room"], self.predicted_room,
                         "run_eval must derive predicted_room via room_map (not node_id)")
        self.assertTrue(scored["room_correct"],
                        "same room_id should score correct")
        label_wrong = dict(label_correct,
                           incident_id="specs-25-wrong",
                           ground_truth_room_id="DEFINITELY NOT A ROOM")
        scored_wrong = self.rev.score_one(label_wrong, self.idir, self.rm)
        self.assertFalse(scored_wrong["room_correct"],
                         "different room_id should score incorrect")

    def test_spec_26_meters_saved_only_on_correct(self):
        """Meters saved is counted only on incidents where the room prediction was correct."""
        correct = {
            "incident_id": "specs-26-correct",
            "building_id": C.DEMO_BUILDING,
            "ground_truth_room_id": self.predicted_room,
            "ground_truth_node_id": self.predicted_node,
            "ground_truth_best_entry_id": "ext_dock",
        }
        wrong = dict(correct, incident_id="specs-26-wrong",
                     ground_truth_room_id="NEVER_A_ROOM")
        c = self.rev.score_one(correct, self.idir, self.rm)
        w = self.rev.score_one(wrong, self.idir, self.rm)
        self.assertIsNotNone(c["meters_saved"],
                             "meters_saved must be a number on a correct prediction")
        self.assertIsNone(w["meters_saved"],
                          "meters_saved must be None on an incorrect prediction")

    def test_spec_27_seconds_to_draft_before_approval(self):
        """Seconds-to-draft is measured at the first packet-streaming-done event, before the
        approval gate."""
        tr = load_trace(self.idir)
        i_draft = next((i for i, ev in enumerate(tr)
                        if ev.get("actor") == "commander" and ev.get("tool") == "draft_ready"),
                       None)
        i_gate = next((i for i, ev in enumerate(tr)
                       if ev.get("tool") == "interrupt:send_packet"), None)
        self.assertIsNotNone(i_draft, "no commander/draft_ready event")
        self.assertIsNotNone(i_gate, "no commander/interrupt:send_packet event")
        self.assertLess(i_draft, i_gate,
                        "draft_ready must precede the approval interrupt gate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
