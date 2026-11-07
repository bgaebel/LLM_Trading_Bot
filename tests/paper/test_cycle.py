"""Spec 004 AK9: one paper cycle on (fake) live data; a second run in the same bar adds nothing."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel, valid_reply
from llm_trading_bot.llm.agents import BotConfig, load_config
from llm_trading_bot.llm.due import iso
from llm_trading_bot.llm.openai_client import ModelReply
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot
from llm_trading_bot.paper import cycle
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.risk import Controls
from llm_trading_bot.strategy.signals import Signal

TREND = BotConfig(("trend",), None)


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
    report = paper_cycle(con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW)
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
    paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    before = counts(con)
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW + 5)
    assert report.buyhold_bought == 0
    assert all(not m.new for m in report.markets)
    after = counts(con)
    assert (after["decisions"], after["trades"]) == (before["decisions"], before["trades"])
    assert after["equity"] == before["equity"] + 2  # one new equity point per agent and cycle


def test_failing_eth_candles_do_not_block_btc(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.fail_candles = {Symbol.ETHEUR}
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
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
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    assert "HTTP 503" in report.quotes_error
    assert report.markets == [] and report.equity == []
    assert counts(con) == {"decisions": 0, "trades": 0, "equity": 0}

    client.fail_quotes = False
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW + 10)
    assert all(m.new for m in report.markets)


def test_quotes_stored_by_a_recent_cycle_still_count_when_one_fetch_fails(
    con: sqlite3.Connection,
) -> None:
    client = ScenarioClient(btc="down-calm-plain-s1", eth="down-calm-plain-s1")
    paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    client.fail_quotes = True
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW + 15)
    # The ticker failed, so the candles' snapshot cannot be built either: no new decision.
    assert report.equity and all(m.error for m in report.markets)


def test_sell_signal_without_position_does_not_trade(con: sqlite3.Connection) -> None:
    client = ScenarioClient(btc="down-calm-plain-s1", eth="down-calm-plain-s1")
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    assert [(m.action, m.traded) for m in report.markets] == [("SELL", False), ("SELL", False)]


def test_halted_portfolio_takes_no_decision_and_is_no_error(con: sqlite3.Connection) -> None:
    con.execute("UPDATE portfolios SET halted=1 WHERE agent='trend'")
    report = paper_cycle(con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW)
    assert [(m.status, m.error) for m in report.markets] == [("HALTED", ""), ("HALTED", "")]
    # ADR-0021: a stopped depot must not keep the service degraded (deploy would roll back).
    assert report.complete and not report.risk_stops
    assert con.execute("SELECT COUNT(*) FROM decisions WHERE agent='trend'").fetchone()[0] == 0
    assert [e.agent for e in report.equity] == ["trend", BUYHOLD]


def test_halted_by_a_trade_in_the_cycle_is_reported_once(con: sqlite3.Connection) -> None:
    # A peak far above the equity: the first trade's mark triggers the stop.
    con.execute("UPDATE portfolios SET peak=20000 WHERE agent='trend'")
    client = ScenarioClient()
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    btc, eth = report.markets
    assert (btc.status, btc.traded) == ("OK", True)
    assert (eth.status, eth.error) == ("HALTED", "")
    assert [(s.agent, s.peak) for s in report.risk_stops] == [("trend", 20000.0)]
    assert report.complete
    sides = [r[0] for r in con.execute("SELECT side FROM trades WHERE agent='trend' ORDER BY id")]
    assert sides == ["BUY", "RISK_SELL"]
    again = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW + 60)
    assert not again.risk_stops and again.complete
    assert {m.status for m in again.markets} <= {"HALTED", "OK"}


def test_halted_by_the_final_valuation_is_reported(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    # Same bar again: nothing to decide, so only the final valuation can stop the depot.
    con.execute("UPDATE portfolios SET peak=20000 WHERE agent='trend'")
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW + 5)
    assert all(not m.new for m in report.markets)
    assert [s.agent for s in report.risk_stops] == ["trend"]
    assert report.complete


def test_strategy_failure_in_btc_does_not_block_eth(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_trading_bot.strategy.signals import run_strategy as real

    def flaky(name: str, snap: MarketSnapshot, *args: Any) -> Signal:
        if snap.symbol == Symbol.BTCEUR:
            raise ZeroDivisionError("broken indicator")
        return real(name, snap, *args)

    monkeypatch.setattr(cycle, "run_strategy", flaky)
    report = paper_cycle(con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW)
    btc, eth = report.markets
    assert btc.error == "ZeroDivisionError: broken indicator"
    assert (eth.new, eth.traded) == (True, True)
    assert not report.complete and len(report.equity) == 2
    assert con.execute("SELECT COUNT(*) FROM decisions WHERE agent='trend'").fetchone()[0] == 1


def test_cycle_stores_the_newest_closed_minute_per_market(con: sqlite3.Connection) -> None:
    paper_cycle(con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW)
    rows = con.execute("SELECT symbol, bar_ts FROM market ORDER BY symbol").fetchall()
    last_minute = (NOW // 60 - 1) * 60
    assert [(r[0], r[1]) for r in rows] == [("BTCEUR", last_minute), ("ETHEUR", last_minute)]


# ------------------------------------------------------------------ Spec 008 AK5: stopping


def test_stop_request_skips_gpt_but_still_values_the_depots(tmp_path: Path) -> None:
    from llm_fakes import KEY, FakeModel
    from llm_trading_bot.llm.agents import load_config

    con = open_db(tmp_path / "bot.db", agents=("gpt-luna", "gpt-sol", BUYHOLD))
    try:
        model = FakeModel()
        config = load_config({"OPENAI_API_KEY": KEY})
        report = paper_cycle(
            con,
            ScenarioClient(),
            config,
            costs=Costs(),
            model=model,
            clock=lambda: NOW,
            stop_requested=lambda: True,
        )
        assert model.calls == []
        assert [(o.agent, o.status) for o in report.llm] == [
            ("gpt-luna", "STOPPING"),
            ("gpt-sol", "STOPPING"),
        ]
        assert con.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
        assert [e.agent for e in report.equity] == ["gpt-luna", "gpt-sol", BUYHOLD]
        assert report.complete  # stopping is not an error
    finally:
        con.close()


def test_stop_request_is_not_asked_without_gpt_depots(
    con: sqlite3.Connection,
) -> None:
    asked: list[int] = []

    def stop() -> bool:
        asked.append(counts(con)["decisions"])
        return True

    report = paper_cycle(
        con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW, stop_requested=stop
    )
    # Classic depots only: there is no GPT step, so nothing to skip.
    assert asked == [] and report.complete and len(report.equity) == 2


# ------------------------------------------------------------------ Spec 011 AK7/AK8: per market


def test_bad_eth_quote_does_not_block_btc(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.bad_quotes = {Symbol.ETHEUR}
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    btc, eth = report.markets
    assert (btc.symbol, btc.new, btc.traded, btc.error) == (Symbol.BTCEUR, True, True, "")
    # The broken quote already fails the ETH snapshot (it carries the quote).
    assert eth.error == "MarketDataError: ETH-EUR: no quote - ticker/book: unusable quote (fake)"
    assert report.quotes_error.startswith("MarketDataError: ETH-EUR: ticker/book")
    assert not report.complete
    # Buy & hold buys only with both quotes; holding nothing, it is still valued.
    assert report.buyhold_bought == 0 and [e.agent for e in report.equity] == ["trend", BUYHOLD]
    # The ETH bar stays open: with a good quote the next round decides it.
    client.bad_quotes = set()
    again = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW + 5)
    assert [(m.symbol, m.new) for m in again.markets] == [
        (Symbol.BTCEUR, False),
        (Symbol.ETHEUR, True),
    ]
    assert again.complete and again.buyhold_bought == 2


def test_holding_the_stale_market_blocks_both(con: sqlite3.Connection) -> None:
    con.execute("UPDATE portfolios SET eth=0.5 WHERE agent='trend'")
    client = ScenarioClient()
    client.bad_quotes = {Symbol.ETHEUR}
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=lambda: NOW)
    assert [m.error for m in report.markets] == [
        "MarketDataError: ETH-EUR: no fresh quote for the held position",
        "MarketDataError: ETH-EUR: no quote - ticker/book: unusable quote (fake)",
    ]
    assert counts(con)["decisions"] == 0
    # No equity point for a depot whose value is unknown; the round still ran (incomplete).
    assert [e.agent for e in report.equity] == [BUYHOLD]
    assert [(m.agent, m.symbol) for m in report.unvalued] == [("trend", Symbol.ETHEUR)]
    assert con.execute("SELECT COUNT(*) FROM equity WHERE agent='trend'").fetchone()[0] == 0


class Ticking:
    """A clock that moves on with every candle request (a slow Bitvavo)."""

    def __init__(self, step: float) -> None:
        self.now, self.step = float(NOW), step

    def __call__(self) -> float:
        return self.now


class SlowCandles(ScenarioClient):
    def __init__(self, clock: Ticking) -> None:
        super().__init__()
        self.clock = clock

    def closed_candles(self, symbol: Symbol, interval: int, *, now_epoch: int) -> Any:
        self.clock.now += self.clock.step
        return super().closed_candles(symbol, interval, now_epoch=now_epoch)


@pytest.mark.parametrize(("step", "fetches"), [(2.0, 2), (3.0, 3)])
def test_slow_round_refreshes_quotes_before_the_classic_decisions(
    con: sqlite3.Connection, step: float, fetches: int
) -> None:
    # 8 candle requests: 16 s keep the first quotes (<= 20 s), 24 s fetch new ones.
    clock = Ticking(step)
    client = SlowCandles(clock)
    report = paper_cycle(con, client, TREND, costs=Costs(), clock=clock)
    assert report.complete and all(m.traded for m in report.markets)
    # One fetch at the start, one inside the snapshot, one more when the first got too old.
    assert client.quote_calls == fetches
    stored = con.execute("SELECT MIN(ts) FROM market").fetchone()[0]
    assert stored == iso(NOW + (8 * step if fetches == 3 else 0))


@pytest.fixture
def gpt_con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "gpt.db", agents=("gpt-luna", BUYHOLD))
    yield connection
    connection.close()


def test_slow_round_refreshes_quotes_before_the_final_valuation(
    gpt_con: sqlite3.Connection,
) -> None:
    con = gpt_con
    clock = Ticking(0.0)
    client = ScenarioClient()

    def slow(markets: list[str]) -> ModelReply:
        clock.now = NOW + 30  # the answer takes 30 s
        return valid_reply(markets)

    model = FakeModel({"gpt-6-luna": slow})
    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna"})
    calls_before = client.quote_calls
    report = paper_cycle(con, client, config, costs=Costs(), model=model, clock=clock)
    assert report.complete
    # start, snapshot, after the answer; the final valuation reuses the post-answer quotes.
    assert client.quote_calls - calls_before == 3


def test_final_valuation_never_uses_stale_quotes(gpt_con: sqlite3.Connection) -> None:
    con = gpt_con
    clock = Ticking(0.0)
    client = ScenarioClient()

    def slow(markets: list[str]) -> ModelReply:
        clock.now = NOW + 30
        client.bad_quotes = {Symbol.ETHEUR}  # the refresh after the answer loses ETH
        return valid_reply(markets)

    model = FakeModel({"gpt-6-luna": slow})
    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna"})
    report = paper_cycle(con, client, config, costs=Costs(), model=model, clock=clock)
    # Buy & hold holds ETH, but the ETH quote from the start is 30 s old: no equity point.
    assert [(m.agent, m.symbol) for m in report.unvalued][:1] == [(BUYHOLD, Symbol.ETHEUR)]
    assert BUYHOLD not in {e.agent for e in report.equity}
    assert not report.complete
    # Only the point its purchase wrote at the start, on the then fresh quotes.
    assert con.execute("SELECT COUNT(*) FROM equity WHERE agent='buyhold'").fetchone()[0] == 1


# ------------------------------------------------------------------ Spec 011 AK5/AK6


PAUSED = Controls(True, "file")


def test_pause_stores_classic_decisions_without_trading(con: sqlite3.Connection) -> None:
    report = paper_cycle(
        con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW, controls=PAUSED
    )
    assert report.paused and report.complete
    assert [(m.status, m.new, m.traded) for m in report.markets] == [
        ("PAUSED", True, False),
        ("PAUSED", True, False),
    ]
    assert report.buyhold_bought == 0
    assert counts(con) == {"decisions": 2, "trades": 0, "equity": 2}


def test_pause_skips_gpt_without_a_request(gpt_con: sqlite3.Connection) -> None:
    model = FakeModel()
    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna"})
    report = paper_cycle(
        gpt_con,
        ScenarioClient(),
        config,
        costs=Costs(),
        model=model,
        clock=lambda: NOW,
        controls=PAUSED,
    )
    assert [(o.agent, o.status) for o in report.llm] == [("gpt-luna", "PAUSED")]
    assert model.calls == [] and report.complete
    assert gpt_con.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


def test_pause_keeps_the_drawdown_stop(con: sqlite3.Connection) -> None:
    con.execute("UPDATE portfolios SET peak=20000, btc=0.01, cash=9000 WHERE agent='trend'")
    report = paper_cycle(
        con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW, controls=PAUSED
    )
    assert [s.agent for s in report.risk_stops] == ["trend"]
    sides = [r[0] for r in con.execute("SELECT side FROM trades")]
    assert sides == ["RISK_SELL"]


def test_resume_request_is_applied_at_the_start_of_the_round(con: sqlite3.Connection) -> None:
    from llm_trading_bot.paper.risk import epoch

    con.execute("UPDATE portfolios SET halted=1, cash=8000, peak=12000 WHERE agent='trend'")
    request = Controls(resumes={"trend": NOW - 60, "gpt-sol": NOW - 60})
    report = paper_cycle(
        con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW, controls=request
    )
    assert report.released == ["trend"] and report.refused == []
    assert all(m.status == "OK" for m in report.markets)  # trades again in the same round
    old = Controls(resumes={"trend": NOW - 2 * 86_400})
    con.execute("UPDATE portfolios SET halted=1 WHERE agent='trend'")
    again = paper_cycle(
        con, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW + 5, controls=old
    )
    assert again.released == [] and again.refused == [("trend", "the depot still holds coins")]
    assert epoch(iso(NOW)) == NOW
