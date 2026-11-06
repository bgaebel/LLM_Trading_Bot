"""Spec 004 AK8: fee and slippage come from configuration, bounded, and every trade stores its
slippage."""

import math
from pathlib import Path

import pytest

from llm_trading_bot.market.models import Quote, Symbol
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import Costs, record_decision
from llm_trading_bot.strategy.signals import Signal


def test_defaults_are_the_arena_code_values() -> None:
    assert Costs() == Costs(0.0025, 0.0005)
    assert Costs.from_env({}) == Costs(0.0025, 0.0005)


def test_values_are_read_from_the_environment() -> None:
    assert Costs.from_env({"TAKER_FEE": "0.0015", "SLIPPAGE": "0.0003"}) == Costs(0.0015, 0.0003)


@pytest.mark.parametrize("value", [0.0, 0.05])
def test_range_limits_are_inclusive(value: float) -> None:
    Costs(value, value)


@pytest.mark.parametrize("value", [-0.0001, 0.0501, math.nan, math.inf])
@pytest.mark.parametrize("field", ["TAKER_FEE", "SLIPPAGE"])
def test_values_outside_range_are_refused(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        Costs.from_env({field: repr(value)})


def test_not_a_number_text_is_refused() -> None:
    with pytest.raises(ValueError):
        Costs.from_env({"SLIPPAGE": "five"})


def test_trade_stores_and_applies_the_configured_costs(tmp_path: Path) -> None:
    con = open_db(tmp_path / "bot.db", agents=("trend",))
    qs = {Symbol.BTCEUR: Quote(60_000.0, 60_010.0), Symbol.ETHEUR: Quote(3_000.0, 3_001.0)}
    try:
        record_decision(
            con,
            "trend",
            Symbol.BTCEUR,
            3600,
            "TREND",
            Signal("BUY", 0.45, 0.7, "test", "trend"),
            {},
            costs=Costs(0.001, 0.003),
            now="2026-11-06T06:00:00+00:00",
            qs=qs,
        )
        trade = con.execute("SELECT side, fill, notional, fee, slip_rate FROM trades").fetchone()
        assert trade["side"] == "BUY"
        assert trade["slip_rate"] == 0.003
        assert trade["fill"] == pytest.approx(60_010.0 * 1.003)
        assert trade["fee"] == pytest.approx(trade["notional"] * 0.001)
    finally:
        con.close()
