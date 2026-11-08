"""Live observations (Spec 015, ADR-0027): buffered during a round, written after it.

What the bot saw and decided beyond the trading history - the order book of every fetch, what
Bitvavo delivered per interval, why a market had no snapshot, the strategy version and release of
each decision, and each GPT attempt with its trigger, raw answer and unclamped targets.

Two halves, so that observing can never change or block trading:

- ``ObservationBuffer`` collects plain rows in memory while ``paper_cycle`` runs. Callers record
  through ``note``, which swallows every error: a broken recorder costs only observations.
- ``ObservationWriter`` writes one buffer per round to ``observations.db`` after the round, in its
  own transaction, outside any ``bot.db`` transaction, with a short busy timeout. It never raises:
  every failure is counted, and a damaged file or one of another version switches recording off
  for the rest of the run - never repaired, migrated or recreated.

Only the holder of the ``bot.db`` WriterLock (the service) writes here; there is no second lock,
because a busy lock would end the service (exit 3) instead of only the recording.
"""

import contextlib
import hashlib
import json
import re
import sqlite3
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from llm_trading_bot.llm.due import iso
from llm_trading_bot.market.models import SYMBOLS, Candle, Quote, Symbol
from llm_trading_bot.paper.db import SchemaError
from llm_trading_bot.paper.observations_schema import (
    OBS_FILE,
    check_observations_schema,
    create,
)

BUSY_TIMEOUT_MS = 1000
"""At most one second of a round waits for a busy observations file (Spec 015 AK3)."""
PURPOSES = ("start", "snapshot", "recheck", "after_llm")
"""Why the order book was fetched: round start, snapshot quotes, refetch of quotes older than
QUOTE_MAX_AGE, and the recheck after the GPT answers."""
UNKNOWN_RELEASE = "unknown"
_SHA = re.compile(r"[0-9a-f]{40}")


def observations_path(db: Path) -> Path:
    return db.parent / OBS_FILE


def release_sha(prefix: str | None = None) -> str:
    """The commit this code was installed from, or "unknown".

    Deploys install each commit to ``releases/<sha>/.venv`` (ADR-0020), so the interpreter
    prefix names it. Not an environment variable: units are rendered with the ``current`` link.
    """
    try:
        name = Path(sys.prefix if prefix is None else prefix).resolve().parent.name
    except (OSError, RuntimeError, ValueError):
        return UNKNOWN_RELEASE
    return name if _SHA.fullmatch(name) else UNKNOWN_RELEASE


def rows_sha256(candles: Iterable[Candle]) -> str:
    """SHA-256 of candles in a canonical form, so a rebuilt window can be compared exactly.

    Canonical form: a JSON array with one ``[ts, open, high, low, close, volume]`` array per
    candle in the given order (ascending open time), ``ts`` as integer epoch seconds, the other
    values as floats in Python's shortest round-trip notation, no spaces (separators "," and
    ":"), UTF-8. Example: ``[[1800000000,100.0,101.0,99.0,100.0,1.0]]``.
    """
    data = [
        [int(c.ts), float(c.open), float(c.high), float(c.low), float(c.close), float(c.volume)]
        for c in candles
    ]
    text = json.dumps(data, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    # default=str: a value JSON does not know must cost its exactness, never the row.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


class ObservationBuffer:
    """The observations of one round, in memory only: no I/O, nothing it holds is shared.

    Rows are plain tuples in the column order of their table (``round_id`` is added on write).
    Symbols are stored by name (``BTCEUR``) like in ``bot.db``, so ``(agent, symbol, bar_ts)``
    and ``llm_call_id`` join the decisions and GPT calls there.
    """

    def __init__(self, release: str = UNKNOWN_RELEASE) -> None:
        self.release = release
        self.round_at: int | None = None
        self.books: list[tuple[object, ...]] = []
        self.windows: list[tuple[object, ...]] = []
        self.snapshots: list[tuple[object, ...]] = []
        self.decisions: list[tuple[object, ...]] = []
        self.attempts: list[tuple[object, ...]] = []
        self.dropped = 0
        """Record calls that failed; their rows are missing from this round."""

    def empty(self) -> bool:
        return self.round_at is None and not self.decisions and not self.attempts

    def begin(self, at: int) -> None:
        self.round_at = int(at)

    def book(self, purpose: str, at: float, book: Mapping[Symbol, Quote]) -> None:
        depth: Mapping[Symbol, tuple[float | None, float | None]] = getattr(book, "depth", {})
        for symbol in SYMBOLS:
            quote = book.get(symbol)
            if quote is None:
                continue
            bid_size, ask_size = depth.get(symbol, (None, None))
            self.books.append(
                (iso(at), symbol.name, purpose, quote.bid, quote.ask, bid_size, ask_size)
            )

    def window(self, symbol: Symbol, interval: int, at: float, candles: tuple[Candle, ...]) -> None:
        """The closed candles one request returned; without the Bitvavo extras (another client)
        the closed candles stand in for what was received and the forming bar counts as absent."""
        received = getattr(candles, "received", len(candles))
        first = getattr(candles, "first_open", candles[0].ts if candles else None)
        last = getattr(candles, "last_open", candles[-1].ts if candles else None)
        forming = bool(getattr(candles, "forming", False))
        self.windows.append(
            (
                iso(at),
                symbol.name,
                interval,
                received,
                first,
                last,
                int(forming),
                candles[-1].volume if candles else None,
                rows_sha256(candles),
            )
        )

    def snapshot(self, symbol: Symbol, status: str, message: str | None, decided: bool) -> None:
        assert self.round_at is not None, "begin() first"
        self.snapshots.append((iso(self.round_at), symbol.name, status, message, int(decided)))

    def decision(
        self,
        agent: str,
        symbol: Symbol,
        bar_ts: int,
        at: str,
        strategy: str,
        version: str,
        indicators: Mapping[str, object],
    ) -> None:
        # Serialised now: the dict must not change with whatever happens after the decision.
        self.decisions.append(
            (at, agent, symbol.name, bar_ts, strategy, version, self.release, _json(indicators))
        )

    def llm_attempt(
        self,
        at: str,
        llm_call_id: int,
        agent: str,
        trigger: str,
        detail: Mapping[str, object],
        cadence: Mapping[str, object],
        raw_text: str | None,
        targets: Mapping[str, object],
    ) -> None:
        self.attempts.append(
            (
                at,
                llm_call_id,
                agent,
                trigger,
                _json(detail),
                _json(cadence),
                raw_text,
                _json(targets),
            )
        )


def note(buffer: ObservationBuffer | None, record: Callable[[ObservationBuffer], object]) -> None:
    """Record into ``buffer`` (if any); a failure loses the observation, never the round."""
    if buffer is None:
        return
    try:
        record(buffer)
    except Exception:
        with contextlib.suppress(Exception):
            buffer.dropped += 1


WindowKey = tuple[str, int]
"""(symbol, interval) of a window row."""


def changed_windows(
    seen: Mapping[WindowKey, tuple[object, ...]], windows: Iterable[tuple[object, ...]]
) -> list[tuple[object, ...]]:
    """The window rows that differ from the last one written for their symbol and interval.

    Lossless: the window a round used is the latest written row at or before its time. Without
    this a day costs 11,520 window rows; the 15m/1h/4h windows change only once per bar, so it
    becomes about 3,100 (Spec 015 AK4, measured 2026-11-08).
    """
    fresh: list[tuple[object, ...]] = []
    last = dict(seen)
    for row in windows:
        key = (str(row[1]), int(str(row[2])))
        if last.get(key) != _window_state(row):
            fresh.append(row)
            last[key] = _window_state(row)
    return fresh


def _window_state(row: tuple[object, ...]) -> tuple[object, ...]:
    # rows, first/last open, forming flag and the hash of the closed rows (which covers the
    # last closed volume): what tells two answers of Bitvavo apart. The time does not.
    return (*row[3:7], row[8])


def insert(
    con: sqlite3.Connection,
    buffer: ObservationBuffer,
    windows: list[tuple[object, ...]] | None = None,
) -> None:
    """All rows of ``buffer`` (window rows: ``windows`` if given); the caller holds the
    transaction."""
    if buffer.round_at is not None:
        cursor = con.execute(
            "INSERT INTO rounds(ts,release) VALUES (?,?)", (iso(buffer.round_at), buffer.release)
        )
        round_id = cursor.lastrowid
        con.executemany(
            "INSERT INTO books(round_id,ts,symbol,purpose,bid,ask,bid_size,ask_size)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(round_id, *row) for row in buffer.books],
        )
        con.executemany(
            "INSERT INTO windows(round_id,ts,symbol,interval,rows,first_open,last_open,forming,"
            "last_closed_volume,rows_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(round_id, *row) for row in (buffer.windows if windows is None else windows)],
        )
        con.executemany(
            "INSERT INTO snapshots(round_id,ts,symbol,status,message,decided) VALUES (?,?,?,?,?,?)",
            [(round_id, *row) for row in buffer.snapshots],
        )
    # OR IGNORE: a decision or attempt is observed once; a repeat (e.g. a replayed slot after a
    # clock jump) must not cost the whole round.
    con.executemany(
        "INSERT OR IGNORE INTO decision_meta(ts,agent,symbol,bar_ts,strategy,strategy_version,"
        "release,indicators) VALUES (?,?,?,?,?,?,?,?)",
        buffer.decisions,
    )
    con.executemany(
        "INSERT OR IGNORE INTO llm_attempts(ts,llm_call_id,agent,trigger,trigger_detail,cadence,"
        "raw_text,targets) VALUES (?,?,?,?,?,?,?,?)",
        buffer.attempts,
    )


def _damaged(exc: BaseException) -> bool:
    """Another version or shape, or not a (sound) database: retrying cannot help.

    Plain ``sqlite3.DatabaseError`` is what SQLite's NOTADB and CORRUPT become; busy, I/O and
    disk-full errors are OperationalErrors and may pass, so they are only counted.
    """
    return isinstance(exc, SchemaError) or type(exc) is sqlite3.DatabaseError


class ObservationWriter:
    """Writes buffers to ``observations.db``; never raises, counts instead (Spec 015 AK3, AK5)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.written = 0
        """Transactions written since the start (the start marker and one per round)."""
        self.failed = 0
        """Transactions lost since the start: failed, or skipped because recording is off."""
        self.last_error: str | None = None
        self.off = False
        """True after a damaged file or one of another version: no further attempt this run."""
        self.windows_seen: dict[WindowKey, tuple[object, ...]] = {}
        """The last window written per symbol and interval; after a restart everything is new."""

    def start(self, at: str, release: str, pid: int) -> None:
        """The start marker of this service run: gaps between runs stay visible."""
        self._write(
            lambda con: con.execute(
                "INSERT INTO service_starts(ts,release,pid) VALUES (?,?,?)", (at, release, pid)
            )
        )

    def write(self, buffer: ObservationBuffer) -> None:
        try:
            if buffer.empty():
                return
        except Exception as exc:
            self._fail(exc)
            return
        try:
            fresh = changed_windows(self.windows_seen, buffer.windows)
        except Exception as exc:
            self._fail(exc)
            return
        if self._write(lambda con: insert(con, buffer, fresh)):
            # Only after the commit: a lost round must not hide its windows from the next one.
            for row in fresh:
                self.windows_seen[(str(row[1]), int(str(row[2])))] = _window_state(row)

    def _write(self, work: Callable[[sqlite3.Connection], object]) -> bool:
        if self.off:
            self.failed += 1
            return False
        try:
            con = self._open()
            try:
                con.execute("BEGIN IMMEDIATE")
                try:
                    work(con)
                    con.execute("COMMIT")
                except BaseException:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    raise
            finally:
                con.close()
        except Exception as exc:
            self._fail(exc)
            return False
        self.written += 1
        return True

    def _fail(self, exc: BaseException) -> None:
        self.failed += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        if _damaged(exc):
            self.off = True
            self.last_error = f"recording off: {self.last_error}"

    def _open(self) -> sqlite3.Connection:
        # ADR-0027: created only when missing; an existing file is opened as it is (mode=rw
        # cannot create one), checked, and never replaced.
        if not self.path.exists():
            create(self.path)
        con = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw",
            uri=True,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            check_observations_schema(con)
        except BaseException:
            con.close()
            raise
        return con
