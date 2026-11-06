"""Spec 007 AK2 / ADR-0016: the web UI reads the bot database; never writes, creates, migrates."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from llm_trading_bot.paper.db import open_db, write_tx
from llm_trading_bot.paper.execution import BUYHOLD
from llm_trading_bot.paper.lock import WriterLock
from llm_trading_bot.web.readonly import DbUnavailable, reading


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "bot dir" / "bot.db"  # a space proves the URI is encoded
    con = open_db(path, agents=("trend", BUYHOLD))
    con.close()
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reads_the_portfolios(tmp_path: Path) -> None:
    with reading(_db(tmp_path)) as con:
        assert {r["agent"] for r in con.execute("SELECT agent FROM portfolios")} == {
            "trend",
            BUYHOLD,
        }
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE portfolios SET cash=0",
        "INSERT INTO market VALUES ('BTCEUR','t',0,1,2,3,'x')",
        "CREATE TABLE intruder (a)",
        "PRAGMA user_version=3",
    ],
)
def test_every_write_is_refused(tmp_path: Path, statement: str) -> None:
    with reading(_db(tmp_path)) as con, pytest.raises(sqlite3.OperationalError):
        con.execute(statement)


def test_reading_leaves_the_database_file_unchanged(tmp_path: Path) -> None:
    path = _db(tmp_path)
    before = _digest(path), path.stat().st_size
    for _ in range(20):
        with reading(path) as con:
            con.execute("SELECT * FROM equity").fetchall()
    assert (_digest(path), path.stat().st_size) == before


def test_reader_sees_the_last_committed_state_while_the_bot_writes(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with WriterLock(path):
        writer = open_db(path, agents=())
        try:
            with write_tx(writer):
                writer.execute("UPDATE portfolios SET cash=1 WHERE agent='trend'")
                with reading(path) as con:
                    cash = con.execute("SELECT cash FROM portfolios WHERE agent='trend'")
                    assert cash.fetchone()[0] == 10_000.0
            with reading(path) as con:
                cash = con.execute("SELECT cash FROM portfolios WHERE agent='trend'")
                assert cash.fetchone()[0] == 1.0
        finally:
            writer.close()


def test_one_page_view_is_one_snapshot(tmp_path: Path) -> None:
    path = _db(tmp_path)
    writer = open_db(path, agents=())
    try:
        with reading(path) as con:
            first = con.execute("SELECT cash FROM portfolios WHERE agent='trend'").fetchone()[0]
            with write_tx(writer):
                writer.execute("UPDATE portfolios SET cash=2 WHERE agent='trend'")
            again = con.execute("SELECT cash FROM portfolios WHERE agent='trend'").fetchone()[0]
        assert first == again == 10_000.0
    finally:
        writer.close()


def test_missing_database_is_reported_and_not_created(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    with pytest.raises(DbUnavailable, match="Noch keine Daten"), reading(path):
        pass
    assert not path.exists()


def test_other_schema_version_is_shown_not_migrated(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE portfolios (agent TEXT)")
    old.execute("PRAGMA user_version=1")
    old.commit()
    old.close()
    before = _digest(path)
    with pytest.raises(DbUnavailable, match="schema version 1"), reading(path):
        pass
    assert _digest(path) == before


def test_a_file_that_is_no_database_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    path.write_bytes(b"not a database at all" * 100)
    with pytest.raises(DbUnavailable, match="nicht lesbar"), reading(path):
        pass
    assert path.read_bytes() == b"not a database at all" * 100
