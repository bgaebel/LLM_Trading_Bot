"""Spec 004 AK7: the buy & hold benchmark buys each market exactly once, also across restarts."""

from pathlib import Path

import pytest

from llm_trading_bot.market.models import MarketDataError, Quote, Symbol
from llm_trading_bot.paper.db import balance, open_db
from llm_trading_bot.paper.execution import (
    BUYHOLD,
    Costs,
    current_weight,
    initialize_buyhold,
    record_decision,
)
from llm_trading_bot.strategy.signals import Signal

AGENTS = ("trend", BUYHOLD)
QS = {Symbol.BTCEUR: Quote(60_000.0, 60_010.0), Symbol.ETHEUR: Quote(3_000.0, 3_001.0)}
NOW = "2026-11-06T06:00:00+00:00"


def trades(path: Path) -> list[tuple[str, str]]:
    con = open_db(path, agents=AGENTS)
    try:
        return [(r[0], r[1]) for r in con.execute("SELECT symbol, side FROM trades ORDER BY id")]
    finally:
        con.close()


def test_buys_both_markets_once_and_never_again_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    con = open_db(path, agents=AGENTS)
    try:
        assert initialize_buyhold(con, QS, Costs(), when=NOW) == 2
        p = balance(con, BUYHOLD)
        for symbol in QS:
            assert current_weight(p, symbol, QS) == pytest.approx(0.45, abs=0.005)
        assert initialize_buyhold(con, QS, Costs(), when=NOW) == 0
    finally:
        con.close()

    con = open_db(path, agents=AGENTS)
    try:
        moved = {Symbol.BTCEUR: Quote(50_000.0, 50_010.0), Symbol.ETHEUR: Quote(2_000.0, 2_001.0)}
        assert initialize_buyhold(con, moved, Costs(), when=NOW) == 0
    finally:
        con.close()
    assert trades(path) == [("BTCEUR", "BUY"), ("ETHEUR", "BUY")]


def test_later_buy_signals_for_the_benchmark_become_hold(tmp_path: Path) -> None:
    con = open_db(tmp_path / "bot.db", agents=AGENTS)
    try:
        initialize_buyhold(con, QS, Costs(), when=NOW)
        record_decision(
            con,
            BUYHOLD,
            Symbol.BTCEUR,
            3600,
            "BUY&HOLD",
            Signal("BUY", 0.45, 1.0, "again", BUYHOLD),
            {},
            costs=Costs(),
            now=NOW,
            qs=QS,
        )
        assert con.execute("SELECT action FROM decisions WHERE bar_ts=3600").fetchone()[0] == "HOLD"
        assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2
    finally:
        con.close()


@pytest.mark.parametrize(
    "qs",
    [
        {Symbol.BTCEUR: QS[Symbol.BTCEUR]},
        {**QS, Symbol.ETHEUR: Quote(3_000.0, 3_100.0)},
        {**QS, Symbol.BTCEUR: Quote(0.0, 1.0)},
    ],
    ids=["one-market", "wide-spread", "zero-bid"],
)
def test_needs_fresh_valid_quotes_of_both_markets(tmp_path: Path, qs: dict[Symbol, Quote]) -> None:
    con = open_db(tmp_path / "bot.db", agents=AGENTS)
    try:
        with pytest.raises(MarketDataError):
            initialize_buyhold(con, qs, Costs(), when=NOW)
        assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    finally:
        con.close()
