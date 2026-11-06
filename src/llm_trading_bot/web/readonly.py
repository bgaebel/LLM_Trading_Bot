"""Read-only access to the bot database for the web UI.

ADR-0016: exactly one writer per database (the paper cycle with its WriterLock); the web UI only
reads. ``mode=ro`` makes SQLite refuse every write and never create the file, ``query_only`` is a
second guard against a programming error, and the schema check refuses other versions instead of
touching them. Each page view gets its own short connection (one snapshot, then closed), so a
long-lived reader never holds back the WAL checkpoint and no connection is shared between threads.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from llm_trading_bot.paper.db import SchemaError, check_schema

BUSY_TIMEOUT_MS = 2000


class DbUnavailable(RuntimeError):
    """Nothing to show: the database is missing, of another version or not readable right now."""


def _uri(path: Path) -> str:
    # as_uri() percent-encodes spaces and other characters, so any path works as a URI.
    return path.resolve().as_uri() + "?mode=ro"


@contextmanager
def reading(path: Path) -> Iterator[sqlite3.Connection]:
    """One consistent read snapshot of the bot database; raises DbUnavailable."""
    if not path.is_file():
        raise DbUnavailable("Noch keine Daten – der Bot hat noch keinen Zyklus geschrieben.")
    try:
        con = sqlite3.connect(
            _uri(path), uri=True, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
    except sqlite3.Error as exc:
        raise DbUnavailable(f"Datenbank nicht lesbar ({type(exc).__name__}).") from exc
    con.row_factory = sqlite3.Row
    try:
        try:
            con.execute("PRAGMA query_only=1")
            con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            check_schema(con)
            # All queries of one page see the same committed state, even while the bot writes.
            con.execute("BEGIN")
        except SchemaError as exc:
            raise DbUnavailable(f"Datenbank passt nicht zu dieser Version: {exc}") from exc
        except sqlite3.Error as exc:
            raise DbUnavailable(f"Datenbank nicht lesbar ({type(exc).__name__}).") from exc
        yield con
    finally:
        if con.in_transaction:
            con.execute("ROLLBACK")
        con.close()
