"""Tests for the real deepagents wiring. Skips live-build tests when deepagents is not
importable (sandbox Python is 3.10; deepagents needs 3.11+). The subagent structure and the
API-key loader are testable without deepagents installed."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from pinpoint import deep_agent
from pinpoint.deep_agent import (
    DeepAgentsNotConfigured,
    COMMANDER_SYSTEM_PROMPT,
    COMMANDER_SYSTEM_PROMPT_NO_TODOS,
    _load_api_key_from_file,
    build_subagents,
)
from pinpoint import agent_spec
from pinpoint.agent_spec import PYDANTIC_AVAILABLE, PERMISSIONS_AVAILABLE

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
        self.assertEqual([(getattr(t, "name", None) or t.__name__) for t in rec["tools"]], ["get_property"])

    def test_locator_has_five_tools(self):
        loc = next(s for s in self.subs if s["name"] == "locator")
        self.assertEqual(
            {(getattr(t, "name", None) or t.__name__) for t in loc["tools"]},
            {"match_photo", "look", "best_question", "update_belief", "ask_caller"},
        )

    def test_site_has_detect_site_features_only(self):
        s = next(x for x in self.subs if x["name"] == "site")
        self.assertEqual([(getattr(t, "name", None) or t.__name__) for t in s["tools"]], ["detect_site_features"])

    def test_router_has_four_tools(self):
        r = next(s for s in self.subs if s["name"] == "router")
        self.assertEqual(
            {(getattr(t, "name", None) or t.__name__) for t in r["tools"]},
            {"best_entry_under_belief", "route", "hazards_along", "backup_route"},
        )

    def test_verifier_has_no_tools_and_enforces_sources(self):
        v = next(s for s in self.subs if s["name"] == "verifier")
        self.assertEqual(v["tools"], [])
        p = v["system_prompt"].lower()
        self.assertIn("source", p)
        self.assertIn("audit", p)

    def test_all_subagents_use_haiku(self):
        """Pinpoint is locked to haiku-only to stay inside the $5 spend cap.
        Env vars PINPOINT_*_MODEL still let Connor swap models for one-off
        tests, but the module defaults must remain haiku."""
        m = {s["name"]: s["model"] for s in self.subs}
        for n in ("records", "locator", "site", "router", "verifier"):
            self.assertIn("haiku", m[n].lower(),
                          f"{n} subagent must default to haiku, got {m[n]}")


class TestCommanderSystemPrompt(unittest.TestCase):

    def test_prompt_mentions_todos_parallel_approval(self):
        p = COMMANDER_SYSTEM_PROMPT.lower()
        self.assertIn("write_todos", p)
        self.assertIn("parallel", p)
        self.assertIn("verifier", p)
        self.assertIn("send_packet", p)

    def test_no_todos_variant_drops_write_todos(self):
        """The no-todos variant is used when TodoListMiddleware can't be imported.
        It must not instruct the model to call write_todos."""
        p = COMMANDER_SYSTEM_PROMPT_NO_TODOS.lower()
        self.assertNotIn("write_todos", p)
        # Still carries the same operational guarantees.
        self.assertIn("parallel", p)
        self.assertIn("verifier", p)
        self.assertIn("send_packet", p)


class TestSubagentResponseFormat(unittest.TestCase):

    @unittest.skipUnless(PYDANTIC_AVAILABLE,
                         "pydantic not installed (transitive dep of deepagents)")
    def test_every_subagent_has_response_format(self):
        for sub in build_subagents():
            with self.subTest(name=sub["name"]):
                self.assertIn("response_format", sub,
                              f"{sub['name']} subagent missing response_format")
                from pydantic import BaseModel
                self.assertTrue(
                    isinstance(sub["response_format"], type)
                    and issubclass(sub["response_format"], BaseModel),
                    f"{sub['name']}.response_format must be a BaseModel subclass",
                )

    @unittest.skipIf(PYDANTIC_AVAILABLE,
                     "pydantic is available; response_format should be attached")
    def test_response_format_omitted_when_pydantic_missing(self):
        """Graceful degradation: when pydantic is absent we skip the field rather
        than crashing on missing models."""
        for sub in build_subagents():
            with self.subTest(name=sub["name"]):
                self.assertNotIn("response_format", sub)


class TestSubagentPermissions(unittest.TestCase):

    @unittest.skipUnless(PERMISSIONS_AVAILABLE,
                         "deepagents.FilesystemPermission not importable in this env")
    def test_every_subagent_has_permissions(self):
        for sub in build_subagents():
            with self.subTest(name=sub["name"]):
                self.assertIn("permissions", sub,
                              f"{sub['name']} subagent missing permissions")
                self.assertTrue(sub["permissions"], "permissions list must not be empty")

    @unittest.skipIf(PERMISSIONS_AVAILABLE,
                     "FilesystemPermission is available; permissions should be attached")
    def test_permissions_omitted_when_fp_missing(self):
        for sub in build_subagents():
            with self.subTest(name=sub["name"]):
                self.assertNotIn("permissions", sub)


class TestBuildAgentWiring(unittest.TestCase):
    """build_agent() wires create_deep_agent with memory=['AGENTS.md'] and the right
    system prompt based on TodoListMiddleware availability. We inject a fake
    create_agent_fn to capture kwargs without needing deepagents installed."""

    def _install_fake_langgraph(self):
        """Inject a fake langgraph.checkpoint.memory module so build_agent can import
        MemorySaver without the real package installed."""
        import types
        if "langgraph.checkpoint.memory" in sys.modules:
            patcher = mock.patch.object(sys.modules["langgraph.checkpoint.memory"], "MemorySaver", type("MemorySaver", (), {}))
        else:
            fake = types.ModuleType("langgraph.checkpoint.memory")
            fake.MemorySaver = type("MemorySaver", (), {})
            patcher = mock.patch.dict(sys.modules, {"langgraph": types.ModuleType("langgraph"), "langgraph.checkpoint": types.ModuleType("langgraph.checkpoint"), "langgraph.checkpoint.memory": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _capturing_fn(self):
        calls = {}
        def fake(**kwargs):
            calls.update(kwargs)
            return mock.MagicMock(name="deep_agent_instance")
        return calls, fake

    @mock.patch.object(deep_agent, "_probe_todo_middleware")
    def test_passes_memory_agents_md(self, probe):
        probe.return_value = type("FakeTLM", (), {})
        self._install_fake_langgraph()
        calls, fake = self._capturing_fn()
        deep_agent.build_agent(create_agent_fn=fake)
        self.assertEqual(calls.get("memory"), ["AGENTS.md"])

    @mock.patch.object(deep_agent, "_probe_todo_middleware")
    def test_swaps_to_no_todos_prompt_when_middleware_missing(self, probe):
        probe.return_value = None
        self._install_fake_langgraph()
        calls, fake = self._capturing_fn()
        deep_agent.build_agent(create_agent_fn=fake)
        self.assertEqual(calls.get("system_prompt"), COMMANDER_SYSTEM_PROMPT_NO_TODOS)
        self.assertEqual(calls.get("middleware"), [])

    @mock.patch.object(deep_agent, "_probe_todo_middleware")
    def test_keeps_todos_prompt_when_middleware_present(self, probe):
        probe.return_value = type("FakeTLM", (), {})
        self._install_fake_langgraph()
        calls, fake = self._capturing_fn()
        deep_agent.build_agent(create_agent_fn=fake)
        self.assertEqual(calls.get("system_prompt"), COMMANDER_SYSTEM_PROMPT)
        self.assertEqual(len(calls.get("middleware", [])), 1)


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
            with mock.patch.object(deep_agent, "_ensure_api_key", side_effect=DeepAgentsNotConfigured("missing key")), self.assertRaises(DeepAgentsNotConfigured):
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

    def test_build_agent_stream_available(self):
        """The real agent must expose .stream() with stream_mode kwarg so run_real_agent
        can iterate updates + messages chunks for D's console."""
        os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-build-only")
        agent = deep_agent.build_agent()
        self.assertTrue(hasattr(agent, "stream"))
        import inspect
        sig = inspect.signature(agent.stream)
        self.assertIn("stream_mode", sig.parameters)


if __name__ == "__main__":
    unittest.main()
