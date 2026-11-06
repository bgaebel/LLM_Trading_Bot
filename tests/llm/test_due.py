"""Spec 005 AK3 / ADR-0011: every 15 min, after a >= 1.8 % move once 5 min have passed, and never
more than 8 attempts per rolling hour - per depot."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from llm_trading_bot.llm.due import Cadence, attempts_last_hour, hourly_limit_reached, iso, llm_due
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.db import open_db, write_tx

T = 1_793_000_000
CADENCE = Cadence.from_env({})


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("gpt-sol", "buyhold"))
    yield connection
    connection.close()


def attempt(
    con: sqlite3.Connection,
    at: float,
    *,
    agent: str = "gpt-sol",
    btc: float | None = 100.0,
    eth: float | None = 10.0,
    status: str = "OK",
) -> None:
    with write_tx(con):
        con.execute(
            "INSERT INTO llm_calls(ts,agent,markets,ref_mid_btc,ref_mid_eth,status,model,"
            "prompt_version,prompt_sha256,max_output_tokens,effort,context)"
            " VALUES (?,?,?,?,?,?,'m','llm-v1','h',8000,'low','{}')",
            (iso(at), agent, "BTC-EUR,ETH-EUR", btc, eth, status),
        )


def due(con: sqlite3.Connection, now: float, btc: float = 100.0, eth: float = 10.0) -> bool:
    return llm_due(con, "gpt-sol", {Symbol.BTCEUR: btc, Symbol.ETHEUR: eth}, now, CADENCE)


def test_defaults_and_lower_bounds() -> None:
    assert Cadence.from_env({}) == Cadence(900, 300, 0.018, 8)
    low = {
        "LLM_MIN_INTERVAL_SECONDS": "1",
        "LLM_EVENT_COOLDOWN_SECONDS": "1",
        "LLM_EVENT_MOVE_THRESHOLD": "0",
        "LLM_MAX_CALLS_PER_HOUR": "-5",
    }
    assert Cadence.from_env(low) == Cadence(600, 300, 0.005, 0)
    with pytest.raises(ValueError):
        Cadence.from_env({"LLM_MIN_INTERVAL_SECONDS": "900.5"})


def test_first_attempt_is_always_due(con: sqlite3.Connection) -> None:
    assert due(con, T)


@pytest.mark.parametrize(
    ("elapsed", "expected"), [(-5, False), (899, False), (900, True), (901, True)]
)
def test_regular_interval_is_15_minutes(
    con: sqlite3.Connection, elapsed: int, expected: bool
) -> None:
    attempt(con, T)
    assert due(con, T + elapsed) is expected


@pytest.mark.parametrize(
    ("elapsed", "btc", "eth", "expected"),
    [
        (299, 110.0, 10.0, False),  # big move, but cooldown not over
        (300, 101.8, 10.0, True),  # exactly 1.8 % after exactly 5 min
        (300, 101.79, 10.0, False),
        (300, 98.2, 10.0, True),  # falls count as well
        (300, 98.21, 10.0, False),
        (600, 100.0, 10.18, True),  # ETH alone triggers
        (600, 100.0, 10.17, False),
    ],
)
def test_event_after_cooldown_on_either_market(
    con: sqlite3.Connection, elapsed: int, btc: float, eth: float, expected: bool
) -> None:
    attempt(con, T)
    assert due(con, T + elapsed, btc, eth) is expected


def test_missing_or_zero_reference_never_triggers_an_event(con: sqlite3.Connection) -> None:
    attempt(con, T, btc=None, eth=0.0)
    assert not due(con, T + 600, 200.0, 20.0)


def test_only_the_last_attempt_of_this_depot_counts(con: sqlite3.Connection) -> None:
    attempt(con, T - 5000)
    attempt(con, T, status="BUDGET_BLOCKED")  # blocked attempts reset the timer too (Arena)
    attempt(con, T + 100, agent="gpt-luna")
    assert not due(con, T + 600)
    assert due(con, T + 900)


def test_at_most_8_attempts_per_rolling_hour(con: sqlite3.Connection) -> None:
    for i in range(8):
        attempt(con, T + i * 300)
    attempt(con, T + 100, agent="gpt-luna")
    now = T + 7 * 300 + 1
    assert attempts_last_hour(con, "gpt-sol", now) == 8
    assert hourly_limit_reached(con, "gpt-sol", now, CADENCE)
    # The first attempt leaves the window exactly one hour later.
    assert not hourly_limit_reached(con, "gpt-sol", T + 3600, CADENCE)
    assert not hourly_limit_reached(con, "gpt-luna", now, CADENCE)
