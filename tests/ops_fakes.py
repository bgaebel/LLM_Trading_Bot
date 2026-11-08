"""Shared helpers for the operations tests (Spec 009)."""

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.llm.due import iso
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.observations_schema import create


def at(epoch: float) -> Callable[[], float]:
    return lambda: epoch


def bot_db(path: Path, cycles: int = 2) -> Path:
    """A database written by real paper cycles (classic depots, fake market)."""
    con = open_db(path, agents=("trend", BUYHOLD))
    try:
        for n in range(cycles):
            paper_cycle(
                con,
                ScenarioClient(),
                BotConfig(("trend",), None),
                costs=Costs(),
                clock=at(NOW + 60 * n),
            )
    finally:
        con.close()
    return path


def obs_db(path: Path, rounds: int = 3) -> Path:
    """An observations DB (Spec 015) with rows in every table, written the way the service does:
    one transaction per round after the round."""
    path.parent.mkdir(parents=True, exist_ok=True)
    create(path)
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute(
            "INSERT INTO service_starts(ts,release,pid) VALUES (?,?,?)", (iso(NOW), "abc123", 42)
        )
        for n in range(rounds):
            ts = iso(NOW + 60 * n)
            con.execute("BEGIN IMMEDIATE")
            round_id = con.execute(
                "INSERT INTO rounds(ts,release) VALUES (?,?)", (ts, "abc123")
            ).lastrowid
            con.execute(
                "INSERT INTO books(round_id,ts,symbol,purpose,bid,ask,bid_size,ask_size)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (round_id, ts, "BTC-EUR", "start", 60000.0, 60001.0, 0.5, None),
            )
            con.execute(
                "INSERT INTO snapshots(round_id,ts,symbol,status,message,decided)"
                " VALUES (?,?,?,?,?,?)",
                (round_id, ts, "ETH-EUR", "gap_1m", "gap in recent 1m bars", 0),
            )
            con.execute(
                "INSERT INTO windows(round_id,ts,symbol,interval,rows,first_open,last_open,"
                "forming,last_closed_volume,rows_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (round_id, ts, "BTC-EUR", 1, 120, NOW - 7200, NOW - 60, 1, 1.5, "0" * 64),
            )
            con.execute(
                "INSERT INTO decision_meta(ts,agent,symbol,bar_ts,strategy,strategy_version,"
                "release,indicators) VALUES (?,?,?,?,?,?,?,?)",
                (ts, "trend", "BTC-EUR", NOW + 60 * n, "trend", "v1", "abc123", "{}"),
            )
            con.execute(
                "INSERT INTO llm_attempts(ts,llm_call_id,agent,trigger,trigger_detail,cadence,"
                "raw_text,targets) VALUES (?,?,?,?,?,?,?,?)",
                (ts, n + 1, "luna", "interval", "{}", "{}", "not json", "{}"),
            )
            con.execute("COMMIT")
    finally:
        con.close()
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
