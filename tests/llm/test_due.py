"""Spec 005 AK3 / ADR-0013: hourly, after a >= 1.8 % move or a changed reference signal once
10 min have passed, and never more than 4 attempts per rolling hour - per depot."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from llm_trading_bot.llm.due import Cadence, attempts_last_hour, hourly_limit_reached, iso, llm_due
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.db import open_db, write_tx

T = 1_793_000_000
CADENCE = Cadence.from_env({})
CALM = {"trend": "HOLD", "breakout": "HOLD", "meanrev": "HOLD"}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("gpt-luna", "buyhold"))
    yield connection
    connection.close()


def context(btc: dict[str, str] | None = CALM, eth: dict[str, str] | None = CALM) -> str:
    markets = {}
    if btc is not None:
        markets["BTC-EUR"] = {"reference_signals": btc, "market": "BTCEUR"}
    if eth is not None:
        markets["ETH-EUR"] = {"reference_signals": eth, "market": "ETHEUR"}
    return json.dumps({"markets": markets})


def attempt(
    con: sqlite3.Connection,
    at: float,
    *,
    agent: str = "gpt-luna",
    btc: float | None = 100.0,
    eth: float | None = 10.0,
    status: str = "OK",
    ctx: str | None = None,
) -> None:
    with write_tx(con):
        con.execute(
            "INSERT INTO llm_calls(ts,agent,markets,ref_mid_btc,ref_mid_eth,status,model,"
            "prompt_version,prompt_sha256,max_output_tokens,effort,context)"
            " VALUES (?,?,?,?,?,?,'m','llm-v1','h',8000,'low',?)",
            (iso(at), agent, "BTC-EUR,ETH-EUR", btc, eth, status, ctx or context()),
        )


def due(
    con: sqlite3.Connection,
    now: float,
    btc: float = 100.0,
    eth: float = 10.0,
    signals: dict[Symbol, dict[str, str]] | None = None,
) -> bool:
    signals = signals or {Symbol.BTCEUR: CALM, Symbol.ETHEUR: CALM}
    return llm_due(con, "gpt-luna", {Symbol.BTCEUR: btc, Symbol.ETHEUR: eth}, signals, now, CADENCE)


def test_defaults_and_lower_bounds() -> None:
    assert Cadence.from_env({}) == Cadence(3600, 600, 0.018, 4)
    low = {
        "LLM_MIN_INTERVAL_SECONDS": "1",
        "LLM_EVENT_COOLDOWN_SECONDS": "1",
        "LLM_EVENT_MOVE_THRESHOLD": "0",
        "LLM_MAX_CALLS_PER_HOUR": "-5",
    }
    assert Cadence.from_env(low) == Cadence(900, 300, 0.005, 0)
    with pytest.raises(ValueError):
        Cadence.from_env({"LLM_MIN_INTERVAL_SECONDS": "3600.5"})


def test_first_attempt_is_always_due(con: sqlite3.Connection) -> None:
    assert due(con, T)


@pytest.mark.parametrize(
    ("elapsed", "expected"), [(-5, False), (3599, False), (3600, True), (3601, True)]
)
def test_regular_interval_is_one_hour(
    con: sqlite3.Connection, elapsed: int, expected: bool
) -> None:
    attempt(con, T)
    assert due(con, T + elapsed) is expected


@pytest.mark.parametrize(
    ("elapsed", "btc", "eth", "expected"),
    [
        (599, 110.0, 10.0, False),  # big move, but cooldown not over
        (600, 101.8, 10.0, True),  # exactly 1.8 % after exactly 10 min
        (600, 101.79, 10.0, False),
        (600, 98.2, 10.0, True),  # falls count as well
        (600, 98.21, 10.0, False),
        (1200, 100.0, 10.18, True),  # ETH alone triggers
        (1200, 100.0, 10.17, False),
    ],
)
def test_move_event_after_cooldown_on_either_market(
    con: sqlite3.Connection, elapsed: int, btc: float, eth: float, expected: bool
) -> None:
    attempt(con, T)
    assert due(con, T + elapsed, btc, eth) is expected


@pytest.mark.parametrize("name", ["trend", "breakout", "meanrev"])
def test_changed_reference_signal_triggers_after_cooldown(
    con: sqlite3.Connection, name: str
) -> None:
    attempt(con, T)
    changed = {Symbol.BTCEUR: CALM, Symbol.ETHEUR: {**CALM, name: "BUY"}}
    assert not due(con, T + 599, signals=changed)
    assert due(con, T + 600, signals=changed)
    assert not due(con, T + 600)  # same signals, small move: nothing new


def test_market_missing_last_time_counts_as_new(con: sqlite3.Connection) -> None:
    attempt(con, T, ctx=context(eth=None))
    assert due(con, T + 600)
    assert not due(con, T + 600, signals={Symbol.BTCEUR: CALM})


def test_unreadable_previous_context_counts_as_changed(con: sqlite3.Connection) -> None:
    attempt(con, T, ctx="not json")
    assert due(con, T + 600)


def test_missing_or_zero_reference_price_never_triggers_a_move(con: sqlite3.Connection) -> None:
    attempt(con, T, btc=None, eth=0.0)
    assert not due(con, T + 600, 200.0, 20.0)


def test_only_the_last_attempt_of_this_depot_counts(con: sqlite3.Connection) -> None:
    attempt(con, T - 5000)
    attempt(con, T, status="BUDGET_BLOCKED")  # blocked attempts reset the timer too (Arena)
    attempt(con, T + 100, agent="gpt-sol")
    assert not due(con, T + 1800)
    assert due(con, T + 3600)


def test_at_most_4_attempts_per_rolling_hour(con: sqlite3.Connection) -> None:
    for i in range(4):
        attempt(con, T + i * 600)
    attempt(con, T + 100, agent="gpt-sol")
    now = T + 3 * 600 + 1
    assert attempts_last_hour(con, "gpt-luna", now) == 4
    assert hourly_limit_reached(con, "gpt-luna", now, CADENCE)
    # The first attempt leaves the window exactly one hour later.
    assert not hourly_limit_reached(con, "gpt-luna", T + 3600, CADENCE)
    assert not hourly_limit_reached(con, "gpt-sol", now, CADENCE)


@pytest.mark.parametrize(("mid", "expected"), [(125.0, True), (124.99, False), (75.0, True)])
def test_move_threshold_is_inclusive_at_an_exact_value(
    con: sqlite3.Connection, mid: float, expected: bool
) -> None:
    """0.25 is exact in binary floating point, so the >= boundary is hit exactly."""
    cadence = Cadence.from_env({"LLM_EVENT_MOVE_THRESHOLD": "0.25"})
    attempt(con, T)
    mids = {Symbol.BTCEUR: mid, Symbol.ETHEUR: 10.0}
    signals = {Symbol.BTCEUR: CALM, Symbol.ETHEUR: CALM}
    assert llm_due(con, "gpt-luna", mids, signals, T + 600, cadence) is expected
