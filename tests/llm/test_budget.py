"""Spec 005 AK4 / ADR-0009: hard daily EUR limit with reservations committed before each call."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from llm_trading_bot.llm.budget import (
    Attempt,
    BudgetBlocked,
    BudgetSettings,
    Usage,
    charged_today,
    reserve,
    settle,
)
from llm_trading_bot.paper.db import open_db

DAY = 1_793_059_200  # 2026-10-27 00:00:00 UTC
SETTINGS = BudgetSettings.from_env({}, "gpt-6.1-sol")
ATTEMPT = Attempt("gpt", "BTCEUR", 60_000.0, "gpt-6.1-sol", 8000, "medium", "ab" * 32, "{}")


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("gpt", "buyhold"))
    yield connection
    connection.close()


def book(
    con: sqlite3.Connection, at: float = DAY + 3600, settings: BudgetSettings = SETTINGS
) -> int:
    return reserve(con, ATTEMPT, settings, now=iso(at), now_epoch=at)


def row(con: sqlite3.Connection, attempt_id: int) -> sqlite3.Row:
    result: sqlite3.Row = con.execute(
        "SELECT * FROM llm_calls WHERE id=?", (attempt_id,)
    ).fetchone()
    return result


def test_defaults_are_three_euro_ten_cent_and_list_prices() -> None:
    assert (
        BudgetSettings(Decimal(3), Decimal("0.10"), Decimal("2.00"), Decimal("10.00")) == SETTINGS
    )


def test_other_model_without_prices_has_no_prices() -> None:
    settings = BudgetSettings.from_env({}, "gpt-6-astra")
    assert settings.input_eur_per_million is None and settings.output_eur_per_million is None


@pytest.mark.parametrize(
    "env",
    [
        {"OPENAI_INPUT_EUR_PER_MILLION": "2"},
        {"LLM_DAILY_BUDGET_EUR": "-1"},
        {"LLM_DAILY_BUDGET_EUR": "drei"},
        {"LLM_CALL_RESERVE_EUR": "NaN"},
        {"OPENAI_INPUT_EUR_PER_MILLION": "2", "OPENAI_OUTPUT_EUR_PER_MILLION": "inf"},
    ],
)
def test_invalid_settings_are_refused(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        BudgetSettings.from_env(env, "gpt-6.1-sol")


def test_reservation_is_committed_before_the_call(con: sqlite3.Connection) -> None:
    attempt_id = book(con)
    stored = row(con, attempt_id)
    assert (stored["status"], stored["reserved_eur"], stored["cost_eur"]) == (
        "PENDING",
        "0.10",
        None,
    )
    assert not con.in_transaction


def test_limit_is_exact_at_three_euro(con: sqlite3.Connection) -> None:
    for _ in range(30):  # 30 x 0.10 = 3.00 is allowed
        book(con)
    with pytest.raises(BudgetBlocked, match="daily budget reached"):
        book(con)
    blocked = con.execute("SELECT status, reserved_eur FROM llm_calls ORDER BY id DESC").fetchone()
    assert (blocked["status"], blocked["reserved_eur"]) == ("BUDGET_BLOCKED", None)
    assert charged_today(con, DAY + 3600) == (Decimal("3.00"), False)


def test_known_cost_replaces_the_reservation(con: sqlite3.Connection) -> None:
    attempt_id = book(con)
    cost = settle(
        con,
        attempt_id,
        SETTINGS,
        status="OK",
        usage=Usage(800, 1750, 1500, 0),
        latency_seconds=2.5,
        response_id="resp_1",
    )
    assert cost == Decimal("0.0191")
    stored = row(con, attempt_id)
    assert (stored["cost_eur"], stored["reasoning_tokens"], stored["reservation_exceeded"]) == (
        "0.0191",
        1500,
        0,
    )
    assert charged_today(con, DAY + 3600) == (Decimal("0.0191"), False)


def test_unknown_usage_and_crashes_keep_the_reservation(con: sqlite3.Connection) -> None:
    timed_out = book(con)
    settle(con, timed_out, SETTINGS, status="ERROR", usage=None, latency_seconds=60.0)
    book(con)  # process dies here: stays PENDING
    assert charged_today(con, DAY + 3600) == (Decimal("0.20"), False)


def test_cost_above_the_reservation_blocks_the_rest_of_the_day(con: sqlite3.Connection) -> None:
    attempt_id = book(con)
    settle(con, attempt_id, SETTINGS, status="OK", usage=Usage(800, 20_000), latency_seconds=9.0)
    assert row(con, attempt_id)["reservation_exceeded"] == 1
    with pytest.raises(BudgetBlocked, match="more than its reservation"):
        book(con)
    book(con, at=DAY + 86_400 + 60)  # a new UTC day starts fresh


def test_not_sent_attempts_cost_nothing(con: sqlite3.Connection) -> None:
    attempt_id = book(con)
    settle(con, attempt_id, SETTINGS, status="OK", usage=None, latency_seconds=0.0, sent=False)
    assert row(con, attempt_id)["status"] == "NOT_SENT"
    assert charged_today(con, DAY + 3600) == (Decimal(0), False)


def test_yesterday_does_not_count(con: sqlite3.Connection) -> None:
    for _ in range(30):
        book(con, at=DAY - 60)
    book(con, at=DAY)


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        (BudgetSettings.from_env({}, "gpt-6-astra"), "no prices"),
        (BudgetSettings.from_env({"LLM_CALL_RESERVE_EUR": "0"}, "gpt-6.1-sol"), "positive"),
        (BudgetSettings.from_env({"LLM_DAILY_BUDGET_EUR": "0"}, "gpt-6.1-sol"), "daily budget"),
    ],
    ids=["no-prices", "zero-reserve", "zero-cap"],
)
def test_blocking_configurations_store_the_reason(
    con: sqlite3.Connection, settings: BudgetSettings, reason: str
) -> None:
    with pytest.raises(BudgetBlocked, match=reason):
        book(con, settings=settings)
    stored = con.execute("SELECT status, error FROM llm_calls").fetchone()
    assert stored["status"] == "BUDGET_BLOCKED" and reason in stored["error"]
