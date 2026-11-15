"""Tests for the LLM-facing tool wrappers.

Skipped wholesale if pydantic or langchain_core aren't installed, because
the wrappers depend on them. The deepagents real-llm path runs under a
uv-managed Python 3.11 venv where they are installed.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pydantic  # noqa: F401
    from langchain_core.tools import ToolException  # noqa: F401
    _HAS_DEPS = True
except Exception:  # pragma: no cover
    _HAS_DEPS = False


@unittest.skipUnless(_HAS_DEPS, "pydantic/langchain_core not installed (fine on stdlib-only sandboxes)")
class ArgsSchemaShape(unittest.TestCase):
    """Every wrapped tool exposes an args_schema that is a BaseModel subclass."""

    def test_every_tool_has_pydantic_args_schema(self):
        from pydantic import BaseModel
        from agent.pinpoint import tool_wrappers as tw

        expected = {
            "get_property", "match_photo", "look", "ask_caller",
            "detect_site_features", "hazards_along", "send_packet",
            "notify_contact", "best_question", "update_belief",
            "best_entry_under_belief", "route", "backup_route",
        }
        self.assertEqual(set(tw.ALL_WRAPPED_TOOLS), expected)
        for name, t in tw.ALL_WRAPPED_TOOLS.items():
            with self.subTest(tool=name):
                self.assertTrue(
                    hasattr(t, "args_schema") and t.args_schema is not None,
                    f"{name} has no args_schema",
                )
                self.assertTrue(
                    isinstance(t.args_schema, type) and issubclass(t.args_schema, BaseModel),
                    f"{name}.args_schema is not a BaseModel subclass",
                )


@unittest.skipUnless(_HAS_DEPS, "pydantic/langchain_core not installed")
class SchemaValidation(unittest.TestCase):
    """Schema validation should reject known-bad shapes with a descriptive error."""

    def test_candidate_missing_node_id_rejected(self):
        from agent.pinpoint import tool_wrappers as tw
        from pydantic import ValidationError

        with self.assertRaises(ValidationError) as cx:
            tw.best_entry_under_belief.args_schema(
                building_id="bayou-medical-plaza",
                candidates=[{"room_label": "clinic"}],
                profile="foot",
            )
        msg = str(cx.exception)
        self.assertIn("node_id", msg)

    def test_route_bad_profile_rejected(self):
        from agent.pinpoint import tool_wrappers as tw
        from pydantic import ValidationError

        with self.assertRaises(ValidationError) as cx:
            tw.route.args_schema(
                building_id="bayou-medical-plaza",
                from_node="ext_dock",
                to_node="suite210_interior",
                profile="walking",
            )
        msg = str(cx.exception)
        self.assertIn("profile", msg)

    def test_best_question_needs_two_candidates(self):
        from agent.pinpoint import tool_wrappers as tw
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            tw.best_question.args_schema(
                candidates=[{"node_id": "x", "belief": 1.0}],
            )

    def test_address_as_building_id_accepted_at_schema_layer(self):
        """The Pydantic schema accepts the string; the underlying routing fix resolves it."""
        from agent.pinpoint import tool_wrappers as tw
        parsed = tw.best_entry_under_belief.args_schema(
            building_id="1200 Smith St, Houston, TX 77002",
            candidates=[{"node_id": "suite210_interior", "belief": 1.0}],
            profile="foot",
        )
        self.assertEqual(parsed.building_id, "1200 Smith St, Houston, TX 77002")


@unittest.skipUnless(_HAS_DEPS, "pydantic/langchain_core not installed")
class ExceptionTranslation(unittest.TestCase):
    """Underlying typed failures become ToolException with a helpful message."""

    def test_matterport_unavailable_becomes_tool_exception(self):
        from unittest.mock import patch
        from agent.pinpoint import tool_wrappers as tw
        from agent.pinpoint.errors import MatterportUnavailable
        from langchain_core.tools import ToolException

        with patch(
            "agent.pinpoint.routing.best_entry_under_belief",
            side_effect=MatterportUnavailable("no graph for building xyz"),
        ):
            with self.assertRaises(ToolException) as cx:
                tw.best_entry_under_belief.invoke({
                    "building_id": "xyz",
                    "candidates": [{"node_id": "suite210_interior", "belief": 1.0}],
                    "profile": "foot",
                })
            self.assertIn("best_entry_under_belief failed", str(cx.exception))
            self.assertIn("MatterportUnavailable", str(cx.exception))

    def test_key_error_becomes_tool_exception(self):
        from unittest.mock import patch
        from agent.pinpoint import tool_wrappers as tw
        from langchain_core.tools import ToolException

        with patch(
            "agent.pinpoint.stubs.get_property",
            side_effect=KeyError("tenants"),
        ):
            with self.assertRaises(ToolException) as cx:
                tw.get_property.invoke({"address": "1200 Smith St"})
            self.assertIn("get_property failed", str(cx.exception))


@unittest.skipUnless(_HAS_DEPS, "pydantic/langchain_core not installed")
class EndToEndRouting(unittest.TestCase):
    """Address-shaped building_id resolves through the existing routing fix."""

    def test_address_resolves_to_bayou_graph(self):
        from agent.pinpoint import tool_wrappers as tw
        out = tw.best_entry_under_belief.invoke({
            "building_id": "1200 Smith St, Houston, TX 77002",
            "candidates": [{"node_id": "suite210_interior", "belief": 1.0}],
            "profile": "foot",
        })
        self.assertIn("entrance_id", out)
        self.assertIsNotNone(out["entrance_id"])


if __name__ == "__main__":
    unittest.main()
