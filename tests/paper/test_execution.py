"""Spec 011 AK7: valuation and decisions on quotes of single markets (ADR-0021).

A market the depot does not hold may lack a quote: its term is exactly 0.0 either way, so value,
weight and every decision are bit-identical to having the quote. Anything else stays fail closed.
"""

import random
import sqlite3
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from llm_trading_bot.market.models import SYMBOLS, MarketDataError, Quote, Symbol
from llm_trading_bot.paper.db import balance, open_db, write_tx
from llm_trading_bot.paper.execution import (
    Costs,
    covers,
    current_weight,
    record_decision,
    unquoted_holdings,
    value,
)
from llm_trading_bot.strategy.signals import Signal

NOW = "2026-11-07T08:00:00+00:00"


def bits(x: float) -> bytes:
    return struct.pack("<d", x)


def _case(rng: random.Random) -> tuple[dict[str, float], dict[Symbol, Quote], Symbol]:
    qs = {
        Symbol.BTCEUR: Quote(b := rng.uniform(20_000, 100_000), b * (1 + rng.uniform(0, 0.004))),
        Symbol.ETHEUR: Quote(e := rng.uniform(1_000, 6_000), e * (1 + rng.uniform(0, 0.004))),
    }
    empty = rng.choice(SYMBOLS)
    p = {
        "cash": rng.choice((0.0, rng.uniform(0, 20_000))),
        "btc": 0.0 if empty == Symbol.BTCEUR else rng.choice((0.0, rng.uniform(0, 0.3))),
        "eth": 0.0 if empty == Symbol.ETHEUR else rng.choice((0.0, rng.uniform(0, 4))),
    }
    return p, qs, empty


def test_value_without_a_zero_holding_market_is_bit_identical() -> None:
    rng = random.Random("spec011-value")
    for _ in range(5_000):
        p, qs, empty = _case(rng)
        partial = {s: q for s, q in qs.items() if s != empty}
        assert bits(value(p, partial)) == bits(value(p, qs))
        for s in SYMBOLS:
            assert bits(current_weight(p, s, partial)) == bits(current_weight(p, s, qs))
        assert covers(p, partial) and unquoted_holdings(p, partial) == []


def test_value_of_a_held_market_without_quote_fails() -> None:
    p = {"cash": 100.0, "btc": 0.0, "eth": 1e-12}
    qs = {Symbol.BTCEUR: Quote(60_000.0, 60_010.0)}
    with pytest.raises(MarketDataError, match="ETH-EUR"):
        value(p, qs)
    assert unquoted_holdings(p, qs) == [Symbol.ETHEUR]
    assert not covers(p, qs) and not covers({**p, "eth": 0.0}, qs, Symbol.ETHEUR)


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("trend",))
    yield connection
    connection.close()


def _decide(
    con: sqlite3.Connection, symbol: Symbol, qs: dict[Symbol, Quote], **kw: Any
) -> sqlite3.Row:
    sig = Signal(kw.pop("action", "BUY"), kw.pop("target", 0.45), 0.7, "test", "trend")
    record_decision(
        con, "trend", symbol, 3600, "TREND", sig, {}, costs=Costs(), now=NOW, qs=qs, **kw
    )
    row: sqlite3.Row = con.execute("SELECT * FROM decisions ORDER BY id DESC").fetchone()
    return row


def _state(con: sqlite3.Connection) -> list[Any]:
    tables = [
        [tuple(r)[1:] for r in con.execute(f"SELECT * FROM {t} ORDER BY id")]
        for t in ("decisions", "trades", "equity")
    ]
    return [tuple(balance(con, "trend")), *tables]


QS = {Symbol.BTCEUR: Quote(60_000.0, 60_010.0), Symbol.ETHEUR: Quote(3_000.0, 3_001.0)}


@pytest.mark.parametrize("seed", range(40))
def test_partial_decision_equals_the_one_with_both_quotes(tmp_path: Path, seed: int) -> None:
    rng = random.Random(f"spec011-decide:{seed}")
    p, qs, empty = _case(rng)
    decided = next(s for s in SYMBOLS if s != empty)
    action = rng.choice(("BUY", "SELL", "HOLD"))
    target = rng.choice((0.0, 0.25, 0.45))
    states = []
    for name, quotes in (("full", qs), ("partial", {s: q for s, q in qs.items() if s != empty})):
        c = open_db(tmp_path / f"{name}.db", agents=("trend",))
        with write_tx(c):
            c.execute(
                "UPDATE portfolios SET cash=?,btc=?,eth=? WHERE agent='trend'",
                (p["cash"], p["btc"], p["eth"]),
            )
        _decide(c, decided, quotes, action=action, target=target, partial=name == "partial")
        states.append(_state(c))
        c.close()
    assert states[0] == states[1]


def test_partial_quotes_without_permission_stay_stale(con: sqlite3.Connection) -> None:
    row = _decide(con, Symbol.BTCEUR, {Symbol.BTCEUR: QS[Symbol.BTCEUR]})
    assert (row["status"], row["action"]) == ("STALE_QUOTE", "HOLD")  # the Arena path


def test_partial_quotes_trade_when_they_cover_the_depot(con: sqlite3.Connection) -> None:
    row = _decide(con, Symbol.BTCEUR, {Symbol.BTCEUR: QS[Symbol.BTCEUR]}, partial=True)
    assert row["status"] == "OK"
    assert con.execute("SELECT side FROM trades").fetchone()[0] == "BUY"


def test_partial_quotes_without_the_decided_market_stay_stale(con: sqlite3.Connection) -> None:
    row = _decide(con, Symbol.BTCEUR, {Symbol.ETHEUR: QS[Symbol.ETHEUR]}, partial=True)
    assert row["status"] == "STALE_QUOTE"
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_partial_quotes_without_a_held_market_stay_stale(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET eth=0.5 WHERE agent='trend'")
    row = _decide(con, Symbol.BTCEUR, {Symbol.BTCEUR: QS[Symbol.BTCEUR]}, partial=True)
    assert row["status"] == "STALE_QUOTE"
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
