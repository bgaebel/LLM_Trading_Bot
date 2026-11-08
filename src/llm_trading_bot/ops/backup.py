# Origin: LLM-Trading-Arena@7563724 apps/arena/backup.py and operations.py (adapted)
"""Backups of the bot database (Spec 009, ADR-0020) and of observations.db (Spec 015, ADR-0027).

A backup reads the database through a read-only connection (ADR-0016: never the WriterLock, the
service keeps running) and copies it with SQLite's online backup API inside one read
transaction, so the copy is one committed state even while the service writes. The copy is
checked (schema, ``integrity_check``, unique keys, row counts equal to the source snapshot)
before it becomes visible under its final name, which is never overwritten. Rotation removes
only this module's own copies in that directory. A restore writes only to a path that does not
exist yet: a backup is never copied over a database (CLAUDE.md: history is never reset silently).

Each kind of database is a ``Profile``: its schema check, unique keys and the prefix of its file
names. Rotation and the clean-up of temporary files only ever touch the names of one profile, so
copies of bot.db and observations.db never remove each other.
"""

import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from llm_trading_bot.paper.db import COLUMNS, UNIQUE_KEYS, SchemaError, check_schema
from llm_trading_bot.paper.observations_schema import OBS_TABLES, check_observations_schema

KEEP = 14
KEEP_OBSERVATIONS = 7
"""observations.db grows about 2 MB a day (Spec 015): a week of copies keeps the disk in check."""
SIDECARS = ("-wal", "-shm", "-journal")
STALE_SECONDS = 3600
"""A temporary copy older than this belongs to a backup that was killed; rotation removes it."""


class BackupError(RuntimeError):
    """The backup, check or restore failed; nothing was published or overwritten."""


@dataclass(frozen=True)
class Profile:
    """One kind of database: how a copy of it is checked and what its files are called."""

    prefix: str
    tables: tuple[str, ...]
    unique_keys: Mapping[str, tuple[str, ...]]
    check_schema: Callable[[sqlite3.Connection], None]

    @property
    def name(self) -> re.Pattern[str]:
        """Names this profile writes; rotation never touches anything else in the directory."""
        return re.compile(rf"^{re.escape(self.prefix)}-(\d{{8}}T\d{{6}}Z)(?:-(\d+))?\.db$")

    @property
    def temporary(self) -> str:
        """Glob of this profile's temporary copies (``.<prefix>-<UTC>.<pid>.tmp``)."""
        return f".{self.prefix}-*.tmp"


def _bot_schema(con: sqlite3.Connection) -> None:
    # Looked up at call time, so a test can take the schema check away and reach the key check.
    check_schema(con)


BOT = Profile("bot", tuple(COLUMNS), UNIQUE_KEYS, _bot_schema)
# The UNIQUE constraints of observations_schema: a copy that lost one must not pass as good.
OBS_UNIQUE_KEYS: Mapping[str, tuple[str, ...]] = {
    "decision_meta": ("agent", "symbol", "bar_ts"),
    "llm_attempts": ("llm_call_id",),
}
OBSERVATIONS = Profile("observations", OBS_TABLES, OBS_UNIQUE_KEYS, check_observations_schema)
PROFILES: Mapping[str, Profile] = {p.prefix: p for p in (BOT, OBSERVATIONS)}
NAME = BOT.name
"""Names of the bot.db copies (the default profile)."""


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


def _rows(con: sqlite3.Connection, profile: Profile) -> dict[str, int]:
    return {t: int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in profile.tables}


def _check(con: sqlite3.Connection, path: Path, profile: Profile) -> DbCheck:
    try:
        profile.check_schema(con)
    except SchemaError as exc:
        raise BackupError(f"{path.name}: {exc}") from exc
    result = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
    if result != ["ok"]:
        raise BackupError(f"{path.name}: integrity_check: {'; '.join(result[:3])}")
    for table, key in profile.unique_keys.items():
        columns = ", ".join(key)
        duplicate = con.execute(
            f"SELECT {columns} FROM {table} GROUP BY {columns} HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate is not None:
            raise BackupError(f"{path.name}: duplicate {table} key {tuple(duplicate)}")
    return DbCheck(path, _rows(con, profile))


def verify_db(path: Path, profile: Profile = BOT) -> DbCheck:
    """Read-only check of a database or a backup of ``profile``; raises BackupError."""
    con = _read_only(path)
    try:
        con.execute("BEGIN")  # one snapshot for all checks
        return _check(con, path, profile)
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


def _private_dir(directory: Path) -> None:
    """``directory`` and every missing parent with mode 700 (e.g. ``observations/daily``)."""
    missing: list[Path] = []
    path = directory
    while not path.exists():
        missing.append(path)
        path = path.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700, exist_ok=True)
    if not directory.is_dir():
        raise BackupError(f"{directory} is not a directory")


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


def rotate(directory: Path, keep: int, profile: Profile = BOT) -> list[Path]:
    """Remove the oldest of ``profile``'s copies beyond ``keep``; returns what was removed."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    pattern = profile.name
    own = sorted(
        (p for p in directory.iterdir() if p.is_file() and pattern.match(p.name)),
        key=lambda p: _age(pattern, p),
    )
    removed = own[: max(0, len(own) - keep)]
    for path in removed:
        _drop(path)
    # A backup killed hard (SIGKILL, power loss, unit timeout) leaves its temporary file behind.
    for path in directory.glob(profile.temporary):
        if time.time() - path.stat().st_mtime > STALE_SECONDS:
            _drop(path)
            removed.append(path)
    return removed


def _age(pattern: re.Pattern[str], path: Path) -> tuple[str, int]:
    """Oldest first: by the time in the name, then "-1", "-2" after the copy without suffix."""
    match = pattern.match(path.name)
    assert match is not None
    return match.group(1), int(match.group(2) or 0)


def backup_db(
    db: Path,
    directory: Path,
    *,
    keep: int = KEEP,
    clock: Callable[[], float] = time.time,
    profile: Profile = BOT,
) -> DbCheck:
    """Checked copy of ``db`` in ``directory`` (mode 700), then rotation; raises BackupError."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    _private_dir(directory)
    stamp = datetime.fromtimestamp(clock(), UTC).strftime("%Y%m%dT%H%M%SZ")
    temporary = directory / f".{profile.prefix}-{stamp}.{os.getpid()}.tmp"
    source = _read_only(db)
    try:
        try:
            source.execute("BEGIN")  # the counts and the copy see the same committed state
            expected = _rows(source, profile)
            _copy(source, temporary)
        except sqlite3.Error as exc:
            raise BackupError(f"backup of {db.name} failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if source.in_transaction:
                source.execute("ROLLBACK")
        copy = verify_db(temporary, profile)
        if copy.rows != expected:
            raise BackupError(f"copy has rows {copy.rows}, source snapshot had {expected}")
        final = _publish(temporary, directory / f"{profile.prefix}-{stamp}.db")
    except BaseException:
        _drop(temporary)
        raise
    finally:
        source.close()
    rotate(directory, keep, profile)
    return DbCheck(final, copy.rows)


def restore_db(backup: Path, target: Path, profile: Profile = BOT) -> DbCheck:
    """Checked copy of ``backup`` at ``target``, which must not exist yet; raises BackupError."""
    if any(Path(f"{target}{s}").exists() for s in ("", *SIDECARS)):
        raise BackupError(f"{target} already exists - a restore never overwrites a database")
    checked = verify_db(backup, profile)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    source = _read_only(backup)
    try:
        try:
            _copy(source, temporary)
        except sqlite3.Error as exc:
            raise BackupError(f"restore failed: {type(exc).__name__}: {exc}") from exc
        copy = verify_db(temporary, profile)
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
