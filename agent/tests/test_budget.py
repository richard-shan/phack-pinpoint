"""Tests for the $5 spend cap (agent/pinpoint/budget.py) and the
haiku-only model defaults in agent/pinpoint/agent_spec.py.

Each test cleans up its state file in tearDown so runs are isolated.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # agent/
sys.path.insert(0, str(HERE.parent))         # agent/pinpoint/ parent

from agent.pinpoint import budget as budget_mod  # noqa: E402


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmp.name) / ".pinpoint_cost.json"
        os.environ["PINPOINT_COST_STATE_PATH"] = str(self._state_path)
        # Clear any lingering state
        if self._state_path.exists():
            self._state_path.unlink()

    def tearDown(self) -> None:
        os.environ.pop("PINPOINT_COST_STATE_PATH", None)
        self._tmp.cleanup()

    def test_estimate_cost_haiku(self):
        # 1M input tokens on haiku = $1.00; 1M output tokens on haiku = $5.00; sum = $6.00
        cost = budget_mod.estimate_cost(1_000_000, 1_000_000, "anthropic:claude-haiku-5-5")
        self.assertAlmostEqual(cost, 6.00, places=4)
        # Under the cap at smaller sizes
        small = budget_mod.estimate_cost(10_000, 10_000, "claude-haiku-5-5")
        self.assertAlmostEqual(small, 0.06, places=4)

    def test_record_persists(self):
        s1 = budget_mod.record_usage(100_000, 100_000, "anthropic:claude-haiku-5-5")
        self.assertAlmostEqual(s1.cumulative_usd, 0.60, places=4)
        self.assertEqual(s1.runs, 1)

        s2 = budget_mod.record_usage(100_000, 100_000, "anthropic:claude-haiku-5-5")
        self.assertAlmostEqual(s2.cumulative_usd, 1.20, places=4)
        self.assertEqual(s2.runs, 2)

        loaded = budget_mod.load_state()
        self.assertAlmostEqual(loaded.cumulative_usd, 1.20, places=4)
        self.assertEqual(loaded.runs, 2)

    def test_check_budget_exceeded(self):
        # Pre-populate to $4.99 via record_usage with a controlled model/token count.
        # 1M input tokens on haiku = $1.00, so 4_990_000 input tokens = $4.99.
        budget_mod.record_usage(4_990_000, 0, "anthropic:claude-haiku-5-5")
        remaining, exceeded = budget_mod.check_budget()
        self.assertFalse(exceeded)
        self.assertAlmostEqual(remaining, 0.01, places=4)

        # Push over: add $0.02 (20_000 input tokens on haiku = $0.02).
        budget_mod.record_usage(20_000, 0, "anthropic:claude-haiku-5-5")
        remaining, exceeded = budget_mod.check_budget()
        self.assertTrue(exceeded)
        self.assertLess(remaining, 0.0)

    def test_leaf_model_name(self):
        self.assertEqual(budget_mod.leaf_model_name("anthropic:claude-haiku-5-5"), "haiku-5-5")
        self.assertEqual(budget_mod.leaf_model_name("claude-sonnet-5-5"), "sonnet-5-5")
        self.assertEqual(budget_mod.leaf_model_name(""), "")

    def test_unknown_model_uses_opus_rate(self):
        # Unknown model should fail-safe to the most expensive rate.
        cost = budget_mod.estimate_cost(1_000_000, 1_000_000, "some-future-model")
        # opus: $15 input + $75 output per MTok
        self.assertAlmostEqual(cost, 90.00, places=4)


class ModelDefaultsTests(unittest.TestCase):
    def test_model_defaults_are_haiku(self):
        # Unset env overrides, reload agent_spec, confirm all three defaults are haiku.
        patch_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("PINPOINT_") or not k.endswith("_MODEL")
        }
        # Explicitly drop the three override keys.
        patch_env.pop("PINPOINT_COMMANDER_MODEL", None)
        patch_env.pop("PINPOINT_WORKER_MODEL", None)
        patch_env.pop("PINPOINT_LOCATOR_MODEL", None)

        with mock.patch.dict(os.environ, patch_env, clear=True):
            from agent.pinpoint import agent_spec  # noqa: F401
            importlib.reload(agent_spec)
            self.assertIn("haiku", agent_spec.COMMANDER_MODEL.lower())
            self.assertIn("haiku", agent_spec.WORKER_MODEL.lower())
            self.assertIn("haiku", agent_spec.LOCATOR_MODEL.lower())


if __name__ == "__main__":
    unittest.main()
