"""Schema of observations.db (Spec 015, ADR-0027).

What the live bot saw and decided, beyond the trading history in bot.db: the order book per
round, what Bitvavo delivered per interval, why a round made no decision, the strategy version
and release behind each decision, and what GPT answered before it was parsed and clamped.
Measurements only - nothing here feeds back into trading.

ADR-0027: own ``PRAGMA user_version``; another version or shape is refused, never migrated. The
file is created only when it is missing (``create``); a damaged file is never replaced.
"""

import sqlite3
from collections.abc import Mapping
from pathlib import Path

from llm_trading_bot.paper.db import SchemaError

OBS_FILE = "observations.db"
OBS_SCHEMA_VERSION = 1

# Times are ISO-8601 UTC strings like in bot.db; open times of candles are epoch seconds.
_SCHEMA = """
CREATE TABLE service_starts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, release TEXT NOT NULL,
  pid INTEGER NOT NULL);
CREATE TABLE rounds (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, release TEXT NOT NULL);
CREATE TABLE books (
  id INTEGER PRIMARY KEY AUTOINCREMENT, round_id INTEGER NOT NULL, ts TEXT NOT NULL,
  symbol TEXT NOT NULL, purpose TEXT NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL,
  bid_size REAL, ask_size REAL,
  FOREIGN KEY(round_id) REFERENCES rounds(id));
CREATE TABLE snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, round_id INTEGER NOT NULL, ts TEXT NOT NULL,
  symbol TEXT NOT NULL, status TEXT NOT NULL, message TEXT, decided INTEGER NOT NULL,
  FOREIGN KEY(round_id) REFERENCES rounds(id));
CREATE TABLE windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT, round_id INTEGER NOT NULL, ts TEXT NOT NULL,
  symbol TEXT NOT NULL, interval INTEGER NOT NULL, rows INTEGER NOT NULL,
  first_open INTEGER, last_open INTEGER, forming INTEGER NOT NULL,
  last_closed_volume REAL, rows_sha256 TEXT NOT NULL,
  FOREIGN KEY(round_id) REFERENCES rounds(id));
CREATE TABLE decision_meta (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, agent TEXT NOT NULL,
  symbol TEXT NOT NULL, bar_ts INTEGER NOT NULL, strategy TEXT NOT NULL,
  strategy_version TEXT NOT NULL, release TEXT NOT NULL, indicators TEXT NOT NULL,
  UNIQUE(agent,symbol,bar_ts));
CREATE TABLE llm_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, llm_call_id INTEGER NOT NULL,
  agent TEXT NOT NULL, trigger TEXT NOT NULL, trigger_detail TEXT NOT NULL,
  cadence TEXT NOT NULL, raw_text TEXT, targets TEXT NOT NULL,
  UNIQUE(llm_call_id));
CREATE INDEX idx_books_round ON books(round_id);
CREATE INDEX idx_snapshots_round ON snapshots(round_id);
CREATE INDEX idx_windows_round ON windows(round_id);
"""

OBS_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "service_starts": ("id", "ts", "release", "pid"),
    "rounds": ("id", "ts", "release"),
    "books": ("id", "round_id", "ts", "symbol", "purpose", "bid", "ask", "bid_size", "ask_size"),
    "snapshots": ("id", "round_id", "ts", "symbol", "status", "message", "decided"),
    "windows": (
        "id",
        "round_id",
        "ts",
        "symbol",
        "interval",
        "rows",
        "first_open",
        "last_open",
        "forming",
        "last_closed_volume",
        "rows_sha256",
    ),
    "decision_meta": (
        "id",
        "ts",
        "agent",
        "symbol",
        "bar_ts",
        "strategy",
        "strategy_version",
        "release",
        "indicators",
    ),
    "llm_attempts": (
        "id",
        "ts",
        "llm_call_id",
        "agent",
        "trigger",
        "trigger_detail",
        "cadence",
        "raw_text",
        "targets",
    ),
}
OBS_TABLES = tuple(OBS_COLUMNS)


def check_observations_schema(con: sqlite3.Connection) -> None:
    """Raise SchemaError unless ``con`` is an observations DB of exactly this version and shape."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version != OBS_SCHEMA_VERSION:
        raise SchemaError(
            f"observations schema version {version}, expected {OBS_SCHEMA_VERSION}; "
            "no automatic migration"
        )
    tables = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = set(OBS_COLUMNS) - tables
    if missing:
        raise SchemaError(f"observations: missing tables {sorted(missing)}")
    for table, expected in OBS_COLUMNS.items():
        actual = tuple(r[1] for r in con.execute(f"PRAGMA table_info({table})"))
        if actual != expected:
            raise SchemaError(f"observations.{table}: columns {actual}, expected {expected}")


def create(path: Path) -> None:
    """Create a fresh observations DB at ``path``; refuses if anything already exists there.

    ADR-0027: only a missing file is created - an existing (maybe damaged) one is never replaced.
    """
    if path.exists():
        raise FileExistsError(f"{path} exists - observations.db is never recreated")
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(
            f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={OBS_SCHEMA_VERSION};COMMIT;"
        )
        check_observations_schema(con)
    finally:
        con.close()
