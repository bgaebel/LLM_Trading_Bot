"""Spec 004 AK9: one paper cycle on (fake) live data; a second run in the same bar adds nothing."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot
from llm_trading_bot.paper import cycle
from llm_trading_bot.paper.cycle import active_agent, paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.strategy.signals import Signal


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("trend", BUYHOLD))
    yield connection
    connection.close()


def counts(con: sqlite3.Connection) -> dict[str, int]:
    return {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("decisions", "trades", "equity")
    }


def test_cycle_buys_benchmark_decides_both_markets_and_stores_equity(
    con: sqlite3.Connection,
) -> None:
    report = paper_cycle(con, ScenarioClient(), "trend", costs=Costs(), clock=lambda: NOW)
    assert report.complete
    assert report.buyhold_bought == 2
    assert [(m.symbol, m.new, m.action, m.status, m.traded) for m in report.markets] == [
        (Symbol.BTCEUR, True, "BUY", "OK", True),
        (Symbol.ETHEUR, True, "BUY", "OK", True),
    ]
    # Trend decides on its own 60m bar: the last closed hour before NOW.
    assert {m.bar_ts for m in report.markets} == {(NOW // 3600 - 1) * 3600}
    assert [e.agent for e in report.equity] == ["trend", BUYHOLD]
    assert counts(con) == {"decisions": 4, "trades": 4, "equity": 2}


def test_second_cycle_in_the_same_bar_adds_no_decision_or_trade(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW)
    before = counts(con)
    report = paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW + 5)
    assert report.buyhold_bought == 0
    assert all(not m.new for m in report.markets)
    after = counts(con)
    assert (after["decisions"], after["trades"]) == (before["decisions"], before["trades"])
    assert after["equity"] == before["equity"] + 2  # one new equity point per agent and cycle


def test_failing_eth_candles_do_not_block_btc(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.fail_candles = {Symbol.ETHEUR}
    report = paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW)
    assert not report.complete
    btc, eth = report.markets
    assert (btc.symbol, btc.new, btc.traded) == (Symbol.BTCEUR, True, True)
    assert eth.symbol == Symbol.ETHEUR and "HTTP 500" in eth.error
    assert (
        con.execute(
            "SELECT COUNT(*) FROM decisions WHERE symbol='ETHEUR' AND bar_ts>=0"
        ).fetchone()[0]
        == 0
    )


def test_without_fresh_quotes_nothing_is_decided_and_the_bar_stays_open(
    con: sqlite3.Connection,
) -> None:
    client = ScenarioClient()
    client.fail_quotes = True
    report = paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW)
    assert "HTTP 503" in report.quotes_error
    assert report.markets == [] and report.equity == []
    assert counts(con) == {"decisions": 0, "trades": 0, "equity": 0}

    client.fail_quotes = False
    report = paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW + 10)
    assert all(m.new for m in report.markets)


def test_quotes_stored_by_a_recent_cycle_still_count_when_one_fetch_fails(
    con: sqlite3.Connection,
) -> None:
    client = ScenarioClient(btc="down-calm-plain-s1", eth="down-calm-plain-s1")
    paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW)
    client.fail_quotes = True
    report = paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW + 15)
    # The ticker failed, so the candles' snapshot cannot be built either: no new decision.
    assert report.equity and all(m.error for m in report.markets)


def test_sell_signal_without_position_does_not_trade(con: sqlite3.Connection) -> None:
    client = ScenarioClient(btc="down-calm-plain-s1", eth="down-calm-plain-s1")
    report = paper_cycle(con, client, "trend", costs=Costs(), clock=lambda: NOW)
    assert [(m.action, m.traded) for m in report.markets] == [("SELL", False), ("SELL", False)]


def test_halted_portfolio_takes_no_new_decision(con: sqlite3.Connection) -> None:
    con.execute("UPDATE portfolios SET halted=1 WHERE agent='trend'")
    report = paper_cycle(con, ScenarioClient(), "trend", costs=Costs(), clock=lambda: NOW)
    assert all(m.error == "portfolio halted" for m in report.markets)
    assert con.execute("SELECT COUNT(*) FROM decisions WHERE agent='trend'").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, "trend"), ({"BOT_AGENT": "meanrev"}, "meanrev"), ({"BOT_AGENT": " "}, "trend")],
)
def test_active_agent_from_environment(env: dict[str, str], expected: str) -> None:
    assert active_agent(env) == expected


@pytest.mark.parametrize("name", ["buyhold", "gpt", "TREND"])
def test_unknown_or_benchmark_agent_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="BOT_AGENT"):
        active_agent({"BOT_AGENT": name})


def test_strategy_failure_in_btc_does_not_block_eth(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_trading_bot.strategy.signals import run_strategy as real

    def flaky(name: str, snap: MarketSnapshot, *args: Any) -> Signal:
        if snap.symbol == Symbol.BTCEUR:
            raise ZeroDivisionError("broken indicator")
        return real(name, snap, *args)

    monkeypatch.setattr(cycle, "run_strategy", flaky)
    report = paper_cycle(con, ScenarioClient(), "trend", costs=Costs(), clock=lambda: NOW)
    btc, eth = report.markets
    assert btc.error == "ZeroDivisionError: broken indicator"
    assert (eth.new, eth.traded) == (True, True)
    assert not report.complete and len(report.equity) == 2
    assert con.execute("SELECT COUNT(*) FROM decisions WHERE agent='trend'").fetchone()[0] == 1


def test_cycle_stores_the_newest_closed_minute_per_market(con: sqlite3.Connection) -> None:
    paper_cycle(con, ScenarioClient(), "trend", costs=Costs(), clock=lambda: NOW)
    rows = con.execute("SELECT symbol, bar_ts FROM market ORDER BY symbol").fetchall()
    last_minute = (NOW // 60 - 1) * 60
    assert [(r[0], r[1]) for r in rows] == [("BTCEUR", last_minute), ("ETHEUR", last_minute)]
