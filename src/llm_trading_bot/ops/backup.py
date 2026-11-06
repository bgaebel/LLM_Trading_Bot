# Origin: LLM-Trading-Arena@7563724 apps/arena/backup.py and operations.py (adapted)
"""Backups of the bot database (Spec 009, ADR-0018).

A backup reads the database through a read-only connection (ADR-0016: never the WriterLock, the
service keeps running) and copies it with SQLite's online backup API inside one read
transaction, so the copy is one committed state even while the service writes. The copy is
checked (schema, ``integrity_check``, unique keys, row counts equal to the source snapshot)
before it becomes visible under its final name, which is never overwritten. Rotation removes
only this module's own copies in that directory. A restore writes only to a path that does not
exist yet: a backup is never copied over a database (CLAUDE.md: history is never reset silently).
"""

import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from llm_trading_bot.paper.db import COLUMNS, UNIQUE_KEYS, SchemaError, check_schema

KEEP = 14
NAME = re.compile(r"^bot-\d{8}T\d{6}Z(?:-\d+)?\.db$")
"""Names this module writes; rotation never touches anything else in the directory."""
SIDECARS = ("-wal", "-shm", "-journal")


class BackupError(RuntimeError):
    """The backup, check or restore failed; nothing was published or overwritten."""


@dataclass(frozen=True)
class DbCheck:
    path: Path
    rows: dict[str, int]

    def summary(self) -> str:
        counts = ", ".join(f"{table} {n}" for table, n in self.rows.items())
        return f"schema ok, integrity ok, unique keys ok; rows: {counts}"


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise BackupError(f"no database at {path}")
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise BackupError(f"cannot open {path}: {type(exc).__name__}: {exc}") from exc
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=1")
    return con


def _rows(con: sqlite3.Connection) -> dict[str, int]:
    return {t: int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in COLUMNS}


def _check(con: sqlite3.Connection, path: Path) -> DbCheck:
    try:
        check_schema(con)
    except SchemaError as exc:
        raise BackupError(f"{path.name}: {exc}") from exc
    result = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
    if result != ["ok"]:
        raise BackupError(f"{path.name}: integrity_check: {'; '.join(result[:3])}")
    for table, key in UNIQUE_KEYS.items():
        columns = ", ".join(key)
        duplicate = con.execute(
            f"SELECT {columns} FROM {table} GROUP BY {columns} HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate is not None:
            raise BackupError(f"{path.name}: duplicate {table} key {tuple(duplicate)}")
    return DbCheck(path, _rows(con))


def verify_db(path: Path) -> DbCheck:
    """Read-only check of a bot database or a backup; raises BackupError."""
    con = _read_only(path)
    try:
        con.execute("BEGIN")  # one snapshot for all checks
        return _check(con, path)
    except sqlite3.Error as exc:
        raise BackupError(f"{path.name}: {type(exc).__name__}: {exc}") from exc
    finally:
        if con.in_transaction:
            con.execute("ROLLBACK")
        con.close()


def _private_empty(path: Path) -> None:
    """Create ``path`` empty with mode 600 before SQLite writes anything into it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)


def _fsync(path: Path) -> None:
    # A writable handle: Windows refuses fsync on a read-only one.
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if sys.platform != "win32":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _drop(path: Path) -> None:
    for suffix in ("", *SIDECARS):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _copy(source: sqlite3.Connection, temporary: Path) -> None:
    """Online backup into ``temporary``; the copy is a self-contained file (no WAL beside it)."""
    _private_empty(temporary)
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
        # The copy inherits WAL mode from the page header; switch it off so a single file is
        # the whole backup and checking it leaves no -wal/-shm behind.
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()


def _publish(temporary: Path, final: Path) -> Path:
    """Make ``temporary`` visible as ``final`` (or ``final-N``) without overwriting anything."""
    candidate, n = final, 0
    while True:
        try:
            os.link(temporary, candidate)
            break
        except FileExistsError:
            n += 1
            candidate = final.with_name(f"{final.stem}-{n}{final.suffix}")
    temporary.unlink()
    _fsync(candidate)
    return candidate


def rotate(directory: Path, keep: int) -> list[Path]:
    """Remove the oldest of this module's copies beyond ``keep``; returns what was removed."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    own = sorted(p for p in directory.iterdir() if p.is_file() and NAME.match(p.name))
    removed = own[: max(0, len(own) - keep)]
    for path in removed:
        _drop(path)
    return removed


def backup_db(
    db: Path, directory: Path, *, keep: int = KEEP, clock: Callable[[], float] = time.time
) -> DbCheck:
    """Checked copy of ``db`` in ``directory`` (mode 700), then rotation; raises BackupError."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(clock(), UTC).strftime("%Y%m%dT%H%M%SZ")
    temporary = directory / f".bot-{stamp}.{os.getpid()}.tmp"
    source = _read_only(db)
    try:
        try:
            source.execute("BEGIN")  # the counts and the copy see the same committed state
            expected = _rows(source)
            _copy(source, temporary)
        except sqlite3.Error as exc:
            raise BackupError(f"backup of {db.name} failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if source.in_transaction:
                source.execute("ROLLBACK")
        copy = verify_db(temporary)
        if copy.rows != expected:
            raise BackupError(f"copy has rows {copy.rows}, source snapshot had {expected}")
        final = _publish(temporary, directory / f"bot-{stamp}.db")
    except BaseException:
        _drop(temporary)
        raise
    finally:
        source.close()
    rotate(directory, keep)
    return DbCheck(final, copy.rows)


def restore_db(backup: Path, target: Path) -> DbCheck:
    """Checked copy of ``backup`` at ``target``, which must not exist yet; raises BackupError."""
    if any(Path(f"{target}{s}").exists() for s in ("", *SIDECARS)):
        raise BackupError(f"{target} already exists - a restore never overwrites a database")
    checked = verify_db(backup)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    source = _read_only(backup)
    try:
        try:
            _copy(source, temporary)
        except sqlite3.Error as exc:
            raise BackupError(f"restore failed: {type(exc).__name__}: {exc}") from exc
        copy = verify_db(temporary)
        if copy.rows != checked.rows:
            raise BackupError(f"restored rows {copy.rows}, backup has {checked.rows}")
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise BackupError(f"{target} appeared meanwhile - nothing overwritten") from exc
        temporary.unlink()
        _fsync(target)
    except BaseException:
        _drop(temporary)
        raise
    finally:
        source.close()
    return DbCheck(target, copy.rows)
