# Origin: LLM-Trading-Arena@7563724 apps/arena/operations.py (adapted) - WriterLock
"""Single-writer guard for the paper database (CLAUDE.md: never two processes on one DB)."""

import sys
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self


class WriterLockBusy(RuntimeError):
    """Another process owns the database."""


class WriterLock:
    """OS-owned advisory lock next to the DB; released by the OS on exit or crash.

    The lock file is never deleted: unlinking it would let a second process lock a new inode
    while the first still holds the old one.
    """

    def __init__(self, db_path: Path) -> None:
        self.path = Path(str(db_path.resolve()) + ".writer.lock")
        self.file: BinaryIO | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise WriterLockBusy(f"another process owns {self.path.name}") from None
        self.file = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.file is None:
            return
        if sys.platform == "win32":
            import msvcrt

            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
        self.file = None
