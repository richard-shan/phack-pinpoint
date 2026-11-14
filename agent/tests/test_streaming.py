"""Spec tests for the deepagents streaming forwarder.

Runs on the sandbox Python 3.10 without deepagents installed. The agent `.stream()`
API is mocked by yielding a scripted sequence of chunks. The forwarder is the unit
under test; the production deepagents runtime is not invoked here.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_AGENT = _THIS.parent.parent
sys.path.insert(0, str(_AGENT))

from pinpoint import streaming, trace


class _AIMessageChunkLike:
    """Duck-typed stand-in for langchain_core.messages.AIMessageChunk."""
    def __init__(self, content):
        self.content = content


def _mock_stream():
    """Scripted sequence used by several tests: one update, two tokens, one interrupt,
    one more update after resume. Yields (mode, chunk) tuples the way LangGraph does
    when stream_mode is a list."""
    yield ("updates", {"records": {"brief": "ok"}})
    yield ("messages", (_AIMessageChunkLike("Delegating "), {"langgraph_node": "commander"}))
    yield ("messages", (_AIMessageChunkLike("to locator."), {"langgraph_node": "commander"}))
    yield ("updates", {"__interrupt__": [{"value": {"action_requests": [{"action": "send_packet"}]}}]})
    yield ("updates", {"verifier": {"audit": "OK"}})


class TestStreamingForwarder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.idir = Path(self.tmp.name)
        trace.start(self.idir)
        streaming.start_events_file(self.idir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_start_events_file_creates_header(self):
        """start_events_file writes a header event with kind=header, source=system."""
        lines = (self.idir / "events.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["kind"], "header")
        self.assertEqual(rec["source"], "system")
        self.assertIn("t_ms", rec)

    def test_02_forward_update_writes_to_events_and_trace(self):
        """An updates chunk appends one event line AND one trace.jsonl entry."""
        streaming.forward_chunk("updates", {"records": {"brief": "ok"}}, self.idir)
        events = (self.idir / "events.jsonl").read_text().splitlines()
        trace_lines = (self.idir / "trace.jsonl").read_text().splitlines()
        self.assertEqual(len(events), 2)  # header + update
        self.assertEqual(json.loads(events[1])["kind"], "update")
        self.assertEqual(json.loads(events[1])["source"], "records")
        self.assertTrue(any('"stream_update"' in L for L in trace_lines))

    def test_03_forward_message_token_writes_content_delta(self):
        """A messages chunk writes a token event with content_delta populated."""
        msg = _AIMessageChunkLike("Delegating ")
        streaming.forward_chunk("messages", (msg, {"langgraph_node": "commander"}), self.idir)
        events = (self.idir / "events.jsonl").read_text().splitlines()
        token = json.loads(events[-1])
        self.assertEqual(token["kind"], "token")
        self.assertEqual(token["source"], "commander")
        self.assertEqual(token["payload"]["content_delta"], "Delegating ")

    def test_04_empty_token_skipped(self):
        """An empty content delta writes nothing (token streams are noisy)."""
        before = (self.idir / "events.jsonl").read_text().splitlines()
        msg = _AIMessageChunkLike("")
        streaming.forward_chunk("messages", (msg, {"langgraph_node": "commander"}), self.idir)
        streaming.forward_chunk("messages", (None, None), self.idir)
        after = (self.idir / "events.jsonl").read_text().splitlines()
        self.assertEqual(len(after), len(before))

    def test_05_long_token_truncated(self):
        """A content delta longer than 200 chars is truncated with an ellipsis."""
        long = "x" * 500
        msg = _AIMessageChunkLike(long)
        streaming.forward_chunk("messages", (msg, {"langgraph_node": "locator"}), self.idir)
        events = (self.idir / "events.jsonl").read_text().splitlines()
        token = json.loads(events[-1])
        self.assertLessEqual(len(token["payload"]["content_delta"]), 200)
        self.assertTrue(token["payload"]["content_delta"].endswith("..."))

    def test_06_tail_events_yields_parsed_in_order(self):
        """tail_events yields every event as a parsed dict in file order."""
        streaming.forward_chunk("updates", {"records": {"brief": "ok"}}, self.idir)
        streaming.forward_chunk("messages",
                                (_AIMessageChunkLike("hi"), {"langgraph_node": "commander"}),
                                self.idir)
        kinds = [e["kind"] for e in streaming.tail_events(self.idir)]
        self.assertEqual(kinds, ["header", "update", "token"])

    def test_07_interrupt_detected_in_updates(self):
        """A chunk with __interrupt__ is detected by chunk_is_interrupt and forwards
        an update event marked interrupt=True."""
        payload = {"__interrupt__": [{"value": {"action_requests": [{"action": "send_packet"}]}}]}
        self.assertTrue(streaming.chunk_is_interrupt(payload))
        streaming.forward_chunk("updates", payload, self.idir)
        events = (self.idir / "events.jsonl").read_text().splitlines()
        last = json.loads(events[-1])
        self.assertEqual(last["source"], "__interrupt__")
        self.assertTrue(last["payload"]["interrupt"])

    def test_08_jsonl_stays_valid_across_many_writes(self):
        """Every events.jsonl line parses as JSON after a full scripted stream."""
        for mode, chunk in _mock_stream():
            streaming.forward_chunk(mode, chunk, self.idir)
        for line in (self.idir / "events.jsonl").read_text().splitlines():
            if line.strip():
                json.loads(line)  # no-op if valid, raises otherwise

    def test_09_structural_schema_each_event(self):
        """Every event has t_ms, kind, source, payload fields with correct types."""
        for mode, chunk in _mock_stream():
            streaming.forward_chunk(mode, chunk, self.idir)
        for e in streaming.tail_events(self.idir):
            self.assertIn("t_ms", e)
            self.assertIn("kind", e)
            self.assertIn("source", e)
            self.assertIn("payload", e)
            self.assertIsInstance(e["t_ms"], (int, float))
            self.assertIsInstance(e["source"], str)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("deepagents") is not None,
    "deepagents not installed; live integration skipped",
)
class TestStreamingLiveIntegration(unittest.TestCase):  # pragma: no cover
    def test_build_agent_stream_available(self):
        """When deepagents IS importable, build_agent() has a .stream() that accepts
        stream_mode=['updates','messages']. Not executed here; signature only."""
        from pinpoint import deep_agent
        agent = deep_agent.build_agent()
        self.assertTrue(hasattr(agent, "stream"))


if __name__ == "__main__":
    unittest.main()
