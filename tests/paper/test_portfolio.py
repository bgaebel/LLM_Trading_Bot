"""Spec 004 AK3: one portfolio per agent with 10,000 EUR, created exactly once."""

from pathlib import Path

from llm_trading_bot.paper.db import START_CASH, balance, open_db, write_tx


def test_new_agents_start_with_cash_only(tmp_path: Path) -> None:
    con = open_db(tmp_path / "bot.db", agents=("trend", "buyhold"))
    try:
        assert START_CASH == 10_000.0
        for agent in ("trend", "buyhold"):
            assert balance(con, agent) == {
                "agent": agent,
                "cash": 10_000.0,
                "btc": 0.0,
                "eth": 0.0,
                "peak": 10_000.0,
                "halted": 0,
            }
        assert con.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 2
    finally:
        con.close()


def test_reopening_never_resets_a_portfolio(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    con = open_db(path, agents=("trend", "buyhold"))
    with write_tx(con):
        con.execute("UPDATE portfolios SET cash=1.5, btc=0.25, peak=12345 WHERE agent='trend'")
    con.close()

    con = open_db(path, agents=("trend", "buyhold", "meanrev"))
    try:
        trend = balance(con, "trend")
        assert (trend["cash"], trend["btc"], trend["peak"]) == (1.5, 0.25, 12345.0)
        assert balance(con, "meanrev")["cash"] == START_CASH
        assert con.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 3
    finally:
        con.close()
