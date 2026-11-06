"""Spec 004 AK6: without fresh quotes of both markets nothing is traded."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_trading_bot.market.models import MarketDataError, Quote, Symbol
from llm_trading_bot.paper.db import START_CASH, balance, open_db, write_tx
from llm_trading_bot.paper.execution import (
    Costs,
    Quotes,
    latest_quotes,
    record_decision,
    record_quotes,
)
from llm_trading_bot.strategy.signals import Signal

T = 1_793_000_000
QS = {Symbol.BTCEUR: Quote(60_000.0, 60_010.0), Symbol.ETHEUR: Quote(3_000.0, 3_001.0)}


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("trend", "buyhold"))
    yield connection
    connection.close()


def decide(con: sqlite3.Connection, qs: Quotes | None) -> None:
    record_decision(
        con,
        "trend",
        Symbol.BTCEUR,
        3600,
        "TREND",
        Signal("BUY", 0.45, 0.7, "test", "trend"),
        {},
        costs=Costs(),
        now=iso(T),
        qs=qs,
    )


@pytest.mark.parametrize(
    "qs", [None, {}, {Symbol.BTCEUR: QS[Symbol.BTCEUR]}], ids=["none", "empty", "one"]
)
def test_missing_quotes_store_stale_decision_without_trade(
    con: sqlite3.Connection, qs: Quotes | None
) -> None:
    decide(con, qs)
    row = con.execute("SELECT action, target, status FROM decisions").fetchone()
    assert (row["action"], row["target"], row["status"]) == ("HOLD", 0.0, "STALE_QUOTE")
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert balance(con, "trend")["cash"] == START_CASH


@pytest.mark.parametrize(("age", "fresh"), [(0, True), (20, True), (21, False), (-1, False)])
def test_quotes_older_than_20_seconds_or_from_the_future_are_stale(
    con: sqlite3.Connection, age: int, fresh: bool
) -> None:
    record_quotes(con, QS, now=iso(T))
    result = latest_quotes(con, now_epoch=T + age)
    assert (result == QS) if fresh else (result is None)


def test_one_stale_market_makes_both_stale(con: sqlite3.Connection) -> None:
    record_quotes(con, QS, now=iso(T))
    with write_tx(con):
        con.execute("UPDATE market SET ts=? WHERE symbol='ETHEUR'", (iso(T - 30),))
    assert latest_quotes(con, now_epoch=T) is None


def test_missing_or_invalid_stored_quote_is_stale(con: sqlite3.Connection) -> None:
    assert latest_quotes(con, now_epoch=T) is None
    record_quotes(con, QS, now=iso(T))
    with write_tx(con):
        con.execute("UPDATE market SET ask=bid*1.01 WHERE symbol='BTCEUR'")  # 100 bp spread
    assert latest_quotes(con, now_epoch=T) is None
    with write_tx(con):
        con.execute("UPDATE market SET ts='not a time' WHERE symbol='BTCEUR'")
    assert latest_quotes(con, now_epoch=T) is None


def test_record_quotes_needs_both_valid_quotes(con: sqlite3.Connection) -> None:
    with pytest.raises(MarketDataError, match="both"):
        record_quotes(con, {Symbol.BTCEUR: QS[Symbol.BTCEUR]}, now=iso(T))
    with pytest.raises(MarketDataError, match="spread"):
        record_quotes(con, {**QS, Symbol.ETHEUR: Quote(3_000.0, 3_100.0)}, now=iso(T))
    assert con.execute("SELECT COUNT(*) FROM market").fetchone()[0] == 0


def test_newer_quotes_replace_older_ones(con: sqlite3.Connection) -> None:
    record_quotes(con, QS, now=iso(T))
    newer = {Symbol.BTCEUR: Quote(61_000.0, 61_005.0), Symbol.ETHEUR: Quote(3_100.0, 3_100.5)}
    record_quotes(con, newer, now=iso(T + 30))
    assert latest_quotes(con, now_epoch=T + 30) == newer
    assert con.execute("SELECT COUNT(*) FROM market").fetchone()[0] == 2


def test_stale_store_leads_to_stale_decision(con: sqlite3.Connection) -> None:
    record_quotes(con, QS, now=iso(T - 21))
    decide(con, latest_quotes(con, now_epoch=T))
    assert con.execute("SELECT status FROM decisions").fetchone()[0] == "STALE_QUOTE"
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
