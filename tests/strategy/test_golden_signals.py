"""ADR-0007 / Spec 003 AK2: all five strategies identical to Arena v0.3.1."""

from typing import Any

import pytest

from golden import assert_same, golden, scenarios, snapshot_for
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.signals import STRATEGIES, STRATEGY_VERSION, run_strategy


def test_weights_cover_flat_partial_and_full_position() -> None:
    assert golden()["meta"]["weights"] == [0.0, 0.25, 0.45]


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["params"]["name"])
def test_signals_match_arena(case: dict[str, Any]) -> None:
    snap = snapshot_for(case["params"])
    ind = indicators(snap, case["roundtrip_cost_frac"])
    for weight, expected in case["signals"].items():
        for name in STRATEGIES:
            sig = run_strategy(name, snap, ind, float(weight))
            actual = {
                "action": sig.action,
                "target_fraction": sig.target_fraction,
                "confidence": sig.confidence,
                "reason": sig.reason,
            }
            assert_same(actual, expected[name], f"{case['params']['name']}@{weight}/{name}")
            assert sig.strategy == name
            assert sig.version == STRATEGY_VERSION
