"""Spec 009 AK2: read-only checks, and restores only to a path that does not exist yet."""

import io
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from llm_trading_bot.cli import EXIT_INVALID, EXIT_OK, main, run_restore, run_verify_db
from llm_trading_bot.ops.backup import BackupError, backup_db, restore_db, verify_db
from ops_fakes import bot_db, digest


def test_restore_writes_a_checked_copy_to_a_new_path(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    copy = backup_db(db, tmp_path / "backups")
    target = tmp_path / "restore-test" / "bot.db"
    restored = restore_db(copy.path, target)
    assert restored.rows == copy.rows == verify_db(db).rows
    assert sorted(p.name for p in target.parent.iterdir()) == ["bot.db"]
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("existing", ["", "-wal"])
def test_restore_never_overwrites_a_database(tmp_path: Path, existing: str) -> None:
    db = bot_db(tmp_path / "bot.db")
    copy = backup_db(db, tmp_path / "backups")
    target = tmp_path / "live" / "bot.db"
    target.parent.mkdir()
    Path(f"{target}{existing}").write_bytes(b"the live database")
    before = digest(Path(f"{target}{existing}"))
    with pytest.raises(BackupError, match="never overwrites"):
        restore_db(copy.path, target)
    assert digest(Path(f"{target}{existing}")) == before
    assert sorted(p.name for p in target.parent.iterdir()) == [f"bot.db{existing}"]


def test_a_broken_backup_is_not_restored(tmp_path: Path) -> None:
    broken = tmp_path / "bot-20270101T000000Z.db"
    broken.write_bytes(b"\x00" * 4096)
    target = tmp_path / "restore" / "bot.db"
    with pytest.raises(BackupError):
        restore_db(broken, target)
    assert not target.exists()


def test_verify_db_reads_only_and_reports_rows(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    before = digest(db)
    checked = verify_db(db)
    assert checked.rows["portfolios"] == 2 and checked.rows["equity"] == 4
    assert digest(db) == before


def test_verify_db_refuses_another_schema(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version=1")
    con.close()
    with pytest.raises(BackupError, match="schema version 1, expected 2"):
        verify_db(db)


def test_verify_db_cli(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    out = io.StringIO()
    assert run_verify_db(db, out=out) == EXIT_OK
    assert "bot.db: schema ok, integrity ok, unique keys ok; rows: portfolios 2" in out.getvalue()
    out = io.StringIO()
    assert run_verify_db(tmp_path / "missing.db", out=out) == EXIT_INVALID
    assert "verify failed: no database at" in out.getvalue()
    assert main(["verify-db", "--db", str(db)]) == EXIT_OK


def test_restore_cli_refuses_an_existing_target(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    copy = backup_db(db, tmp_path / "backups")
    out = io.StringIO()
    assert run_restore(copy.path, db, out=out) == EXIT_INVALID
    assert "restore refused:" in out.getvalue() and "never overwrites" in out.getvalue()
    out = io.StringIO()
    assert run_restore(copy.path, tmp_path / "new" / "bot.db", out=out) == EXIT_OK
    assert "restored bot-" in out.getvalue()
