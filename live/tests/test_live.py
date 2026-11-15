"""One incident end to end on real records and routing, with and without the console.

No network and no photo-matching packages are needed: the caller's position is given to
the run, which exercises everything else (records, question loop, routing, packet,
verifier, approval gates, console). Building 6550930's graph and the CoStar index are
both in the repo.
"""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from live import run, tools
from live.console_api import Console

from env import console as console_export
from env import tools as A
from pinpoint import learning, orchestrator, stubs

ROOT = Path(__file__).resolve().parent.parent.parent
BUILDING = "6550930"                       # 7900 Fannin St: lobby scan, four doors, 17 health care tenants
TARGET = "3ymmrb20g30hctysc1fq4ztpc"       # the Suite 1200 door, in the cross corridor


SOUTH, NORTH = "6tm9tky8ehef6yfhg8ymq2s5b", "i3d7aunib0nbrnnaifmribxzb"    # lobby, by the doors at each end


def _ambiguous(building_id, truth, k):
    """A match that cannot tell the corridor position from two lobby positions at opposite
    doors. The agent only asks the caller when the candidates are in different rooms or
    the answer would change the door, so its question loop needs a spread like this."""
    g = tools._graph(building_id)
    return [{"node_id": n, "score": score, "simulated": True, "evidence_path": g.nodes[n].get("pano_path")}
            for n, score in ((truth, 0.42), (SOUTH, 0.33), (NORTH, 0.25))]


class _Base(unittest.TestCase):
    def setUp(self):
        self.old_sim = tools._simulated_candidates
        tools._simulated_candidates = _ambiguous
        self.tmp = tempfile.TemporaryDirectory()
        self.old_incidents, self.old_assets = orchestrator.INCIDENTS, tools.ASSETS
        orchestrator.INCIDENTS = Path(self.tmp.name) / "incidents"
        orchestrator.INCIDENTS.mkdir()
        tools.ASSETS = Path(self.tmp.name) / "assets"
        self.old_state, run.STATE = run.STATE, Path(self.tmp.name) / "state"     # the watcher's record of handled incidents

    def tearDown(self):
        tools.uninstall()
        tools._simulated_candidates = self.old_sim
        orchestrator.INCIDENTS, tools.ASSETS = self.old_incidents, self.old_assets
        run.STATE = self.old_state
        self.tmp.cleanup()

    def files(self, res):
        d = Path(res["dir"])
        return {p.stem: (json.loads(p.read_text()) if p.suffix == ".json" else p.read_text())
                for p in d.iterdir() if p.suffix in (".json", ".md")}


class _ScriptedModel:
    """Stands in for the model on the agent's LLM path, so that path is tested without an
    API key: it calls the tools the agent's wrappers call, writes its reports where the
    agent writes them (before each approval gate and at the end), and asks to send."""

    def __init__(self, node=None, door="door_made_up", belief=0.5, asks_to_send=True, question=None):
        self.node, self.door, self.belief = node, door, belief
        self.asks_to_send, self.question = asks_to_send, question
        self.decisions, self.mistake = [], None

    def _report(self, idir, node):
        (idir / "location.json").write_text(json.dumps(
            {"node_id": node, "room_id": "r", "room_label": "somewhere", "belief": self.belief,
             "candidates": [], "questions_asked": 0, "evidence_path": ""}))
        (idir / "route.json").write_text(json.dumps({"best_entry_id": self.door, "path_nodes": []}))
        (idir / "packet.md").write_text("# the model's own packet\n")

    def __call__(self, intake, idir, auto_approve=True, approve_fn=None):
        from pinpoint import trace
        idir = Path(idir)
        idir.mkdir(parents=True, exist_ok=True)
        trace.start(idir)
        cands = stubs.match_photo(intake["incident_id"], intake["caller_photo_path"], 10)   # wrong id in the slot
        stubs.detect_site_features(intake["building_id"])
        self.mistake = stubs.look("not-a-position")
        if self.question:
            stubs.ask_caller(intake["incident_id"], self.question)
        node = self.node or cands[0]["node_id"]
        if self.asks_to_send:
            for action in ("send_packet", "notify_contact"):
                self._report(idir, node)
                self.decisions.append(approve_fn(intake["incident_id"], [action]))
        self._report(idir, node)
        return {"interrupts": len(self.decisions), "cost_usd": 0.0}


class ModelInCharge(_Base):
    """`live.run --llm` with a scripted model: what the model may decide, and what it may not."""

    def setUp(self):
        super().setUp()
        from pinpoint import budget, deep_agent
        self.deep_agent = deep_agent
        self.saved = (deep_agent.run_real_agent, deep_agent._ensure_api_key, deep_agent.build_agent,
                      budget.check_budget, run.INCIDENTS)
        deep_agent._ensure_api_key = lambda: "placeholder"
        deep_agent.build_agent = lambda *a, **k: None
        budget.check_budget = lambda: (5.0, False)
        run.INCIDENTS = orchestrator.INCIDENTS

    def tearDown(self):
        from pinpoint import budget
        (self.deep_agent.run_real_agent, self.deep_agent._ensure_api_key, self.deep_agent.build_agent,
         budget.check_budget, run.INCIDENTS) = self.saved
        super().tearDown()

    def run_with(self, model, **kw):
        self.deep_agent.run_real_agent = model
        res = run.run_llm_incident(BUILDING, None, "collapsed by the surgery office", position=TARGET, **kw)
        return res, self.files(res)

    def test_packet_is_written_from_the_tools_not_by_the_model(self):
        best = A.best_entry(BUILDING, TARGET, "foot")
        model = _ScriptedModel(door=best["entrance_id"])
        res, f = self.run_with(model)
        self.assertEqual(model.decisions, ["approve", "reject"])       # the packet; contacting the building
        self.assertIn("error", model.mistake)                          # a bad argument comes back as a result
        self.assertEqual((res["decision"], res["position"], res["door"]), ("approve", TARGET, best["entrance_id"]))
        self.assertEqual(f["audit"]["status"], "OK")
        self.assertEqual(f["audit"]["unsupported"], [])
        self.assertIn("## Enter here", f["packet"])
        self.assertIn("Position chosen by the agent's model", f["packet"])
        self.assertIn("The model's report names the same door.", f["packet"])
        self.assertNotIn("the model's own packet", f["packet"])
        kept = Path(res["dir"]) / "llm"
        self.assertIn("the model's own packet", (kept / "packet.md").read_text())
        self.assertEqual(json.loads((kept / "location.json").read_text())["node_id"], TARGET)
        self.assertEqual(f["route"]["foot"]["entrance_id"], best["entrance_id"])

    def test_a_position_the_match_did_not_return_is_not_used(self):
        res, f = self.run_with(_ScriptedModel(node=SOUTH))
        self.assertEqual((res["position"], res["model_position"]), (TARGET, SOUTH))
        self.assertIn("which the photo match did not return", f["packet"])
        self.assertIn("names a different door (door_made_up)", f["packet"])
        self.assertEqual(f["audit"]["unsupported"], [])

    def test_a_model_that_never_asks_to_send_still_gets_a_packet(self):
        model = _ScriptedModel(asks_to_send=False)
        res, f = self.run_with(model)
        self.assertEqual(model.decisions, [])
        self.assertTrue(res["model_never_asked_to_send"])
        self.assertEqual(res["decision"], "approve")
        self.assertIn("## Enter here", f["packet"])

    def test_the_models_own_question_goes_out_as_written(self):
        res, f = self.run_with(_ScriptedModel(question="Which shop is nearest to you?"))
        asked = tools.SESSION["asked"][0]
        self.assertEqual(asked["question"], "Which shop is nearest to you?")
        self.assertTrue(asked["source"].startswith("not asked"))        # no caller is connected in this run
        self.assertIn("the model did not put a question to the caller", f["packet"])


class WithoutConsole(_Base):
    def test_incident_runs_on_real_records_and_routing(self):
        res = run.run_incident(BUILDING, None, "caller text", position=TARGET, auto_approve=True)
        f = self.files(res)
        self.assertEqual(res["decision"], "approve")
        self.assertEqual(f["brief"]["building_id"], BUILDING)
        self.assertEqual(f["brief"]["address"], "7900 Fannin St")
        self.assertTrue(f["brief"]["occupancy_risks"])
        best = A.best_entry(BUILDING, f["location"]["node_id"], "foot")
        self.assertEqual(f["route"]["foot"]["entrance_id"], best["entrance_id"])
        self.assertEqual(f["route"]["front_door_baseline"]["entrance_id"], best["front_door"]["entrance_id"])
        self.assertAlmostEqual(f["route"]["seconds_saved_vs_front"], best["front_door"]["seconds_saved"], places=1)
        self.assertEqual(f["audit"]["status"], "OK")
        self.assertEqual(f["audit"]["unsupported"], [], f["audit"]["unsupported"])
        self.assertGreater(f["audit"]["checked"], 15)
        for heading in ("## Caller location", "## Stage here", "## Enter here", "## Route: crew on foot",
                        "## Building brief (CoStar)", "## Not confirmed"):
            self.assertIn(heading, f["packet"])
        self.assertNotIn("ext_main", f["packet"])
        self.assertNotIn("Bayou", f["packet"])
        self.assertIn("given by the dispatcher, not matched from a photo", f["packet"])
        self.assertEqual((f["location"]["node_id"], f["location"]["confidence"], f["location"]["questions_asked"]),
                         (TARGET, 1.0, 0))                       # a known position: no questions
        self.assertNotIn("problem", res)
        picture = Path(res["dir"]) / "route.jpg"
        self.assertTrue(picture.exists() and picture.stat().st_size > 20000)
        self.assertIn("Route on the floor plan", f["packet"])

    def test_question_loop_raises_confidence_in_the_true_position(self):
        res = run.run_incident(BUILDING, None, simulate_match=TARGET, auto_approve=True)
        loc = self.files(res)["location"]
        self.assertGreaterEqual(loc["questions_asked"], 1)
        self.assertEqual(loc["node_id"], TARGET)
        self.assertGreater(loc["confidence"], 0.42)                  # the match alone gave 0.42
        asked = tools.SESSION["asked"][0]
        self.assertIn(asked["attribute"], tools.QUESTIONS)
        self.assertTrue(asked["source"].startswith("simulated caller"))

    def test_no_question_when_it_would_not_change_the_door(self):
        tools._simulated_candidates = self.old_sim                   # truth plus its nearest neighbours
        res = run.run_incident(BUILDING, None, simulate_match=TARGET, auto_approve=True)
        loc = self.files(res)["location"]
        self.assertEqual(loc["questions_asked"], 0)
        self.assertIn("share room", loc["skip_reason"])

    def test_lane_b_data_reaches_the_packet(self):
        res = run.run_incident(BUILDING, None, position=TARGET, auto_approve=True)
        f = self.files(res)
        self.assertIn("room_type", tools.question_attributes(BUILDING, TARGET))       # from perception/data
        self.assertIn("Vehicle access, read from the aerial image", f["packet"])
        self.assertTrue(any("Turn" in s["text"] and " at the " in s["text"] for s in f["route"]["foot_directions"]))

    def test_unknown_photo_degrades_instead_of_crashing(self):
        res = run.run_incident(BUILDING, str(Path(self.tmp.name) / "missing.jpg"), auto_approve=True)
        f = self.files(res)
        self.assertEqual(f["audit"]["status"], "DEGRADED")
        self.assertIn("DEGRADED", f["packet"])
        self.assertIn("could not be read", res["problem"])       # the run says so, and so does the packet
        self.assertIn("- Why: the caller photo could not be read", f["packet"])
        self.assertIn("south plaza doors", f["packet"])          # falls back to the front door by name

    def test_install_and_uninstall(self):
        before = stubs.match_photo
        tools.install()
        self.assertIsNot(stubs.match_photo, before)
        tools.uninstall()
        self.assertIs(stubs.match_photo, before)

    def test_llm_agent_constructs_with_the_real_tools(self):
        """The agent's deepagents path accepts the real tools (every one needs a description).
        Construction only: no model is called, so the key here is a placeholder."""
        import importlib.util
        import os
        if importlib.util.find_spec("deepagents") is None:
            self.skipTest("deepagents is not installed in this Python")
        from pinpoint import deep_agent
        tools.install()
        tools.reset(building_id=BUILDING)
        old = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = old or "placeholder-not-a-real-key"
        try:
            agent = deep_agent.build_agent()
        finally:
            if old is None:
                del os.environ["ANTHROPIC_API_KEY"]
        self.assertTrue(hasattr(agent, "stream"))
        self.assertEqual({sa["name"] for sa in deep_agent.build_subagents()},
                         {"records", "locator", "site", "router", "verifier"})

    def test_reply_parsing(self):
        p = tools._parse_reply
        self.assertIs(p("near_elevator", "yeah I can see them", [True, False]), True)
        self.assertIs(p("near_elevator", "no, nothing like that", [True, False]), False)
        self.assertIsNone(p("near_elevator", "umm", [True, False]))
        self.assertEqual(p("floor", "we're on the second floor", ["Floor 1", "Floor 2"]), "Floor 2")
        self.assertEqual(p("floor", "14", ["Floor 13", "Floor 14"]), "Floor 14")
        self.assertEqual(p("room_type", "in a hallway", ["Hallway", "Office"]), "Hallway")
        self.assertEqual(p("room_type", "an elevator lobby I think", ["lobby", "elevator_lobby", "corridor"]), "elevator_lobby")
        self.assertEqual(p("room_type", "the lobby", ["lobby", "elevator_lobby"]), "lobby")


@unittest.skipUnless(shutil.which("node") and (ROOT / "app" / "local-server.mjs").exists(), "console not available")
class WithConsole(_Base):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name) / "console"
        (root / "data").mkdir(parents=True)
        (root / "data" / "catalog.json").write_text(json.dumps({"properties": [
            {"id": BUILDING, "name": "7900 Fannin", "address": "7900 Fannin St, Houston, TX", "city": "Houston",
             "models": [], "unknowns": []}]}))
        console_export.write_scan(BUILDING, out=root / "data" / f"{BUILDING}-scan.json")
        script = ("import {createLocalServer} from %s;"
                  "const s=createLocalServer({root:%s,stateDirectory:%s});"
                  "s.listen(0,'127.0.0.1',()=>console.log(s.address().port));"
                  % (json.dumps((ROOT / "app" / "local-server.mjs").as_uri()), json.dumps(str(root)),
                     json.dumps(str(root / "state"))))
        self.proc = subprocess.Popen(["node", "--input-type=module", "-e", script], stdout=subprocess.PIPE, text=True)
        self.console = Console(f"http://127.0.0.1:{int(self.proc.stdout.readline())}")

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        super().tearDown()

    def test_console_shows_the_incident_and_takes_the_route(self):
        res = run.run_incident(BUILDING, None, "collapsed in the lobby", simulate_match=TARGET,
                               console=self.console, auto_approve=True)
        inc = self.console.get(res["console_incident"])
        self.assertEqual(inc["status"], "approved")
        self.assertTrue(inc["packetId"])
        self.assertIn("## Enter here", inc["packetMarkdown"])
        self.assertIn("packet lines trace to a source", inc["notes"])
        self.assertTrue(inc["notes"].startswith("AUTO-APPROVED by the run"))     # no dispatcher was involved
        self.assertIn(f"/assets/real/live/{inc['id']}/route.jpg", inc["packetMarkdown"])
        self.assertNotIn("**Status:**", inc["packetMarkdown"])   # the console shows its own status
        texts = [e["text"] for e in inc["events"]]
        self.assertTrue(any(t.startswith("Packet updated") for t in texts))
        self.assertFalse(any(t.startswith("Dispatcher edited") for t in texts))
        sr = inc["spatialRoute"]
        best = A.best_entry(BUILDING, TARGET, "foot")
        self.assertEqual((sr["status"], sr["updatedBy"], sr["entryId"]), ("ready", "agent", best["entrance_id"]))
        self.assertEqual(sr["destination"], TARGET)
        roles = [m["role"] for m in inc["messages"]]
        self.assertIn("dispatcher", roles)                       # the agent's question
        self.assertTrue(any("simulated caller" in m["text"] for m in inc["messages"]))

    def _run_in_thread(self, **kw):
        box = {}
        t = threading.Thread(target=lambda: box.update(run.run_incident(
            BUILDING, None, position=TARGET, console=self.console, ask="simulate", approve_timeout_s=30, **kw)))
        t.start()
        end = time.time() + 20
        cid = None
        while time.time() < end:                                 # wait for the draft to reach the console
            rows = self.console.incidents()
            if rows and "trace to a source" in (self.console.get(rows[0]["id"]).get("notes") or ""):
                cid = rows[0]["id"]
                break
            time.sleep(0.2)
        self.assertIsNotNone(cid, "the draft never reached the console")
        return t, box, cid

    def test_waits_for_the_dispatcher_to_approve(self):
        t, box, cid = self._run_in_thread()
        self.assertTrue(t.is_alive(), "the agent did not wait at the approval gate")
        self.console.decide(cid, "approved")
        t.join(timeout=20)
        self.assertEqual(box["decision"], "approve")
        self.assertEqual(box["console_decision"]["decision"], "approved")
        self.assertEqual(self.console.get(cid)["status"], "approved")   # nothing was written after approval

    def test_caller_answers_in_the_console(self):
        box = {}
        t = threading.Thread(target=lambda: box.update(run.run_incident(
            BUILDING, None, simulate_match=TARGET, console=self.console, ask="console", ask_timeout_s=20,
            approve_timeout_s=30)))
        t.start()
        end, cid, asked = time.time() + 20, None, None
        while time.time() < end and asked is None:               # wait for the agent's question
            rows = self.console.incidents()
            if rows:
                cid = rows[0]["id"]
                asked = next((m for m in self.console.get(cid)["messages"]
                              if m["role"] == "dispatcher" and m["text"].startswith("Pinpoint asks")), None)
            time.sleep(0.2)
        self.assertIsNotNone(asked, "the agent never asked the caller anything")
        time.sleep(1.2)                                          # let the agent start listening
        self.console.message(cid, "caller", "no I can't see any")
        end = time.time() + 20
        while time.time() < end and "trace to a source" not in (self.console.get(cid).get("notes") or ""):
            time.sleep(0.2)
        self.console.decide(cid, "approved")
        t.join(timeout=20)
        self.assertEqual(box.get("decision"), "approve")
        first = tools.SESSION["asked"][0]
        self.assertEqual(first["source"], "caller, typed in the console")
        self.assertIs(first["raw"], False)

    def test_watcher_runs_new_incidents_once_and_leaves_handled_ones_alone(self):
        try:
            done = run.run_incident(BUILDING, None, position=TARGET, console=self.console, auto_approve=True)
            rev = self.console.get(done["console_incident"])["revision"]
            photo = Path(self.tmp.name) / "caller.jpg"
            from PIL import Image
            Image.new("RGB", (64, 48), "gray").save(photo)
            fresh = self.console.create(BUILDING, "help", str(photo))["id"]
            run.watch(self.console, once=True, auto_approve=True, position=TARGET)
            self.assertEqual(self.console.get(done["console_incident"])["revision"], rev)   # untouched
            self.assertEqual(self.console.get(fresh)["status"], "approved")
            rev2 = self.console.get(fresh)["revision"]
            run.watch(self.console, once=True, auto_approve=True, position=TARGET)
            self.assertEqual(self.console.get(fresh)["revision"], rev2)                     # not run twice
        finally:
            pass

    def test_a_run_tells_the_watcher_it_has_the_incident(self):
        photo = Path(self.tmp.name) / "caller.jpg"
        from PIL import Image
        Image.new("RGB", (64, 48), "gray").save(photo)
        res = run.run_incident(BUILDING, str(photo), position=TARGET, console=self.console, leave_for_review=True)
        rev = self.console.get(res["console_incident"])["revision"]
        run.watch(self.console, once=True, auto_approve=True, position=TARGET)      # must leave it alone
        self.assertEqual(self.console.get(res["console_incident"])["revision"], rev)

    def test_leave_for_review_drafts_and_does_not_wait(self):
        res = run.run_incident(BUILDING, None, "example", position=TARGET, console=self.console,
                               leave_for_review=True)
        inc = self.console.get(res["console_incident"])
        self.assertEqual(inc["status"], "review")                # still open for a dispatcher
        self.assertIn("## Enter here", inc["packetMarkdown"])
        self.assertEqual(res["console_decision"]["decision"], "left for review")
        self.assertNotIn("APPROVED", (Path(res["dir"]) / "packet.md").read_text().split("\n")[2])

    def test_model_run_waits_for_the_dispatcher_in_the_console(self):
        from pinpoint import budget, deep_agent
        saved = (deep_agent.run_real_agent, deep_agent._ensure_api_key, deep_agent.build_agent, budget.check_budget,
                 run.INCIDENTS)
        model = _ScriptedModel()
        deep_agent.run_real_agent, deep_agent._ensure_api_key = model, (lambda: "placeholder")
        deep_agent.build_agent, budget.check_budget = (lambda *a, **k: None), (lambda: (5.0, False))
        run.INCIDENTS = orchestrator.INCIDENTS
        try:
            box = {}
            t = threading.Thread(target=lambda: box.update(run.run_llm_incident(
                BUILDING, None, position=TARGET, console=self.console, ask="simulate", approve_timeout_s=30)))
            t.start()
            end, cid = time.time() + 20, None
            while time.time() < end and cid is None:
                rows = self.console.incidents()
                if rows and "trace to a source" in (self.console.get(rows[0]["id"]).get("notes") or ""):
                    cid = rows[0]["id"]
                time.sleep(0.2)
            self.assertIsNotNone(cid, "the packet never reached the console")
            self.assertTrue(t.is_alive(), "the run did not wait at the approval gate")
            inc = self.console.get(cid)
            self.assertIn("with a model in charge", inc["notes"])
            self.assertIn("Position chosen by the agent's model", inc["packetMarkdown"])
            self.assertEqual(inc["spatialRoute"]["destination"], TARGET)
            self.console.decide(cid, "approved")
            t.join(timeout=20)
            self.assertEqual((box["decision"], model.decisions), ("approve", ["approve", "reject"]))
            self.assertEqual(self.console.get(cid)["status"], "approved")
        finally:
            (deep_agent.run_real_agent, deep_agent._ensure_api_key, deep_agent.build_agent, budget.check_budget,
             run.INCIDENTS) = saved

    def test_corrected_room_is_recorded_after_rejection(self):
        old_buildings = learning.BUILDINGS
        learning.BUILDINGS = Path(self.tmp.name) / "buildings"
        try:
            t, box, cid = self._run_in_thread()
            self.console.decide(cid, "rejected", "wrong room", corrected_room_id="Suite 1200")
            t.join(timeout=20)
            self.assertFalse(t.is_alive())
            self.assertEqual(box["decision"], "reject")
            self.assertEqual(box["console_decision"]["corrected_room_id"], "Suite 1200")
            self.assertEqual(learning.read_corrections(BUILDING)[0]["corrected"], "Suite 1200")
        finally:
            learning.BUILDINGS = old_buildings

    def test_dispatcher_rejection_stops_the_send(self):
        t, box, cid = self._run_in_thread()
        self.console.decide(cid, "rejected", "wrong wing")
        t.join(timeout=20)
        self.assertEqual(box["decision"], "reject")
        self.assertEqual(box["console_decision"]["reason"], "wrong wing")


if __name__ == "__main__":
    unittest.main()
