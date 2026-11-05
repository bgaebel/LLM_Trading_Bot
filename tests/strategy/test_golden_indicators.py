"""ADR-0007 / Spec 003 AK1: indicators identical to Arena v0.3.1."""

from typing import Any

import pytest

from golden import assert_same, golden, scenarios, snapshot_for
from llm_trading_bot.market.models import Candle
from llm_trading_bot.strategy.indicators import adx, atr, ema, indicators, rsi


def test_fixture_comes_from_the_expected_arena_commit() -> None:
    meta = golden()["meta"]
    assert meta["arena_commit"] == "7563724"
    assert meta["branches_missing"] == []


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["params"]["name"])
def test_indicators_match_arena(case: dict[str, Any]) -> None:
    snap = snapshot_for(case["params"])
    assert_same(dict(indicators(snap, case["roundtrip_cost_frac"])), case["indicators"])


@pytest.mark.parametrize("case", golden()["indicator_cases"], ids=lambda c: c["fn"])
def test_indicator_edge_cases_match_arena(case: dict[str, Any]) -> None:
    fn, n = case["fn"], case["n"]
    if fn == "rsi":
        actual: Any = rsi(case["values"], n)
    elif fn == "ema":
        actual = ema(case["values"], n)
    else:
        bars = tuple(Candle(*row) for row in case["rows"])
        actual = atr(bars, n) if fn == "atr" else list(adx(bars, n))
    assert_same(actual, case["expected"])


def test_ema_without_data_raises() -> None:
    with pytest.raises(ValueError):
        ema([], 5)
    with pytest.raises(ValueError):
        ema([1.0], 0)
