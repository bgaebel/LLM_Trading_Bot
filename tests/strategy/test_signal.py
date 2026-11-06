"""Spec 003 AK5: signals are a validated type; unknown strategies are an error."""

import math

import pytest

from golden import scenarios, snapshot_for
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.signals import Signal, run_strategy


def valid(**changes: object) -> Signal:
    base: dict[str, object] = {
        "action": "BUY",
        "target_fraction": 0.45,
        "confidence": 0.7,
        "reason": "test",
        "strategy": "trend",
    }
    base.update(changes)
    return Signal(**base)  # type: ignore[arg-type]


def test_valid_signal_passes_and_carries_version() -> None:
    sig = valid()
    sig.validate()
    assert sig.version == "v0.3.1"


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "SHORT"},
        {"target_fraction": 0.3},
        {"target_fraction": 1.0},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"confidence": math.nan},
        {"reason": "  "},
        {"strategy": ""},
        {"version": ""},
    ],
)
def test_invalid_signal_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        valid(**changes).validate()


def test_unknown_strategy_rejected() -> None:
    case = scenarios()[0]
    snap = snapshot_for(case["params"])
    with pytest.raises(ValueError, match="unknown strategy"):
        run_strategy("gpt", snap, indicators(snap, case["roundtrip_cost_frac"]))
