"""Spec 009 AK1: checked online backups, never overwritten, rotation of own copies only.
Spec 015 AK6: the same for observations.db in its own profile and its own invocation."""

import io
import os
import shlex
import sqlite3
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from golden_scenarios import NOW
from llm_trading_bot.cli import EXIT_INVALID, EXIT_OK, main, run_backup, run_restore
from llm_trading_bot.llm.due import iso
from llm_trading_bot.ops.backup import (
    KEEP,
    NAME,
    OBSERVATIONS,
    BackupError,
    backup_db,
    restore_db,
    rotate,
    verify_db,
)
from llm_trading_bot.paper.lock import WriterLock
from llm_trading_bot.paper.observations_schema import OBS_TABLES, create
from ops_fakes import at, bot_db, digest, obs_db

UNIT = Path(__file__).resolve().parents[2] / "deploy" / "llm-trading-bot-backup.service"


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


def test_rotation_orders_copies_of_the_same_second_by_their_suffix(tmp_path: Path) -> None:
    db = bot_db(tmp_path / "bot.db")
    made = [backup_db(db, tmp_path / "backups", keep=14, clock=at(NOW)) for _ in range(3)]
    assert [m.path.name.split("Z")[-1] for m in made] == [".db", "-1.db", "-2.db"]
    rotate(tmp_path / "backups", 2)
    left = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert left == sorted(m.path.name for m in made[1:])  # the oldest (no suffix) went first


def test_rotation_removes_temporary_files_of_killed_backups(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    directory.mkdir()
    stale = directory / ".bot-20270101T000000Z.123.tmp"
    fresh = directory / ".bot-20270101T000100Z.456.tmp"
    for path in (stale, fresh):
        path.write_bytes(b"half a copy")
    old = time.time() - 2 * 3600
    os.utime(stale, (old, old))
    rotate(directory, 14)
    assert not stale.exists() and fresh.exists()  # a running backup's file stays


def test_verify_db_finds_duplicate_keys_even_past_the_schema_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Defence in depth: a database whose unique index was lost must not pass as a good backup.
    import llm_trading_bot.ops.backup as backup

    db = bot_db(tmp_path / "bot.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE copy AS SELECT * FROM portfolios")
    con.execute("DROP TABLE portfolios")
    con.execute("ALTER TABLE copy RENAME TO portfolios")
    con.execute("INSERT INTO portfolios SELECT * FROM portfolios LIMIT 1")
    con.commit()
    con.close()
    monkeypatch.setattr(backup, "check_schema", lambda con: None)
    with pytest.raises(BackupError, match="duplicate portfolios key"):
        verify_db(db)


# ------------------------------------------------------------------ Spec 015 AK6: observations.db


def stamp_of(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y%m%dT%H%M%SZ")


def test_observations_profile_copy_is_checked_and_named_on_its_own(tmp_path: Path) -> None:
    db = obs_db(tmp_path / "data" / "observations.db")
    before = verify_db(db, OBSERVATIONS).rows
    writer = sqlite3.connect(db)
    try:
        # The service writes its buffer after a round: an open transaction is not in the copy.
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO rounds(ts,release) VALUES (?,?)", (iso(NOW + 3600), "x"))
        directory = tmp_path / "backups" / "observations" / "daily"
        done = backup_db(db, directory, clock=at(NOW), profile=OBSERVATIONS)
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert done.path.name == f"observations-{stamp_of(NOW)}.db"
    assert OBSERVATIONS.name.match(done.path.name) and not NAME.match(done.path.name)
    assert done.rows == before and set(before) == set(OBS_TABLES) and before["rounds"] == 3
    assert verify_db(done.path, OBSERVATIONS).rows == before
    assert sorted(p.name for p in directory.iterdir()) == [done.path.name]
    if sys.platform != "win32":
        assert stat.S_IMODE(done.path.stat().st_mode) == 0o600
        for d in (directory, directory.parent):  # observations/ too, whatever the umask
            assert stat.S_IMODE(d.stat().st_mode) == 0o700, d


def test_observations_profile_runs_while_the_service_holds_the_writer_lock(tmp_path: Path) -> None:
    # ADR-0027: the service owns bot.db's lock and writes observations.db; a backup only reads.
    bot = bot_db(tmp_path / "bot.db")
    db = obs_db(tmp_path / "observations.db")
    with WriterLock(bot):
        done = backup_db(db, tmp_path / "backups", profile=OBSERVATIONS)
    assert done.path.exists()


@pytest.mark.parametrize("problem", ["v99", "bot.db", "corrupt", "duplicate key"])
def test_observations_profile_refuses_what_is_no_observations_db(
    tmp_path: Path, problem: str
) -> None:
    directory = tmp_path / "backups"
    good = obs_db(tmp_path / "good.db")
    kept = backup_db(good, directory, clock=at(NOW), profile=OBSERVATIONS)
    db = tmp_path / "observations.db"
    if problem == "v99":
        create(db)
        con = sqlite3.connect(db)
        con.execute("PRAGMA user_version=99")
        con.close()
    elif problem == "bot.db":
        bot_db(db)
    elif problem == "corrupt":
        db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 8000)
    else:
        # Same columns, so the schema check passes; only the key check finds the lost UNIQUE.
        obs_db(db)
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE copy AS SELECT * FROM llm_attempts")
        con.execute("DROP TABLE llm_attempts")
        con.execute("ALTER TABLE copy RENAME TO llm_attempts")
        con.execute("INSERT INTO llm_attempts SELECT * FROM llm_attempts LIMIT 1")
        con.commit()
        con.close()
    reason = {"duplicate key": "duplicate llm_attempts key"}.get(problem, "")
    with pytest.raises(BackupError, match=reason):
        backup_db(db, directory, keep=1, clock=at(NOW + 86400), profile=OBSERVATIONS)
    assert sorted(p.name for p in directory.iterdir()) == [kept.path.name]


def test_observations_profile_and_bot_profile_refuse_each_others_files(tmp_path: Path) -> None:
    bot = backup_db(bot_db(tmp_path / "bot.db"), tmp_path / "b")
    obs = backup_db(obs_db(tmp_path / "observations.db"), tmp_path / "o", profile=OBSERVATIONS)
    with pytest.raises(BackupError, match="schema version"):
        verify_db(obs.path)
    with pytest.raises(BackupError, match="observations schema version"):
        verify_db(bot.path, OBSERVATIONS)


def test_observations_profile_keeps_14_and_never_rotates_bot_copies(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    bot = bot_db(tmp_path / "bot.db")
    obs = obs_db(tmp_path / "observations.db")
    bots = [backup_db(bot, directory, clock=at(NOW + d * 86400)) for d in range(3)]
    made = [
        backup_db(obs, directory, clock=at(NOW + d * 86400), profile=OBSERVATIONS)
        for d in range(KEEP + 2)
    ]
    assert KEEP == 14
    left = sorted(p.name for p in directory.iterdir())
    assert left == sorted([*(b.path.name for b in bots), *(m.path.name for m in made[2:])])
    rotate(directory, 1)  # bot.db's rotation leaves every observations copy alone
    left = sorted(p.name for p in directory.iterdir())
    assert left == sorted([bots[-1].path.name, *(m.path.name for m in made[2:])])


def test_observations_profile_removes_only_its_own_stale_temporary_files(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    directory.mkdir()
    old = time.time() - 2 * 3600
    stale_obs = directory / ".observations-20270101T000000Z.123.tmp"
    stale_bot = directory / ".bot-20270101T000000Z.456.tmp"
    for path in (stale_obs, stale_bot):
        path.write_bytes(b"half a copy")
        os.utime(path, (old, old))
    rotate(directory, KEEP, OBSERVATIONS)
    assert not stale_obs.exists() and stale_bot.exists()
    rotate(directory, KEEP)
    assert not stale_bot.exists()


def test_observations_profile_restores_only_into_a_new_target(tmp_path: Path) -> None:
    db = obs_db(tmp_path / "data" / "observations.db")
    copy = backup_db(db, tmp_path / "backups", profile=OBSERVATIONS)
    before = digest(db)
    with pytest.raises(BackupError, match="never overwrites"):
        restore_db(copy.path, db, OBSERVATIONS)
    assert digest(db) == before
    target = tmp_path / "restore-test" / "observations.db"
    restored = restore_db(copy.path, target, OBSERVATIONS)
    assert restored.rows == copy.rows and verify_db(target, OBSERVATIONS).rows == copy.rows
    out = io.StringIO()
    again = tmp_path / "restore-cli" / "observations.db"
    assert run_restore(copy.path, again, out=out, profile=OBSERVATIONS) == EXIT_OK
    assert run_restore(copy.path, again, out=out, profile=OBSERVATIONS) == EXIT_INVALID
    assert "never overwrites" in out.getvalue()
    # The bot profile does not take an observations backup as a trade history.
    assert run_restore(copy.path, tmp_path / "wrong" / "bot.db", out=out) == EXIT_INVALID
    assert not (tmp_path / "wrong" / "bot.db").exists()


def test_observations_profile_cli_skips_a_missing_file_only_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "observations.db"
    directory = tmp_path / "observations" / "daily"
    args = ["backup", "--profile", "observations", "--db", str(missing), "--dir", str(directory)]
    assert main([*args, "--if-exists"]) == EXIT_OK
    assert f"backup skipped: no database at {missing} yet" in capsys.readouterr().out
    assert not directory.exists() and not missing.exists()
    assert main(args) == EXIT_INVALID
    assert "backup failed: no database at" in capsys.readouterr().out
    obs_db(missing)
    assert main([*args, "--if-exists"]) == EXIT_OK
    assert "schema ok, integrity ok, unique keys ok; rows: service_starts 1" in (
        capsys.readouterr().out
    )
    (copy,) = directory.iterdir()
    assert main(["verify-db", "--profile", "observations", "--db", str(copy)]) == EXIT_OK
    assert main(["verify-db", "--db", str(copy)]) == EXIT_INVALID  # default: the bot profile
    with pytest.raises(SystemExit):
        main(["backup", "--profile", "nonsense", "--db", str(missing), "--dir", str(directory)])


def run_backup_unit(data: Path, backups: Path) -> tuple[bool, list[int]]:
    """deploy/llm-trading-bot-backup.service as systemd runs a oneshot unit: its ExecStart lines
    in order; a "-" line may fail, any other failure ends the unit as failed."""
    codes: list[int] = []
    for line in UNIT.read_text(encoding="utf-8").splitlines():
        if not line.startswith("ExecStart="):
            continue
        command = line.removeprefix("ExecStart=")
        words = shlex.split(command.removeprefix("-"))
        assert words[0] == "__APP_DIR__/.venv/bin/trading-bot"
        args = [
            w.replace("__DATA_DIR__", str(data)).replace("__BACKUP_DIR__", str(backups))
            for w in words[1:]
        ]
        codes.append(main(args))
        if codes[-1] != EXIT_OK and not command.startswith("-"):
            return False, codes
    return True, codes


def test_corrupt_observations_keeps_bot_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, backups = tmp_path / "data", tmp_path / "backups"
    bot_db(data / "bot.db")
    (data / "observations.db").write_bytes(b"SQLite format 3\x00" + b"\xff" * 8000)
    ok, codes = run_backup_unit(data, backups)
    assert ok and codes == [EXIT_OK, EXIT_INVALID]  # the unit succeeds, the failure is logged
    assert "backup failed:" in capsys.readouterr().out
    (bot,) = (backups / "daily").iterdir()
    assert NAME.match(bot.name) and verify_db(bot).rows == verify_db(data / "bot.db").rows
    observations = backups / "observations" / "daily"
    assert not observations.exists() or list(observations.iterdir()) == []


def test_observations_profile_in_the_unit_skips_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, backups = tmp_path / "data", tmp_path / "backups"
    bot_db(data / "bot.db")
    assert run_backup_unit(data, backups) == (True, [EXIT_OK, EXIT_OK])
    assert "backup skipped: no database at" in capsys.readouterr().out
    obs_db(data / "observations.db")
    assert run_backup_unit(data, backups) == (True, [EXIT_OK, EXIT_OK])
    (copy,) = (backups / "observations" / "daily").iterdir()
    assert OBSERVATIONS.name.match(copy.name)
    assert len(list((backups / "daily").iterdir())) == 2
