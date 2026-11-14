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
from pinpoint import orchestrator, stubs

ROOT = Path(__file__).resolve().parent.parent.parent
BUILDING = "6550930"                       # 7900 Fannin St: lobby scan, four doors, 17 health care tenants
TARGET = "9nydrbq3ga21s4wq6cdairckc"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_incidents, self.old_assets = orchestrator.INCIDENTS, tools.ASSETS
        orchestrator.INCIDENTS = Path(self.tmp.name) / "incidents"
        orchestrator.INCIDENTS.mkdir()
        tools.ASSETS = Path(self.tmp.name) / "assets"

    def tearDown(self):
        tools.uninstall()
        orchestrator.INCIDENTS, tools.ASSETS = self.old_incidents, self.old_assets
        self.tmp.cleanup()

    def files(self, res):
        d = Path(res["dir"])
        return {p.stem: (json.loads(p.read_text()) if p.suffix == ".json" else p.read_text())
                for p in d.iterdir() if p.suffix in (".json", ".md")}


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
        self.assertIn("supplied to this run, not matched from the photo", f["packet"])
        picture = Path(res["dir"]) / "route.jpg"
        self.assertTrue(picture.exists() and picture.stat().st_size > 20000)
        self.assertIn("Route on the floor plan", f["packet"])

    def test_question_loop_uses_graph_attributes(self):
        res = run.run_incident(BUILDING, None, position=TARGET, auto_approve=True)
        loc = self.files(res)["location"]
        self.assertGreaterEqual(loc["questions_asked"], 1)
        asked = tools.SESSION["asked"][0]
        self.assertIn(asked["attribute"], tools.QUESTIONS)
        self.assertTrue(asked["source"].startswith("simulated caller"))

    def test_unknown_photo_degrades_instead_of_crashing(self):
        res = run.run_incident(BUILDING, str(Path(self.tmp.name) / "missing.jpg"), auto_approve=True)
        f = self.files(res)
        self.assertEqual(f["audit"]["status"], "DEGRADED")
        self.assertIn("DEGRADED", f["packet"])
        self.assertIn("south plaza doors", f["packet"])          # falls back to the front door by name

    def test_install_and_uninstall(self):
        before = stubs.match_photo
        tools.install()
        self.assertIsNot(stubs.match_photo, before)
        tools.uninstall()
        self.assertIs(stubs.match_photo, before)

    def test_reply_parsing(self):
        p = tools._parse_reply
        self.assertIs(p("near_elevator", "yeah I can see them", [True, False]), True)
        self.assertIs(p("near_elevator", "no, nothing like that", [True, False]), False)
        self.assertIsNone(p("near_elevator", "umm", [True, False]))
        self.assertEqual(p("floor", "we're on the second floor", ["Floor 1", "Floor 2"]), "Floor 2")
        self.assertEqual(p("floor", "14", ["Floor 13", "Floor 14"]), "Floor 14")
        self.assertEqual(p("room_type", "in a hallway", ["Hallway", "Office"]), "Hallway")


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
        res = run.run_incident(BUILDING, None, "collapsed in the lobby", position=TARGET, console=self.console,
                               auto_approve=True)
        inc = self.console.get(res["console_incident"])
        self.assertEqual(inc["status"], "approved")
        self.assertTrue(inc["packetId"])
        self.assertIn("## Enter here", inc["packetMarkdown"])
        self.assertIn("packet lines trace to a source", inc["notes"])
        self.assertIn(f"/assets/real/live/{inc['id']}/route.jpg", inc["packetMarkdown"])
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
            BUILDING, None, position=TARGET, console=self.console, ask="console", ask_timeout_s=20,
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
        state = run.STATE
        run.STATE = Path(self.tmp.name) / "state"
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
            run.STATE = state

    def test_dispatcher_rejection_stops_the_send(self):
        t, box, cid = self._run_in_thread()
        self.console.decide(cid, "rejected", "wrong wing")
        t.join(timeout=20)
        self.assertEqual(box["decision"], "reject")
        self.assertEqual(box["console_decision"]["reason"], "wrong wing")


if __name__ == "__main__":
    unittest.main()
