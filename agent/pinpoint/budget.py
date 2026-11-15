"""Spend-cap state machine for real-llm runs.

Pinpoint is locked to Haiku defaults (agent_spec.py); this module enforces a
cumulative-dollar cap across runs so an unattended demo cannot overspend.

Not thread-safe across processes; single-process incident is the assumed shape.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

from .config import AGENT_DIR

_LOG = logging.getLogger("pinpoint.budget")

BUDGET_USD = float(os.environ.get("PINPOINT_BUDGET_USD", "5.00"))

# Anthropic list pricing per million tokens (USD). Figures kept conservative so
# a quiet underestimate does not blow the cap. Override via env for adjustments.
PRICES_USD_PER_MTOK = {
    "haiku": {"input": 1.00, "output": 5.00},
    "sonnet": {"input": 3.00, "output": 15.00},   # defensive; not used by default
    "opus":   {"input": 15.00, "output": 75.00},  # defensive; not used by default
}

STATE_PATH = AGENT_DIR / ".pinpoint_cost.json"


class BudgetExceeded(RuntimeError):
    """Raised when the cumulative spend has already crossed BUDGET_USD."""


@dataclass
class BudgetState:
    cumulative_usd: float
    runs: int
    last_updated: str

    @classmethod
    def zero(cls) -> "BudgetState":
        return cls(cumulative_usd=0.0, runs=0, last_updated="")


def _state_path() -> Path:
    # Allow override via env so tests can isolate state file.
    override = os.environ.get("PINPOINT_COST_STATE_PATH")
    return Path(override) if override else STATE_PATH


def load_state() -> BudgetState:
    p = _state_path()
    if not p.exists():
        return BudgetState.zero()
    try:
        data = json.loads(p.read_text())
        return BudgetState(
            cumulative_usd=float(data.get("cumulative_usd", 0.0)),
            runs=int(data.get("runs", 0)),
            last_updated=str(data.get("last_updated", "")),
        )
    except (json.JSONDecodeError, ValueError):
        _LOG.warning("budget state file corrupt; resetting to zero")
        return BudgetState.zero()


def save_state(state: BudgetState) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2))


def _price_for(model: str) -> dict:
    s = (model or "").lower()
    if "haiku" in s:
        return PRICES_USD_PER_MTOK["haiku"]
    if "sonnet" in s:
        return PRICES_USD_PER_MTOK["sonnet"]
    if "opus" in s:
        return PRICES_USD_PER_MTOK["opus"]
    # Unknown model: use the most expensive price we know of to fail-safe.
    _LOG.warning("budget: unknown model %r, charging opus rate as safety margin", model)
    return PRICES_USD_PER_MTOK["opus"]


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    p = _price_for(model)
    in_usd = (max(0, input_tokens) / 1_000_000.0) * p["input"]
    out_usd = (max(0, output_tokens) / 1_000_000.0) * p["output"]
    return round(in_usd + out_usd, 6)


def check_budget() -> Tuple[float, bool]:
    """Return (remaining_usd, exceeded). Does not mutate state."""
    state = load_state()
    remaining = round(BUDGET_USD - state.cumulative_usd, 4)
    return remaining, (state.cumulative_usd >= BUDGET_USD)


def record_usage(input_tokens: int, output_tokens: int, model: str) -> BudgetState:
    """Add this run's cost to the state file and return the new state."""
    cost = estimate_cost(input_tokens, output_tokens, model)
    state = load_state()
    state.cumulative_usd = round(state.cumulative_usd + cost, 6)
    state.runs += 1
    state.last_updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    return state


def leaf_model_name(model_id: str) -> str:
    """Extract 'haiku-5-5' from 'anthropic:claude-haiku-5-5' for pricing lookup."""
    if not model_id:
        return ""
    tail = model_id.split(":")[-1]
    tail = tail.replace("claude-", "")
    return tail
