"""Spec 005 AK4 / ADR-0011: hard daily EUR limits per depot and in total, with worst-case
reservations committed before each request."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from llm_trading_bot.llm.budget import (
    Attempt,
    BudgetBlocked,
    Limits,
    Pricing,
    Usage,
    amount,
    charged_today,
    reservation,
    reserve,
    settle,
)
from llm_trading_bot.paper.db import open_db

DAY = 1_793_059_200  # 2026-10-27 00:00:00 UTC
SOL = Pricing(Decimal("2.00"), Decimal("10.00"))
LIMITS = Limits(Decimal(3), Decimal("2.00"), SOL)


def make(agent: str = "gpt-sol", chars: int = 5_000, out: int = 8_000) -> Attempt:
    return Attempt(
        agent,
        "BTC-EUR,ETH-EUR",
        60_000.0,
        3_000.0,
        "gpt-6.1-sol",
        "llm-v1",
        "ab" * 32,
        out,
        "low",
        "{}",
        chars,
    )


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("gpt-sol", "gpt-luna", "buyhold"))
    yield connection
    connection.close()


def book(
    con: sqlite3.Connection,
    attempt: Attempt | None = None,
    limits: Limits = LIMITS,
    at: float = DAY + 3600,
) -> int:
    return reserve(con, attempt or make(), limits, now=iso(at), now_epoch=at)


def stored(con: sqlite3.Connection, attempt_id: int) -> sqlite3.Row:
    query = "SELECT * FROM llm_calls WHERE id=?"
    row: sqlite3.Row = con.execute(query, (attempt_id,)).fetchone()
    return row


def test_reservation_is_the_worst_case_rounded_up() -> None:
    # 5000 chars * 2 EUR/M + 8000 tokens * 10 EUR/M = 0.01 + 0.08
    assert reservation(make(), SOL) == Decimal("0.0900")
    assert reservation(make(chars=1, out=1), SOL) == Decimal("0.0001")  # 0.000012 rounds up


@pytest.mark.parametrize("value", ["-1", "drei", "NaN", "inf", ""])
def test_amounts_must_be_finite_and_not_negative(value: str) -> None:
    with pytest.raises(ValueError):
        amount(value, "X")


def test_reservation_is_committed_before_the_request(con: sqlite3.Connection) -> None:
    row = stored(con, book(con))
    assert (row["status"], row["reserved_eur"], row["prompt_version"]) == (
        "PENDING",
        "0.0900",
        "llm-v1",
    )
    assert row["cost_eur"] is None and not con.in_transaction


def test_depot_limit_is_exact(con: sqlite3.Connection) -> None:
    attempt = make(chars=0, out=10_000)  # reservation exactly 0.10
    for _ in range(20):  # 20 x 0.10 = 2.00 = depot limit
        book(con, attempt)
    with pytest.raises(BudgetBlocked, match="depot budget reached"):
        book(con, attempt)
    row = con.execute(
        "SELECT status, reserved_eur, error FROM llm_calls ORDER BY id DESC"
    ).fetchone()
    assert (row["status"], row["reserved_eur"]) == ("BUDGET_BLOCKED", None)
    # Another depot still has its own share.
    book(con, make(agent="gpt-luna", chars=0, out=10_000), Limits(Decimal(3), Decimal("0.10"), SOL))


def test_total_limit_binds_across_depots(con: sqlite3.Connection) -> None:
    attempt = make(chars=0, out=10_000)
    for _ in range(20):
        book(con, attempt)
    luna = make(agent="gpt-luna", chars=0, out=10_000)
    luna_limits = Limits(Decimal(3), Decimal(5), SOL)
    for _ in range(10):  # total now 3.00
        book(con, luna, luna_limits)
    with pytest.raises(BudgetBlocked, match="total budget reached"):
        book(con, luna, luna_limits)
    assert charged_today(con, DAY + 3600) == (Decimal("3.00"), False)
    assert charged_today(con, DAY + 3600, "gpt-luna") == (Decimal("1.00"), False)


def test_known_cost_replaces_the_reservation(con: sqlite3.Connection) -> None:
    attempt_id = book(con)
    cost = settle(
        con,
        attempt_id,
        SOL,
        status="OK",
        usage=Usage(1500, 700, 300, 0),
        latency_seconds=2.5,
        response_id="resp_1",
    )
    assert cost == Decimal("0.01")
    row = stored(con, attempt_id)
    assert (row["cost_eur"], row["reasoning_tokens"], row["reservation_exceeded"]) == (
        "0.01",
        300,
        0,
    )
    assert charged_today(con, DAY + 3600, "gpt-sol") == (Decimal("0.01"), False)


def test_unknown_usage_and_crashes_keep_the_reservation(con: sqlite3.Connection) -> None:
    timed_out = book(con)
    settle(con, timed_out, SOL, status="ERROR", usage=None, latency_seconds=60.0, error="Timeout")
    book(con)  # process dies here: stays PENDING
    assert charged_today(con, DAY + 3600) == (Decimal("0.1800"), False)


def test_cost_above_the_reservation_blocks_that_depot_for_the_day(con: sqlite3.Connection) -> None:
    attempt_id = book(con)
    settle(con, attempt_id, SOL, status="OK", usage=Usage(5_000, 20_000), latency_seconds=9.0)
    assert stored(con, attempt_id)["reservation_exceeded"] == 1
    with pytest.raises(BudgetBlocked, match="more than its reservation"):
        book(con)
    book(con, make(agent="gpt-luna"))  # other depot unaffected
    book(con, at=DAY + 86_400 + 60)  # a new UTC day starts fresh


def test_not_sent_attempts_cost_nothing(con: sqlite3.Connection) -> None:
    attempt_id = book(con)
    settle(con, attempt_id, SOL, status="OK", usage=None, latency_seconds=0.0, sent=False)
    assert stored(con, attempt_id)["status"] == "NOT_SENT"
    assert charged_today(con, DAY + 3600) == (Decimal(0), False)


def test_yesterday_does_not_count(con: sqlite3.Connection) -> None:
    attempt = make(chars=0, out=10_000)
    for _ in range(20):
        book(con, attempt, at=DAY - 60)
    book(con, attempt, at=DAY)


def test_model_without_prices_is_never_called(con: sqlite3.Connection) -> None:
    with pytest.raises(BudgetBlocked, match="no prices"):
        book(con, limits=Limits(Decimal(3), Decimal(2), None))
    row = con.execute("SELECT status, error FROM llm_calls").fetchone()
    assert row["status"] == "BUDGET_BLOCKED" and "no prices" in row["error"]


def test_zero_limit_blocks(con: sqlite3.Connection) -> None:
    with pytest.raises(BudgetBlocked, match="depot budget"):
        book(con, limits=Limits(Decimal(3), Decimal(0), SOL))
