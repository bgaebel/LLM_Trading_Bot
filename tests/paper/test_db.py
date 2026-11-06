"""Spec 004 AK1: versioned schema, no automatic migration."""

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_trading_bot.paper.db import COLUMNS, SCHEMA_VERSION, SchemaError, open_db

AGENTS = ("trend", "buyhold")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fresh_db_gets_schema_version_and_pragmas(tmp_path: Path) -> None:
    con = open_db(tmp_path / "sub" / "bot.db", agents=AGENTS)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 1
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r[0].startswith("sqlite_")
        }
        assert tables == set(COLUMNS)
    finally:
        con.close()


def test_reopen_accepts_own_schema(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    open_db(path, agents=AGENTS).close()
    open_db(path, agents=AGENTS).close()


def _make(path: Path, change: Callable[[sqlite3.Connection], None]) -> None:
    open_db(path, agents=AGENTS).close()
    con = sqlite3.connect(path, isolation_level=None)
    try:
        change(con)
    finally:
        con.close()


def _rebuild_without_unique(con: sqlite3.Connection, table: str, ddl: str) -> None:
    con.executescript(f"DROP TABLE {table}; {ddl}")


@pytest.mark.parametrize(
    ("label", "change", "message"),
    [
        ("foreign version", lambda c: c.execute("PRAGMA user_version=2"), "version 2"),
        ("older version", lambda c: c.execute("PRAGMA user_version=0"), "version 0"),
        ("missing table", lambda c: c.execute("DROP TABLE equity"), "missing tables"),
        (
            "missing column",
            lambda c: c.execute("ALTER TABLE trades DROP COLUMN slip_rate"),
            "trades: columns",
        ),
        (
            "missing decision key",
            lambda c: _rebuild_without_unique(
                c,
                "decisions",
                "CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                " agent TEXT NOT NULL, symbol TEXT NOT NULL, bar_ts INTEGER NOT NULL,"
                " model TEXT NOT NULL, action TEXT NOT NULL, target REAL, confidence REAL,"
                " reason TEXT NOT NULL, inputs TEXT NOT NULL, status TEXT NOT NULL);",
            ),
            "decisions: unique key",
        ),
        (
            "missing equity key",
            lambda c: _rebuild_without_unique(
                c,
                "equity",
                "CREATE TABLE equity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                " agent TEXT NOT NULL, value REAL NOT NULL, cash REAL NOT NULL,"
                " btc REAL NOT NULL, eth REAL NOT NULL);",
            ),
            "equity: unique key",
        ),
    ],
)
def test_wrong_shape_is_refused_and_left_untouched(
    tmp_path: Path, label: str, change: Callable[[sqlite3.Connection], None], message: str
) -> None:
    path = tmp_path / "bot.db"
    _make(path, change)
    before = digest(path)
    with pytest.raises(SchemaError, match=message):
        open_db(path, agents=AGENTS)
    assert digest(path) == before, label


def test_arena_like_db_without_version_is_refused(tmp_path: Path) -> None:
    """The Arena uses user_version 0; its DB must never be adopted or migrated."""
    path = tmp_path / "arena.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE portfolios (agent TEXT PRIMARY KEY, cash REAL NOT NULL)")
    con.execute("INSERT INTO portfolios VALUES ('gpt', 123.0)")
    con.commit()
    con.close()
    before = digest(path)
    with pytest.raises(SchemaError, match="version 0"):
        open_db(path, agents=AGENTS)
    assert digest(path) == before


def test_non_database_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    path.write_bytes(b"not a database, just text" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        open_db(path, agents=AGENTS)
