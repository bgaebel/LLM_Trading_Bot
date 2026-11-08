"""Spec 020 AK3: the USDC depots run the v0.3.1 rules unchanged; only the cost of their BTC is
that of the route - 2 * (0.05 + 0.05 + 0.10 + 0.01) % plus the spreads of BTC-USDC and USDC-EUR
instead of 2 * (0.25 + 0.05) % plus the BTC-EUR spread. Every decision carries v0.3.1-usdc."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fakes import USDC_EUR, UsdcScenarioClient
from golden import scenarios, snapshot_for
from golden_scenarios import NOW
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.market.models import Quote, Symbol
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.observations import ObservationBuffer
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip
from llm_trading_bot.strategy.signals import run_strategy

BTC_USDC = Quote(73_500.0, 73_515.0)


def test_route_cost_is_042_percent_plus_both_spreads() -> None:
    qs = {Symbol.BTCUSDC: BTC_USDC, Symbol.USDCEUR: USDC_EUR}
    spreads = 15 / 73_507.5 + 0.0001 / 0.89755  # (ask - bid) / mid of each book
    assert usdc.roundtrip_cost(qs) == pytest.approx(0.0042 + spreads, rel=1e-12)
    route = 2 * (0.0005 + 0.0005 + 0.0010 + 0.0001)
    assert route == pytest.approx(0.0042, rel=1e-12)
    # v0.3.1 on BTC-EUR: 0.60 % plus its spread.
    assert cost_roundtrip(1.0, 0.0025, 0.0005) == pytest.approx(0.0061, rel=1e-12)


def test_the_route_cost_alone_can_turn_hold_into_buy() -> None:
    """Breakout buys only if the 1h ATR moves four times the round trip: at 0.13 % ATR the
    route (≈ 0.44 %) clears it, BTC-EUR (≈ 0.60 %) does not. Same rule, other cost."""
    snap = snapshot_for(next(s["params"] for s in scenarios()))
    qs = {Symbol.BTCUSDC: BTC_USDC, Symbol.USDCEUR: USDC_EUR}

    def breakout(cost: float) -> str:
        i: Any = dict(indicators(snap, cost))
        i.update(
            close_15m=i["high_48h_excl_current"] * 1.002,
            adx_1h=25.0,
            volume15_latest=2.0,
            volume15_mean20_prior=1.0,
            atr14_1h_pct=0.0013,
        )
        return run_strategy("breakout", snap, i, 0.0).action

    assert breakout(usdc.roundtrip_cost(qs)) == "BUY"
    assert breakout(cost_roundtrip(snap.quote.spread_bps, 0.0025, 0.0005)) == "HOLD"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    agents = ("trend", "trend-usdc", "breakout-usdc", BUYHOLD, usdc.BUYHOLD_USDC)
    connection = open_db(tmp_path / "bot.db", agents=agents)
    yield connection
    connection.close()


def inputs(con: sqlite3.Connection, agent: str, symbol: str) -> dict[str, Any]:
    row = con.execute(
        "SELECT inputs FROM decisions WHERE agent=? AND symbol=? AND bar_ts>=0", (agent, symbol)
    ).fetchone()
    data: dict[str, Any] = json.loads(row[0])
    return data


def test_usdc_depot_decides_btc_with_the_route_cost_and_eth_like_v031(
    con: sqlite3.Connection,
) -> None:
    client = UsdcScenarioClient()
    config = BotConfig(("trend",), None, ("trend-usdc",))
    obs = ObservationBuffer()
    report = paper_cycle(con, client, config, costs=Costs(), clock=lambda: NOW, observations=obs)
    assert report.complete

    btc, twin = inputs(con, "trend-usdc", "BTCEUR"), inputs(con, "trend", "BTCEUR")
    expected = usdc.roundtrip_cost(client.usdc_quotes)
    assert btc["indicators"]["roundtrip_cost_pct"] == round(expected * 100, 5)
    assert twin["indicators"]["roundtrip_cost_pct"] != btc["indicators"]["roundtrip_cost_pct"]
    assert (btc["taker_fee_per_side_pct"], btc["slippage_per_side_pct"]) == (0.15, 0.06)
    # Apart from the cost, the BTC decision sees exactly what its v0.3.1 twin sees.
    cost_keys = ("taker_fee_per_side_pct", "slippage_per_side_pct")
    same = {k: v for k, v in btc.items() if k not in (*cost_keys, "indicators")}
    assert same == {k: v for k, v in twin.items() if k not in (*cost_keys, "indicators")}
    ind = {k: v for k, v in btc["indicators"].items() if k != "roundtrip_cost_pct"}
    assert ind == {k: v for k, v in twin["indicators"].items() if k != "roundtrip_cost_pct"}
    # ETH stays on ETH-EUR with the v0.3.1 costs: the very same inputs as the twin.
    assert inputs(con, "trend-usdc", "ETHEUR") == inputs(con, "trend", "ETHEUR")

    versions = {(d[1], d[2]): (d[4], d[5]) for d in obs.decisions}
    assert versions[("trend-usdc", "BTCEUR")] == ("trend", "v0.3.1-usdc")
    assert versions[("trend-usdc", "ETHEUR")] == ("trend", "v0.3.1-usdc")
    assert versions[("trend", "BTCEUR")] == ("trend", "v0.3.1")


def test_usdc_depots_follow_the_timeframe_and_rules_of_their_base(con: sqlite3.Connection) -> None:
    config = BotConfig(("trend",), None, ("trend-usdc", "breakout-usdc"))
    report = paper_cycle(con, UsdcScenarioClient(), config, costs=Costs(), clock=lambda: NOW)
    bars = {(m.agent, m.symbol): m.bar_ts for m in report.markets}
    assert bars[("trend-usdc", Symbol.BTCEUR)] == bars[("trend", Symbol.BTCEUR)]
    assert bars[("trend-usdc", Symbol.BTCEUR)] == (NOW // 3600 - 1) * 3600  # 60m like trend
    assert bars[("breakout-usdc", Symbol.BTCEUR)] == (NOW // 900 - 1) * 900  # 15m like breakout
    actions = {(m.agent, m.symbol): m.action for m in report.markets}
    assert actions[("trend-usdc", Symbol.BTCEUR)] == actions[("trend", Symbol.BTCEUR)] == "BUY"
    models = {r[0] for r in con.execute("SELECT model FROM decisions WHERE bar_ts>=0")}
    assert models == {"TREND", "TREND-USDC", "BREAKOUT-USDC"}
