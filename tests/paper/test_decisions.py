"""Spec 004 AK4: decisions are idempotent per (agent, symbol, bar_ts) and atomic with the trade."""

import sqlite3
from pathlib import Path

import pytest

from llm_trading_bot.market.models import Quote, Symbol
from llm_trading_bot.paper import execution
from llm_trading_bot.paper.db import balance, open_db
from llm_trading_bot.paper.execution import Costs, record_decision
from llm_trading_bot.strategy.signals import Signal

AGENTS = ("trend", "buyhold")
QS = {Symbol.BTCEUR: Quote(60_000.0, 60_010.0), Symbol.ETHEUR: Quote(3_000.0, 3_001.0)}
BUY = Signal("BUY", 0.45, 0.7, "test", "trend")


def counts(con: sqlite3.Connection) -> tuple[int, int, int]:
    return tuple(
        con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("decisions", "trades", "equity")
    )


def decide(con: sqlite3.Connection, sig: Signal = BUY, bar_ts: int = 3600) -> bool:
    return record_decision(
        con,
        "trend",
        Symbol.BTCEUR,
        bar_ts,
        "TREND",
        sig,
        {"k": 1},
        costs=Costs(),
        now="2026-11-06T06:00:00+00:00",
        qs=QS,
    )


def test_same_bar_twice_creates_nothing_even_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    con = open_db(path, agents=AGENTS)
    assert decide(con) is True
    assert counts(con) == (1, 1, 1)
    after_first = balance(con, "trend")
    con.close()

    con = open_db(path, agents=AGENTS)
    try:
        assert decide(con, Signal("SELL", 0.0, 0.7, "other", "trend")) is False
        assert counts(con) == (1, 1, 1)
        assert balance(con, "trend") == after_first
        assert decide(con, bar_ts=7200) is True  # a new bar is decided normally
    finally:
        con.close()


def test_failure_after_the_trade_rolls_back_decision_trade_and_portfolio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = open_db(tmp_path / "bot.db", agents=AGENTS)
    before = balance(con, "trend")

    def broken_mark(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(execution, "mark", broken_mark)
    try:
        with pytest.raises(RuntimeError, match="disk full"):
            decide(con)
        assert counts(con) == (0, 0, 0)
        assert balance(con, "trend") == before
        assert not con.in_transaction
    finally:
        con.close()


def test_invalid_signal_is_refused_without_a_row(tmp_path: Path) -> None:
    con = open_db(tmp_path / "bot.db", agents=AGENTS)
    try:
        with pytest.raises(ValueError, match="target_fraction"):
            decide(con, Signal("BUY", 0.3, 0.7, "test", "trend"))
        assert counts(con) == (0, 0, 0)
    finally:
        con.close()


def test_decision_takes_the_write_lock_up_front(tmp_path: Path) -> None:
    """BEGIN IMMEDIATE: a second writer is refused before anything is read or written."""
    path = tmp_path / "bot.db"
    con = open_db(path, agents=AGENTS)
    other = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        con.execute("PRAGMA busy_timeout=0")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            decide(con)
        other.execute("ROLLBACK")
        assert counts(con) == (0, 0, 0)
    finally:
        other.close()
        con.close()
