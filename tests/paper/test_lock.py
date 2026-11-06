"""Spec 004 AK2: a second writer fails at once; a crashed writer's lock is freed by the OS."""

import subprocess
import sys
from pathlib import Path

import pytest

from llm_trading_bot.paper.lock import WriterLock, WriterLockBusy

CHILD = """
import sys
from pathlib import Path
from llm_trading_bot.paper.lock import WriterLock
with WriterLock(Path(sys.argv[1])):
    print("LOCKED", flush=True)
    sys.stdin.readline()
"""


def _hold_in_child(db: Path) -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "LOCKED"
    return child


def test_second_process_is_refused_until_the_first_exits(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    child = _hold_in_child(db)
    try:
        with pytest.raises(WriterLockBusy), WriterLock(db):
            pass
    finally:
        assert child.stdin is not None
        child.communicate("\n", timeout=30)
    with WriterLock(db):
        pass


def test_lock_of_a_killed_process_is_released(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    child = _hold_in_child(db)
    child.kill()
    child.wait(timeout=30)
    with WriterLock(db):
        pass
    assert (tmp_path / "bot.db.writer.lock").exists()


def test_same_process_cannot_take_the_lock_twice(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    with WriterLock(db), pytest.raises(WriterLockBusy), WriterLock(db):
        pass
    with WriterLock(db):
        pass
