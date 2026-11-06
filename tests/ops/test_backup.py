"""Spec 009 AK1: checked online backups, never overwritten, rotation of own copies only."""

import io
import os
import sqlite3
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from golden_scenarios import NOW
from llm_trading_bot.cli import EXIT_INVALID, EXIT_OK, main, run_backup
from llm_trading_bot.llm.due import iso
from llm_trading_bot.ops.backup import NAME, BackupError, backup_db, rotate, verify_db
from llm_trading_bot.paper.lock import WriterLock
from ops_fakes import at, bot_db, digest


def test_backup_is_a_checked_copy_of_the_committed_state(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "data" / "bot.db")
    before = verify_db(db).rows
    writer = sqlite3.connect(db)
    try:
        # The service is in the middle of a round: an open write transaction with a new row.
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO equity(ts,agent,value,cash,btc,eth) VALUES (?,?,1,1,0,0)",
            (iso(NOW + 3600), "trend"),
        )
        done = backup_db(db, tmp_path / "backups", clock=at(NOW))
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert done.rows == before and done.rows["equity"] > 0 and done.rows["trades"] > 0
    stamp = datetime.fromtimestamp(NOW, UTC).strftime("%Y%m%dT%H%M%SZ")
    assert NAME.match(done.path.name) and done.path.name == f"bot-{stamp}.db"
    assert verify_db(done.path).rows == before
    # One self-contained file: no WAL or temporary files beside it.
    assert sorted(p.name for p in (tmp_path / "backups").iterdir()) == [done.path.name]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_backup_and_its_directory_are_private(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    done = backup_db(db, tmp_path / "backups")
    assert stat.S_IMODE(done.path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700


def test_backup_runs_while_the_service_holds_the_writer_lock(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    with WriterLock(db):  # ADR-0016: a reader, never a second writer
        done = backup_db(db, tmp_path / "backups")
    assert done.path.exists()


def test_a_backup_never_overwrites_one_from_the_same_second(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    first = backup_db(db, tmp_path / "backups", clock=at(NOW))
    original = digest(first.path)
    second = backup_db(db, tmp_path / "backups", clock=at(NOW))
    assert second.path.name == first.path.name.replace(".db", "-1.db")
    assert digest(first.path) == original


def test_rotation_removes_only_the_oldest_own_copies(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    directory = tmp_path / "backups"
    directory.mkdir()
    foreign = ["notes.txt", "bot-manual.db", "bot-20260101T000000Z.db.keep"]
    for name in foreign:
        (directory / name).write_text("x", encoding="utf-8")
    made = [backup_db(db, directory, keep=3, clock=at(NOW + day * 86400)) for day in range(5)]
    left = sorted(p.name for p in directory.iterdir())
    assert left == sorted([*foreign, *(m.path.name for m in made[-3:])])


def test_rotation_needs_at_least_one_copy(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        rotate(tmp_path, 0)


@pytest.mark.parametrize("problem", ["missing", "other schema", "not a database"])
def test_a_failed_backup_publishes_and_deletes_nothing(tmp_path: Path, problem: str) -> None:
    good = bot_db(tmp_path / "good.db")
    directory = tmp_path / "backups"
    kept = [backup_db(good, directory, keep=2, clock=at(NOW + d * 86400)) for d in range(2)]
    db = tmp_path / "bot.db"
    if problem == "other schema":
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE trades(id INTEGER)")
        con.execute("PRAGMA user_version=1")
        con.close()
    elif problem == "not a database":
        db.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(BackupError):
        backup_db(db, directory, keep=1, clock=at(NOW + 5 * 86400))
    assert sorted(p.name for p in directory.iterdir()) == sorted(k.path.name for k in kept)
    if problem == "missing":
        assert not db.exists()  # a read-only open never creates the database


def test_cli_backup_reports_the_copy_and_refuses_bad_input(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    out = io.StringIO()
    assert run_backup(db, tmp_path / "backups", keep=14, out=out) == EXIT_OK
    assert "schema ok, integrity ok, unique keys ok; rows: portfolios 2" in out.getvalue()
    out = io.StringIO()
    assert run_backup(tmp_path / "nope.db", tmp_path / "backups", keep=14, out=out) == EXIT_INVALID
    assert out.getvalue().startswith("backup failed: no database at")
    assert main(["backup", "--db", str(db), "--dir", str(tmp_path / "b2"), "--keep", "0"]) == 1
    assert not (tmp_path / "b2").exists()


def test_backup_leaves_the_source_untouched(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    before = digest(db)
    backup_db(db, tmp_path / "backups")
    assert digest(db) == before
    assert os.path.getsize(db) > 0
