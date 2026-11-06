# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - open_db schema, balance
"""SQLite store for the paper bot.

ADR-0012: own schema with ``PRAGMA user_version``; version 2 adds ``llm_calls`` (Spec 005,
ADR-0013). Columns and idempotency keys follow the Arena so results stay comparable; tables the bot
does not need (ticks, logs) are left out. A database with another version or a different shape is
refused, never migrated, because a silent migration could rewrite the trading history.
"""

import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
START_CASH = 10_000.0
BUSY_TIMEOUT_MS = 10_000

_SCHEMA = """
CREATE TABLE portfolios (
  agent TEXT PRIMARY KEY, cash REAL NOT NULL, btc REAL NOT NULL DEFAULT 0,
  eth REAL NOT NULL DEFAULT 0, peak REAL NOT NULL, halted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, agent TEXT NOT NULL,
  symbol TEXT NOT NULL, bar_ts INTEGER NOT NULL, model TEXT NOT NULL,
  action TEXT NOT NULL, target REAL, confidence REAL, reason TEXT NOT NULL,
  inputs TEXT NOT NULL, status TEXT NOT NULL,
  UNIQUE(agent,symbol,bar_ts));
CREATE TABLE trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, agent TEXT NOT NULL,
  symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, fill REAL NOT NULL,
  notional REAL NOT NULL, fee REAL NOT NULL, slip_rate REAL NOT NULL,
  decision_id INTEGER, FOREIGN KEY(decision_id) REFERENCES decisions(id));
CREATE TABLE market (
  symbol TEXT PRIMARY KEY, ts TEXT NOT NULL, bar_ts INTEGER NOT NULL,
  bid REAL NOT NULL, ask REAL NOT NULL, last_close REAL NOT NULL, source TEXT NOT NULL);
CREATE TABLE equity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, agent TEXT NOT NULL,
  value REAL NOT NULL, cash REAL NOT NULL, btc REAL NOT NULL, eth REAL NOT NULL,
  UNIQUE(ts,agent));
CREATE TABLE llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, agent TEXT NOT NULL,
  markets TEXT NOT NULL, ref_mid_btc REAL, ref_mid_eth REAL, status TEXT NOT NULL,
  model TEXT NOT NULL, prompt_version TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
  max_output_tokens INTEGER NOT NULL, effort TEXT NOT NULL, context TEXT NOT NULL,
  reserved_eur TEXT, cost_eur TEXT, reservation_exceeded INTEGER NOT NULL DEFAULT 0,
  response_id TEXT, input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
  cached_tokens INTEGER, latency_seconds REAL, error TEXT);
CREATE INDEX idx_llm_calls_agent ON llm_calls(agent,id);
"""

COLUMNS: Mapping[str, tuple[str, ...]] = {
    "portfolios": ("agent", "cash", "btc", "eth", "peak", "halted"),
    "decisions": (
        "id",
        "ts",
        "agent",
        "symbol",
        "bar_ts",
        "model",
        "action",
        "target",
        "confidence",
        "reason",
        "inputs",
        "status",
    ),
    "trades": (
        "id",
        "ts",
        "agent",
        "symbol",
        "side",
        "qty",
        "fill",
        "notional",
        "fee",
        "slip_rate",
        "decision_id",
    ),
    "market": ("symbol", "ts", "bar_ts", "bid", "ask", "last_close", "source"),
    "equity": ("id", "ts", "agent", "value", "cash", "btc", "eth"),
    # One row per LLM request (both markets): budget ledger and provenance in one place (ADR-0013).
    "llm_calls": (
        "id",
        "ts",
        "agent",
        "markets",
        "ref_mid_btc",
        "ref_mid_eth",
        "status",
        "model",
        "prompt_version",
        "prompt_sha256",
        "max_output_tokens",
        "effort",
        "context",
        "reserved_eur",
        "cost_eur",
        "reservation_exceeded",
        "response_id",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "latency_seconds",
        "error",
    ),
}

# Restart safety and upserts depend on these keys; a DB without them could duplicate trades.
UNIQUE_KEYS: Mapping[str, tuple[str, ...]] = {
    "portfolios": ("agent",),
    "decisions": ("agent", "symbol", "bar_ts"),
    "market": ("symbol",),
    "equity": ("ts", "agent"),
}


class SchemaError(RuntimeError):
    """The database is not a bot database of the expected version; nothing was changed."""


@contextmanager
def write_tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One IMMEDIATE transaction: commit on success, roll everything back on any error."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        # SQLite may already have rolled back by itself (e.g. disk full); a second ROLLBACK
        # would raise and hide the original error.
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _unique_keys(con: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    keys = set()
    for index in con.execute(f"PRAGMA index_list({table})").fetchall():
        if index["unique"]:
            cols = con.execute(f"PRAGMA index_info({index['name']})").fetchall()
            keys.add(tuple(c["name"] for c in sorted(cols, key=lambda c: c["seqno"])))
    return keys


def check_schema(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"schema version {version}, expected {SCHEMA_VERSION}; no automatic migration"
        )
    missing = set(COLUMNS) - _tables(con)
    if missing:
        raise SchemaError(f"missing tables: {sorted(missing)}")
    for table, expected in COLUMNS.items():
        actual = tuple(r["name"] for r in con.execute(f"PRAGMA table_info({table})"))
        if actual != expected:
            raise SchemaError(f"{table}: columns {actual}, expected {expected}")
    for table, key in UNIQUE_KEYS.items():
        if key not in _unique_keys(con, table):
            raise SchemaError(f"{table}: unique key {key} missing")


def open_db(path: Path, *, agents: Iterable[str]) -> sqlite3.Connection:
    """Open (or create) the bot database and make sure every agent has a portfolio.

    Precondition: the caller holds the WriterLock for ``path`` (single writer, CLAUDE.md).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Autocommit mode: every write goes through write_tx, so there are no implicit transactions.
    con = sqlite3.connect(path, timeout=20, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and not _tables(con):
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(
                f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={SCHEMA_VERSION};COMMIT;"
            )
        check_schema(con)
        con.execute("PRAGMA journal_mode=WAL")
        with write_tx(con):
            for agent in agents:
                # OR IGNORE: reopening never resets an existing portfolio (CLAUDE.md: history).
                con.execute(
                    "INSERT OR IGNORE INTO portfolios (agent,cash,peak) VALUES (?,?,?)",
                    (agent, START_CASH, START_CASH),
                )
    except BaseException:
        con.close()
        raise
    return con


def balance(con: sqlite3.Connection, agent: str) -> dict[str, Any]:
    row = con.execute("SELECT * FROM portfolios WHERE agent=?", (agent,)).fetchone()
    if row is None:
        raise KeyError(f"no portfolio for agent {agent!r}")
    return dict(row)
