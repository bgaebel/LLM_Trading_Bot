"""One-off maintenance (Spec 022, ADR-0032): delete the history of the euro depots from bot.db.

The user decided on 2026-11-09 that everything from before the USDC route goes ("Wirklich
löschen"). This is the only code that deletes trading history, so it is narrow on purpose:

- exactly the seven depots of ``EURO_DEPOTS``, by name, from exactly the tables with per-depot
  rows (``PER_DEPOT_TABLES``); never ``market``, never another depot, never observations.db;
- a dry run by default, through a read-only connection (``mode=ro``, ADR-0016) that cannot write;
- ``execute`` only while holding the WriterLock (the service is stopped, ADR-0017), in ONE
  transaction that is rolled back unless ``integrity_check``, ``foreign_key_check``, the schema
  check and a digest of every other row all pass afterwards.

The server procedure (stop, backup, dry run, execute, start - only with the user's consent) is
in docs/betrieb/server.md.
"""

import hashlib
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from llm_trading_bot.llm.agents import EURO_DEPOTS
from llm_trading_bot.paper.db import BUSY_TIMEOUT_MS, COLUMNS, SchemaError, check_schema, write_tx
from llm_trading_bot.paper.lock import WriterLock
from llm_trading_bot.paper.usdc import is_usdc
from llm_trading_bot.web.readonly import DbUnavailable, reading

PER_DEPOT_TABLES = ("trades", "decisions", "equity", "llm_calls", "portfolios")
"""Every table with a row per depot, in delete order: a trade refers to its decision."""
KEPT_TABLES = ("market",)
"""Rows per market, not per depot: never touched."""


class PurgeRefused(RuntimeError):
    """Nothing was deleted: the database, its schema or the scope is not what this expects."""


def _check_scope() -> None:
    """The scope this code was written for; a schema with another per-depot table, or a depot
    list that reaches a USDC depot, must be looked at again before anything is deleted."""
    if any(is_usdc(a) for a in EURO_DEPOTS):
        raise PurgeRefused(f"the scope would reach a USDC depot: {list(EURO_DEPOTS)}")
    per_depot = {t for t, cols in COLUMNS.items() if "agent" in cols}
    if per_depot != set(PER_DEPOT_TABLES) or set(COLUMNS) != {*PER_DEPOT_TABLES, *KEPT_TABLES}:
        raise PurgeRefused(f"unexpected tables: {sorted(COLUMNS)}")


def _marks() -> str:
    return ",".join("?" for _ in EURO_DEPOTS)


def counts(con: sqlite3.Connection) -> dict[str, int]:
    """Rows of the euro depots per table."""
    return {
        t: int(
            con.execute(
                f"SELECT COUNT(*) FROM {t} WHERE agent IN ({_marks()})", EURO_DEPOTS
            ).fetchone()[0]
        )
        for t in PER_DEPOT_TABLES
    }


def _foreign_references(con: sqlite3.Connection) -> int:
    """Trades of another depot pointing at a decision of a euro depot - never written by the bot;
    deleting would leave them dangling, so their presence refuses the purge."""
    n: int = con.execute(
        "SELECT COUNT(*) FROM trades t JOIN decisions d ON t.decision_id = d.id"
        f" WHERE d.agent IN ({_marks()}) AND t.agent NOT IN ({_marks()})",
        (*EURO_DEPOTS, *EURO_DEPOTS),
    ).fetchone()[0]
    return n


def kept_digest(con: sqlite3.Connection) -> str:
    """SHA-256 over every row the purge must leave alone, bit for bit (``repr`` of each value,
    in rowid order): all rows of the other depots and every row of ``market``."""
    h = hashlib.sha256()
    for table in (*PER_DEPOT_TABLES, *KEPT_TABLES):
        cols = ",".join(COLUMNS[table])
        where = f" WHERE agent NOT IN ({_marks()})" if table in PER_DEPOT_TABLES else ""
        args = EURO_DEPOTS if where else ()
        h.update(table.encode())
        for row in con.execute(f"SELECT {cols} FROM {table}{where} ORDER BY rowid", args):
            h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def dry_run(db: Path) -> dict[str, int]:
    """What ``execute`` would delete; opens read-only and changes nothing."""
    _check_scope()
    try:
        with reading(db) as con:
            if _foreign_references(con):
                raise PurgeRefused("a trade of another depot refers to a euro decision")
            return counts(con)
    except DbUnavailable as exc:
        raise PurgeRefused(str(exc)) from None


def execute(db: Path) -> dict[str, int]:
    """Delete the euro depots' rows; the deleted counts. Raises WriterLockBusy while another
    process (the service) owns the database, PurgeRefused if anything is off - then nothing
    was deleted."""
    _check_scope()
    if not db.is_file():
        raise PurgeRefused(f"no database at {db}")
    with WriterLock(db):
        con = sqlite3.connect(db, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            try:
                check_schema(con)
            except SchemaError as exc:
                raise PurgeRefused(f"schema: {exc}") from None
            return _delete(con)
        finally:
            con.close()


def _delete(con: sqlite3.Connection) -> dict[str, int]:
    with write_tx(con):
        if _foreign_references(con):
            raise PurgeRefused("a trade of another depot refers to a euro decision")
        before = kept_digest(con)
        deleted = {}
        for table in PER_DEPOT_TABLES:
            cur = con.execute(f"DELETE FROM {table} WHERE agent IN ({_marks()})", EURO_DEPOTS)
            deleted[table] = cur.rowcount
        # Checked inside the transaction: any failure rolls every delete back.
        integrity = [tuple(r) for r in con.execute("PRAGMA integrity_check")]
        if integrity != [("ok",)]:
            raise PurgeRefused(f"integrity_check: {integrity[:3]}")
        if dangling := con.execute("PRAGMA foreign_key_check").fetchall():
            raise PurgeRefused(f"foreign_key_check: {len(dangling)} dangling rows")
        try:
            check_schema(con)
        except SchemaError as exc:
            raise PurgeRefused(f"schema: {exc}") from None
        if any(counts(con).values()):
            raise PurgeRefused("rows of a euro depot are left")
        if kept_digest(con) != before:
            raise PurgeRefused("rows of another depot or a market changed")
    return deleted


def report(found: Mapping[str, int]) -> list[str]:
    """One line per table and the total, for the command's output."""
    lines = [f"  {table:<11} {found[table]:>9}" for table in PER_DEPOT_TABLES]
    return [*lines, f"  {'total':<11} {sum(found.values()):>9}"]
