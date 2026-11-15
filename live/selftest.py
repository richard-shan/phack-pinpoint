"""End-to-end self-check of the caller flow, on a scratch console that the live queue never sees.

    .venv/bin/python -m live.selftest                       # Rabinowitz Hall demo photo
    .venv/bin/python -m live.selftest --photo demo-tagged/x.jpg --building princeton-rabinowitz

One call goes through the real code, timed at each step:
  1. a photo with the caller's location tag is shrunk and re-encoded the way iMessage does it
  2. the messaging bridge reads the tag, opens the incident and sends the first reply
  3. the agent locates the caller, posts a first route before its first question, answers
     come from a stand-in caller ("not sure"), and the final route and 3D route are written
  4. the dispatcher confirms: the caller gets one closure text and no document
Prints PASS/FAIL per check and exits non-zero on any failure. Leaves nothing behind.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _console(state_dir):
    script = ("import { createLocalServer } from './app/local-server.mjs';"
              "const s = createLocalServer({stateDirectory: process.argv[1]});"
              "s.listen(0, '127.0.0.1', () => console.log(s.address().port));")
    node = shutil.which("node") or str(Path.home() / ".local" / "node" / "bin" / "node")   # gpu.sh's install
    proc = subprocess.Popen([node, "--input-type=module", "-e", script, str(state_dir)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, f"http://localhost:{proc.stdout.readline().strip()}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--building", default="princeton-rabinowitz")
    ap.add_argument("--photo", type=Path, default=ROOT / "demo" / "princeton-rabinowitz_haipxt.jpg")
    ap.add_argument("--say", default="My friend collapsed and isn't responding")
    ap.add_argument("--answer", default="not sure", help="what the stand-in caller replies to each question")
    a = ap.parse_args(argv)

    import cv2
    from perception.geomark import embed
    from live.console_api import Console, save_photo
    from live.geotag import building_locations
    from live.telegram import Bridge
    from live import tools

    results, timings = [], {}

    def check(name, ok, detail=""):
        results.append((bool(ok), name, detail))

    tmp = Path(tempfile.mkdtemp(prefix="pinpoint-selftest-"))
    proc, base = _console(tmp / "state")
    console, cid = Console(base), None
    try:
        # 1. tagged photo, re-encoded like a messaging app
        loc = building_locations()[a.building]
        img = cv2.imread(str(a.photo))
        if img is None:
            raise SystemExit(f"cannot read {a.photo}")
        tagged = embed(img, loc["lat"], loc["lon"])
        h, w = tagged.shape[:2]
        s = min(1, 1280 / max(h, w))
        tagged = cv2.resize(tagged, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        sent_photo = tmp / "sent.jpg"
        cv2.imwrite(str(sent_photo), tagged, [cv2.IMWRITE_JPEG_QUALITY, 75])

        # 2. bridge: tag -> incident -> first reply
        class Phone:
            def __init__(self):
                self.texts, self.documents = [], []

            def send(self, chat, text):
                self.texts.append((time.time(), text))

            def photo(self, file_id):
                return sent_photo.read_bytes()

        class Line(Bridge):
            def channel(self, chat, result):
                return dict(super().channel(chat, result), platform="imessage", handle="+1 (555) 010-0100")

        phone = Phone()
        bridge = Line(phone, console, tmp / "bridge.json",
                      detector=lambda path, buildings: {"status": "unknown", "reason": "untagged"})
        t0 = time.time()
        bridge.handle({"message": {"message_id": 1, "chat": {"id": "selftest", "type": "private"},
                                   "caption": a.say, "photo": [{"file_id": "x", "width": 1, "height": 1}]}})
        while bridge.state["chats"]["selftest"]["photos"]["1"].get("pending"):
            bridge.finish_jobs()
        cid = bridge.state["chats"]["selftest"]["photos"]["1"].get("incident")
        timings["text -> first reply"] = (phone.texts[0][0] - t0) if phone.texts else None
        check("location tag survives re-encoding and opens the incident", cid)
        if not cid:
            return report(results, timings)
        inc = console.get(cid)
        check("incident is at the tagged building", inc["propertyId"] == a.building, inc["propertyId"])
        check("first reply says help is on the way",
              phone.texts and "Help is on the way" in phone.texts[0][1])
        check("first reply in under 3 s", (timings["text -> first reply"] or 99) < 3,
              f"{timings['text -> first reply']:.2f} s" if timings["text -> first reply"] else "")

        # 3. agent, with a stand-in caller answering every question
        stop = threading.Event()

        def caller():
            answered = set()
            while not stop.is_set():
                bridge.relay()                    # the bridge loop forwards each question as it is asked
                for m in console.get(cid)["messages"]:
                    if m["role"] == "dispatcher" and m["text"].startswith("Pinpoint asks") and m["id"] not in answered:
                        answered.add(m["id"])
                        time.sleep(0.5)
                        console.message(cid, "caller", a.answer)
                time.sleep(0.3)
        threading.Thread(target=caller, daemon=True).start()
        from live.run import run_incident
        t1 = time.time()
        run_incident(a.building, save_photo(console.get(cid), tmp / "agent"), a.say, console=console,
                     console_incident=cid, ask="console", ask_timeout_s=20, leave_for_review=True)
        stop.set()
        time.sleep(0.5)
        bridge.relay()
        timings["agent: photo -> final route"] = time.time() - t1
        questions_on_phone = sum(t.startswith("Pinpoint asks") for _, t in phone.texts)
        inc = console.get(cid)
        routes = [e["at"] for e in inc["events"] if e["type"] == "route"]
        asks = [m["at"] for m in inc["messages"] if m["text"].startswith("Pinpoint asks")]
        first_route = min(routes) if routes else None
        if routes:
            timings["text -> first route"] = (_t(first_route) - _t(inc["createdAt"]))
        check("agent planned a route", inc.get("spatialRoute", {}).get("status") == "ready")
        check("route is up before the first question" if asks else "route is up (no questions were needed)",
              routes and (not asks or first_route <= min(asks)),
              f"{len(asks)} question(s), {len(routes)} route update(s)")
        check("every question reached the caller's phone", questions_on_phone == len(asks),
              f"{questions_on_phone}/{len(asks)}")
        check("route uses no elevators",
              not any(s.get("kind") == "elevator" for s in inc.get("spatialRoute", {}).get("segments", [])))
        check("caller location filled in", inc.get("location"), inc.get("location", "")[:80])
        check("3D model route written", (tools.ASSETS / cid / "route3d.json").exists())

        # 4. dispatcher confirms
        before = len(phone.texts)
        console.decide(cid, "approved")
        bridge.relay()
        new = [t for _, t in phone.texts[before:]]
        check("caller gets one closure text on confirm",
              len(new) == 1 and "Responders have your exact location" in new[0], f"{len(new)} text(s)")
        bridge.relay()
        check("closure text is not repeated", len(phone.texts) == before + 1)
        check("no document sent to the caller", not hasattr(phone, "send_packet") and not phone.documents)
        bridge.pool.shutdown(wait=True)
    finally:
        proc.terminate()
        proc.communicate(timeout=5)
        if cid:
            shutil.rmtree(tools.ASSETS / cid, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    return report(results, timings)


def _t(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def report(results, timings) -> int:
    print("\nPinpoint self-check")
    for ok, name, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    for k, v in timings.items():
        if v is not None:
            print(f"  time  {k}: {v:.1f} s")
    failed = sum(not ok for ok, _, _ in results)
    print(f"\n{'ALL CHECKS PASSED' if not failed else f'{failed} CHECK(S) FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
