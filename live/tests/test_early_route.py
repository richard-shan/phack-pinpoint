"""The crew sees a route before the caller's questions, and a reroute when an answer changes it."""
import unittest
from unittest.mock import patch

from live import tools


class FakeConsole:
    def __init__(self, replies):
        self.replies, self.routes, self.messages = list(replies), [], []

    def message(self, cid, role, text):
        self.messages.append((role, text))

    def wait_for_caller(self, cid, timeout_s=90.0):
        return self.replies.pop(0) if self.replies else None

    def route(self, cid, body):
        self.routes.append((len(self.messages), body["destination"], body["reason"]))


def fake_route_update(building, node, profile):
    return {"destination": node, "reason": f"to {node}", "nodeIds": ["e", node]}


class EarlyRoute(unittest.TestCase):
    def start(self, replies):
        self.console = FakeConsole(replies)
        tools.reset(console=self.console, console_incident="inc-x", console_scan=True,
                            building_id="b", ask="console")
        tools.SESSION["candidates"] = [
            {"node_id": "atrium", "attributes": {"room_type": "atrium", "has_exit": True}},
            {"node_id": "hall", "attributes": {"room_type": "hallway", "has_exit": False}},
        ]

    def test_route_before_first_question_then_reroute_on_answer(self):
        self.start(["hallway"])
        with patch("env.console.route_update", fake_route_update):
            tools.ask_caller("inc-x", "room_type")
        (before, first, why), (after, second, why2) = self.console.routes
        self.assertEqual((before, first), (0, "atrium"))          # posted before the question went out
        self.assertIn("Initial route", why)
        self.assertEqual(second, "hall")
        self.assertIn("Rerouted", why2)
        self.assertGreater(after, before)

    def test_dont_know_rules_nothing_out(self):
        self.start(["I don’t know"])
        with patch("env.console.route_update", fake_route_update):
            reply = tools.ask_caller("inc-x", "has_exit")
        self.assertIsNone(reply["raw"])                             # not taken as "no"
        self.assertEqual([r[1] for r in self.console.routes], ["atrium"])   # no reroute


if __name__ == "__main__":
    unittest.main()
