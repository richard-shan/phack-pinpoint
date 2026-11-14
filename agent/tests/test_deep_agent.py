"""Tests for the real deepagents wiring. Skips live-build tests when deepagents is not
importable (sandbox Python is 3.10; deepagents needs 3.11+). The subagent structure and the
API-key loader are testable without deepagents installed."""
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from pinpoint import deep_agent
from pinpoint.deep_agent import (
    DeepAgentsNotConfigured,
    COMMANDER_SYSTEM_PROMPT,
    _load_api_key_from_file,
    build_subagents,
)

try:
    import deepagents  # noqa: F401
    DEEPAGENTS_AVAILABLE = True
except ImportError:
    DEEPAGENTS_AVAILABLE = False


class TestSubagentStructure(unittest.TestCase):

    def setUp(self):
        self.subs = build_subagents()

    def test_five_subagents(self):
        names = [s["name"] for s in self.subs]
        self.assertEqual(sorted(names),
                         ["locator", "records", "router", "site", "verifier"])

    def test_every_subagent_has_required_fields(self):
        for sub in self.subs:
            with self.subTest(name=sub["name"]):
                for field in ("name", "description", "system_prompt", "tools",
                              "model", "mode"):
                    self.assertIn(field, sub)
                self.assertEqual(sub["mode"], "isolated")
                self.assertTrue(sub["system_prompt"].strip())

    def test_records_has_get_property_only(self):
        rec = next(s for s in self.subs if s["name"] == "records")
        self.assertEqual([t.__name__ for t in rec["tools"]], ["get_property"])

    def test_locator_has_five_tools(self):
        loc = next(s for s in self.subs if s["name"] == "locator")
        self.assertEqual(
            {t.__name__ for t in loc["tools"]},
            {"match_photo", "look", "best_question", "update_belief", "ask_caller"},
        )

    def test_site_has_detect_site_features_only(self):
        s = next(x for x in self.subs if x["name"] == "site")
        self.assertEqual([t.__name__ for t in s["tools"]], ["detect_site_features"])

    def test_router_has_four_tools(self):
        r = next(s for s in self.subs if s["name"] == "router")
        self.assertEqual(
            {t.__name__ for t in r["tools"]},
            {"best_entry_under_belief", "route", "hazards_along", "backup_route"},
        )

    def test_verifier_has_no_tools_and_enforces_sources(self):
        v = next(s for s in self.subs if s["name"] == "verifier")
        self.assertEqual(v["tools"], [])
        p = v["system_prompt"].lower()
        self.assertIn("source", p)
        self.assertIn("audit", p)

    def test_locator_uses_sonnet_others_haiku(self):
        m = {s["name"]: s["model"] for s in self.subs}
        self.assertIn("sonnet", m["locator"].lower())
        for n in ("records", "site", "router", "verifier"):
            self.assertIn("haiku", m[n].lower())


class TestCommanderSystemPrompt(unittest.TestCase):

    def test_prompt_mentions_todos_parallel_approval(self):
        p = COMMANDER_SYSTEM_PROMPT.lower()
        self.assertIn("write_todos", p)
        self.assertIn("parallel", p)
        self.assertIn("verifier", p)
        self.assertIn("send_packet", p)


class TestApiKeyLoading(unittest.TestCase):

    def test_missing_file_returns_none(self):
        self.assertIsNone(_load_api_key_from_file(Path("/tmp/_nope_pp_api.txt")))

    def test_labeled_line_colon(self):
        tmp = Path("/tmp/_pp_api_1.txt")
        tmp.write_text("Vexcel\napi_key\nvfa_abc\nanthropic_api_key: sk-ant-FAKE123\n")
        try:
            self.assertEqual(_load_api_key_from_file(tmp), "sk-ant-FAKE123")
        finally:
            tmp.unlink()

    def test_labeled_line_env_shape(self):
        tmp = Path("/tmp/_pp_api_2.txt")
        tmp.write_text("ANTHROPIC_API_KEY=sk-ant-ENVSHAPE9\n")
        try:
            self.assertEqual(_load_api_key_from_file(tmp), "sk-ant-ENVSHAPE9")
        finally:
            tmp.unlink()

    def test_bare_sk_ant_fallback(self):
        tmp = Path("/tmp/_pp_api_3.txt")
        tmp.write_text("hdr\nprose with sk-ant-LOOSEKEY inside\n")
        try:
            self.assertEqual(_load_api_key_from_file(tmp), "sk-ant-LOOSEKEY")
        finally:
            tmp.unlink()

    def test_no_match_returns_none(self):
        tmp = Path("/tmp/_pp_api_4.txt")
        tmp.write_text("Vexcel\napi_key\nvfa_abc\n")
        try:
            self.assertIsNone(_load_api_key_from_file(tmp))
        finally:
            tmp.unlink()


class TestRunRealAgentGraceful(unittest.TestCase):

    def test_raises_when_key_and_deepagents_missing(self):
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            # api.txt in this repo has no anthropic key, so _ensure_api_key raises first.
            with self.assertRaises(DeepAgentsNotConfigured):
                deep_agent.run_real_agent(
                    {"address": "1 Main St", "caller_photo_path": "x", "caller_text": ""},
                    Path("/tmp/_pp_incident_x"),
                )
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved


@unittest.skipUnless(DEEPAGENTS_AVAILABLE,
                     "deepagents not installed (sandbox Python < 3.11)")
class TestLiveBuild(unittest.TestCase):

    def test_build_agent_returns_something(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-build-only")
        agent = deep_agent.build_agent()
        self.assertIsNotNone(agent)


if __name__ == "__main__":
    unittest.main()
