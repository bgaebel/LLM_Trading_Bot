"""Candle archive of the strategy lab (Spec 016 AK1, ADR-0026): Bitvavo's public candles in
``candles.db``, exactly as sent, fetched in fixed windows and never rewritten once complete.

Design:
- A window is ``[start, start + 1440 * step)`` with ``start`` a multiple of ``1440 * step`` since
  the epoch (1m: one UTC day, 15m: 15 days, 1h: 60 days, 4h: 240 days). One request per window
  with ``start``/``end`` in ms: Bitvavo includes ``start`` and excludes ``end`` by open time, so
  aligned windows tile the time line without loss or overlap (research 2026-11-07).
- Only closed, settled bars are written (``open + step + settle <= fetched_at``, where
  ``fetched_at`` is taken *before* the request). A window reaching into the present stays
  ``open`` and is completed by a later fetch; a ``done`` window is never requested again, and
  triggers refuse any update or delete of a stored candle.
- Every row passes the live client's parser (``_open_time``/``_parse_candle``), so the archive
  holds nothing the live bot would reject; values are kept as the strings Bitvavo sent.
- Only public GET endpoints without keys (ADR-0004). The downloader paces itself by
  ``bitvavo-ratelimit-remaining`` and stops at the first 429: a key-less 429 bans the IP for
  15 minutes, so it never retries one and remembers until when it must wait.
"""

import calendar
import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Protocol
from urllib.parse import quote as url_quote
from urllib.parse import urlencode

from llm_trading_bot.lab.model import MARKETS
from llm_trading_bot.market.bitvavo import (
    API,
    DEFAULT_COOLDOWN,
    TIMEFRAMES,
    _open_time,
    _parse_candle,
)
from llm_trading_bot.market.http import HTTPFailure, TransportError, UrllibTransport
from llm_trading_bot.market.models import MarketDataError

SCHEMA_VERSION = 1
INTERVALS = (1, 15, 60, 240)
"""Minutes; the four series the live snapshot reads."""
WINDOW_BARS = 1440
"""Bitvavo's largest ``limit``; one window is one request."""
SOURCE = "bitvavo-api"
SETTLE_SECONDS = 60
"""Extra wait after a bar's close before it is written. Research 2026-11-07 suspects the volume
of a just-closed bar may still change for a few seconds; a stored row is never rewritten, so a
late revision would otherwise become a permanent conflict."""
SLOW_BELOW = 200
"""Below this ``bitvavo-ratelimit-remaining`` the downloader waits for the next reset."""
MIN_INTERVAL = 0.12
"""Seconds between requests: about 500/min, half of the key-less budget of 1000/min."""
ATTEMPTS = 3
"""Tries for a broken connection or a 5xx; a 429 is never retried."""

EARLIEST: dict[tuple[str, int], int] = {
    # First day with data per series (research 2026-11-07, binary search on the public API).
    # Windows ending on or before it are skipped without a request; the day is generous on
    # purpose, the window containing the first bar is always fetched.
    ("BTC-EUR", 1): calendar.timegm((2019, 8, 1, 0, 0, 0)),
    ("BTC-EUR", 15): calendar.timegm((2021, 2, 20, 0, 0, 0)),
    ("BTC-EUR", 60): calendar.timegm((2019, 3, 8, 0, 0, 0)),
    ("BTC-EUR", 240): calendar.timegm((2019, 3, 8, 0, 0, 0)),
    ("ETH-EUR", 1): calendar.timegm((2019, 3, 8, 0, 0, 0)),
    ("ETH-EUR", 15): calendar.timegm((2019, 3, 8, 0, 0, 0)),
    ("ETH-EUR", 60): calendar.timegm((2019, 3, 8, 0, 0, 0)),
    ("ETH-EUR", 240): calendar.timegm((2019, 3, 8, 0, 0, 0)),
}

# Public market endpoints only (ADR-0004): a market in the path, then candles or trades.
_PUBLIC_PATH = re.compile(r"^/[A-Z0-9]{2,10}-[A-Z0-9]{2,10}/(candles|trades)\?[A-Za-z0-9=&.%-]*$")
_MARKET = re.compile(r"^[A-Z0-9]{2,10}-[A-Z0-9]{2,10}$")

RawCandle = list[int | str]
"""One row as ``/candles`` sends it: ``[open ms, open, high, low, close, volume]``."""
StoredRow = tuple[int, str, str, str, str, str]
"""``(open epoch s, open, high, low, close, volume)`` with the values as sent."""

_SCHEMA = """
CREATE TABLE meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE candles(
    market TEXT NOT NULL,
    interval INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(market, interval, ts)
) WITHOUT ROWID;
CREATE TABLE windows(
    market TEXT NOT NULL,
    interval INTEGER NOT NULL,
    start INTEGER NOT NULL,
    "end" INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('done', 'open')),
    rows INTEGER NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(market, interval, start)
) WITHOUT ROWID;
CREATE TABLE spread_samples(
    market TEXT NOT NULL,
    method TEXT NOT NULL,
    hour INTEGER NOT NULL,
    trades INTEGER NOT NULL,
    pairs INTEGER NOT NULL,
    median REAL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(market, method, hour)
) WITHOUT ROWID;
CREATE TABLE spreads(
    market TEXT NOT NULL,
    quarter TEXT NOT NULL,
    method TEXT NOT NULL,
    spread REAL NOT NULL,
    samples INTEGER NOT NULL,
    computed_at INTEGER NOT NULL,
    PRIMARY KEY(market, quarter, method)
) WITHOUT ROWID;
CREATE TRIGGER candles_never_updated BEFORE UPDATE ON candles
BEGIN SELECT RAISE(ABORT, 'archived candles are never rewritten'); END;
CREATE TRIGGER candles_never_deleted BEFORE DELETE ON candles
BEGIN SELECT RAISE(ABORT, 'archived candles are never deleted'); END;
CREATE TRIGGER done_windows_never_updated BEFORE UPDATE ON windows WHEN OLD.status = 'done'
BEGIN SELECT RAISE(ABORT, 'done windows are never rewritten'); END;
CREATE TRIGGER done_windows_never_deleted BEFORE DELETE ON windows WHEN OLD.status = 'done'
BEGIN SELECT RAISE(ABORT, 'done windows are never deleted'); END;
"""


class ArchiveError(RuntimeError):
    """The archive cannot be used or a response does not fit it; nothing was written."""


class RateLimited(Exception):
    """Bitvavo answered 429; no request may be sent before ``retry_at`` (epoch seconds)."""

    def __init__(self, retry_at: int) -> None:
        super().__init__(f"rate limited until {retry_at}")
        self.retry_at = retry_at


def german_retry_message(retry_at: int, now: int) -> str:
    """CLI text for a stop by the rate limit (German, user-facing)."""
    when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(retry_at))
    minutes = max(1, math.ceil((retry_at - now) / 60))
    return (
        f"Bitvavo-Rate-Limit (HTTP 429): Abruf gestoppt. Weiter frühestens ab {when} "
        f"(in etwa {minutes} min); bis dahin keine Anfrage senden, "
        "sonst verlängert sich die Sperre."
    )


# ---------------------------------------------------------------- transport and pacing


class HeaderTransport(Protocol):
    """GET a URL and return the decoded JSON plus the response headers (lower-case names).

    Raises ``HTTPFailure`` for an HTTP error status (``retry_after`` from the headers)."""

    def get_json_and_headers(self, url: str) -> tuple[object, dict[str, str]]: ...


class LabTransport:
    """The live GET-only urllib transport (``market/http.py``) with the lab's own User-Agent.

    The HTTP code itself stays in ``market/`` so that the static ADR-0004 checks keep seeing
    every request in one place; 429 delays come from ``retry_after_seconds`` there.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._http = UrllibTransport(timeout=timeout, user_agent="llm-trading-bot-lab/0.1")

    def get_json_and_headers(self, url: str) -> tuple[object, dict[str, str]]:
        return self._http.get_json_and_headers(url)


def _header_number(headers: Mapping[str, str], name: str) -> float | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class PublicApi:
    """Paced access to Bitvavo's public market endpoints; one instance per download run.

    ``clock`` and ``sleep`` are injectable so tests run without waiting.
    """

    def __init__(
        self,
        transport: HeaderTransport | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        min_interval: float = MIN_INTERVAL,
        slow_below: int = SLOW_BELOW,
        attempts: int = ATTEMPTS,
    ) -> None:
        self._transport: HeaderTransport = transport if transport is not None else LabTransport()
        self._clock = clock
        self._sleep = sleep
        self._min_interval = min_interval
        self._slow_below = slow_below
        self._attempts = max(1, attempts)
        self._last: float | None = None
        self.requests = 0
        """Requests sent (each try counts)."""
        self.slowdowns = 0
        """Waits for a rate-limit reset because ``remaining`` fell below ``slow_below``."""

    def now(self) -> int:
        return math.floor(self._clock())

    def candles(self, market: str, interval: int, start: int, end: int) -> tuple[object, int]:
        """One window ``[start, end)`` (epoch s); returns (rows as sent, fetched_at)."""
        query = urlencode(
            {
                "interval": TIMEFRAMES[interval],
                "limit": WINDOW_BARS,
                "start": start * 1000,
                "end": end * 1000,
            }
        )
        return self._request(f"/{_market_segment(market)}/candles?{query}")

    def trades(self, market: str, start: int, end: int, *, limit: int = 1000) -> tuple[object, int]:
        """Public trades in ``[start, end)`` (epoch s, under 24 h; weight 5 per request)."""
        if not 0 < end - start < 86_400:
            raise ValueError("trades window must be shorter than 24 h")
        query = urlencode({"start": start * 1000, "end": end * 1000, "limit": limit})
        return self._request(f"/{_market_segment(market)}/trades?{query}")

    def _request(self, path: str) -> tuple[object, int]:
        if not _PUBLIC_PATH.match(path):
            raise ValueError(f"not a public market path: {path}")
        failure: Exception | None = None
        for attempt in range(self._attempts):
            if attempt:
                self._sleep(2.0 * 2**attempt)
            self._pace()
            # Taken before sending: a bar that closes while the response travels was still
            # forming on the server, so it must count as forming here as well.
            sent_at = self.now()
            self.requests += 1
            try:
                data, headers = self._transport.get_json_and_headers(API + path)
            except HTTPFailure as exc:
                if exc.status == 429:
                    # Key-less 429 = 15-minute IP ban (research §2); a shorter hint is the
                    # minute reset, not the ban, so the longer of both wins.
                    wait = max(DEFAULT_COOLDOWN, exc.retry_after or 0.0)
                    raise RateLimited(math.ceil(self._clock() + wait)) from None
                if exc.status < 500:
                    raise
                failure = exc
                continue
            except (TransportError, OSError) as exc:
                failure = exc
                continue
            self._slow_down(headers)
            return data, sent_at
        assert failure is not None
        raise failure

    def _pace(self) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._min_interval:
            self._sleep(self._min_interval - (now - self._last))
        self._last = self._clock()

    def _slow_down(self, headers: Mapping[str, str]) -> None:
        remaining = _header_number(headers, "bitvavo-ratelimit-remaining")
        if remaining is None or remaining >= self._slow_below:
            return
        reset = _header_number(headers, "bitvavo-ratelimit-resetat")
        if reset is None:
            wait = 60.0
        else:
            reset_s = reset / 1000 if reset > 100_000_000_000 else reset
            wait = reset_s - self._clock() + 1.0
        # The budget resets every minute; never stall longer than that on a strange header.
        self.slowdowns += 1
        self._sleep(min(61.0, max(1.0, wait)))


def _market_segment(market: str) -> str:
    if not _MARKET.match(market):
        raise ValueError(f"unknown market format: {market!r}")
    return url_quote(market, safe="")


# ---------------------------------------------------------------- windows and rows


def step_of(interval: int) -> int:
    if interval not in TIMEFRAMES:
        raise ValueError(f"unsupported interval: {interval} min")
    return interval * 60


def window_span(interval: int) -> int:
    return WINDOW_BARS * step_of(interval)


def window_start(interval: int, epoch: int) -> int:
    """Start of the aligned window that contains ``epoch``."""
    span = window_span(interval)
    return epoch // span * span


def windows(interval: int, start: int, end: int) -> list[tuple[int, int]]:
    """Every aligned window overlapping ``[start, end)``, ascending."""
    span = window_span(interval)
    out = []
    w = window_start(interval, start)
    while w < end:
        out.append((w, w + span))
        w += span
    return out


def validate_rows(
    data: object, interval: int, start: int, end: int, *, writable_until: int
) -> tuple[list[StoredRow], int, int]:
    """Rows of one window response that may be stored, ascending.

    Returns (rows, outside, unsettled): ``outside`` rows lie beyond ``[start, end)`` and belong
    to another window; ``unsettled`` ones close after ``writable_until`` and are left for a
    later fetch. Both are skipped before the value checks, like the live client skips forming
    bars. Every other row must pass the live parser. Raises ArchiveError otherwise.
    """
    if not isinstance(data, list):
        raise ArchiveError("candles: unexpected response format")
    step = step_of(interval)
    seen: dict[int, StoredRow] = {}
    outside = unsettled = 0
    for raw in data:
        try:
            ts = _open_time(raw)
            if not start <= ts < end:
                outside += 1
                continue
            if ts + step > writable_until:
                unsettled += 1
                continue
            _parse_candle(raw, step)
        except MarketDataError as exc:
            raise ArchiveError(f"{exc} (window {start}, {interval}m)") from exc
        assert isinstance(raw, list | tuple)  # checked by _open_time
        values = raw[1:6]
        if not all(isinstance(v, str) for v in values):
            # Bitvavo sends OHLCV as strings; anything else could not be kept "as sent".
            raise ArchiveError(f"candles: OHLCV not sent as strings at {ts}")
        row: StoredRow = (ts, values[0], values[1], values[2], values[3], values[4])
        if ts in seen and seen[ts] != row:
            raise ArchiveError(f"candles: conflicting duplicate at {ts}")
        seen[ts] = row
    return [seen[ts] for ts in sorted(seen)], outside, unsettled


@dataclass(frozen=True)
class WindowState:
    status: str
    rows: int
    fetched_at: int


def _raw(r: Sequence[object]) -> RawCandle:
    """A stored row back in the ``/candles`` shape (ms open time, strings as sent)."""
    return [int(str(r[0])) * 1000, str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5])]


# ---------------------------------------------------------------- the archive


class Archive:
    """``candles.db``: writer (one process) or reader (``mode=ro``, never creates the file)."""

    def __init__(self, path: Path, *, readonly: bool = False) -> None:
        self.path = path
        self.readonly = readonly
        if readonly:
            if not path.is_file():
                raise ArchiveError(f"no candle archive at {path}")
            con = sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=10
            )
            con.execute("PRAGMA query_only=1")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(path, isolation_level=None, timeout=30)
        self._con = con
        try:
            self._check_or_create()
        except BaseException:
            con.close()
            raise

    @classmethod
    def open_readonly(cls, path: Path) -> "Archive":
        return cls(path, readonly=True)

    @property
    def db(self) -> sqlite3.Connection:
        """The connection, for the lab modules that keep their tables here (spreads)."""
        return self._con

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Archive":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _check_or_create(self) -> None:
        con = self._con
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables and not self.readonly:
            con.execute("PRAGMA journal_mode=WAL")
            try:
                # One transaction: a crash leaves either no schema or the whole versioned one.
                con.executescript(
                    f"BEGIN IMMEDIATE; {_SCHEMA}"
                    f"INSERT INTO meta(key, value) VALUES ('schema_version', '{SCHEMA_VERSION}');"
                    "COMMIT;"
                )
            except BaseException:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                raise
            return
        if "meta" not in tables:
            raise ArchiveError(f"{self.path} is not a candle archive")
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None or row[0] != str(SCHEMA_VERSION):
            found = None if row is None else row[0]
            raise ArchiveError(
                f"candle archive schema {found} found, this code reads {SCHEMA_VERSION} only"
            )

    # -- meta

    def get_meta(self, key: str) -> str | None:
        row = self._con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        if key == "schema_version":
            raise ArchiveError("the schema version is not changed in place")
        self._con.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def retry_at(self) -> int | None:
        """Until when Bitvavo must not be asked after the last 429 (epoch s), if known."""
        value = self.get_meta("retry_at")
        return None if value is None else int(value)

    # -- windows

    def window_state(self, market: str, interval: int, start: int) -> WindowState | None:
        row = self._con.execute(
            "SELECT status, rows, fetched_at FROM windows "
            "WHERE market=? AND interval=? AND start=?",
            (market, interval, start),
        ).fetchone()
        return None if row is None else WindowState(str(row[0]), int(row[1]), int(row[2]))

    def windows_done(self, market: str, interval: int) -> set[int]:
        return {
            int(r[0])
            for r in self._con.execute(
                "SELECT start FROM windows WHERE market=? AND interval=? AND status='done'",
                (market, interval),
            )
        }

    def store_window(
        self,
        market: str,
        interval: int,
        start: int,
        rows: Sequence[StoredRow],
        *,
        fetched_at: int,
        settle: int = SETTLE_SECONDS,
    ) -> tuple[int, WindowState]:
        """Write one window's rows and its status in one transaction; returns (new rows, state).

        A row already stored must come back identical, and none may vanish; otherwise
        ArchiveError and nothing of this window is written. A window found ``done`` (another
        run finished it) is left as it is.
        """
        step = step_of(interval)
        if start % window_span(interval):
            raise ArchiveError(f"window start {start} not aligned for {interval}m")
        end = start + window_span(interval)
        done = end + settle <= fetched_at
        con = self._con
        con.execute("BEGIN IMMEDIATE")
        try:
            state = self.window_state(market, interval, start)
            if state is not None and state.status == "done":
                con.execute("ROLLBACK")
                return 0, state
            existing = {
                int(r[0]): (int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5]))
                for r in con.execute(
                    "SELECT ts, open, high, low, close, volume FROM candles "
                    "WHERE market=? AND interval=? AND ts>=? AND ts<?",
                    (market, interval, start, end),
                )
            }
            incoming = {r[0]: r for r in rows}
            new: list[StoredRow] = []
            for ts, row in incoming.items():
                if not start <= ts < end or ts % step or ts + step + settle > fetched_at:
                    raise ArchiveError(f"row {ts} does not belong to window {start} now")
                old = existing.get(ts)
                if old is None:
                    new.append(row)
                elif old != row:
                    raise ArchiveError(
                        f"{market} {interval}m: stored candle {ts} differs from Bitvavo's answer"
                    )
            vanished = [ts for ts in existing if ts not in incoming]
            if vanished:
                raise ArchiveError(
                    f"{market} {interval}m: stored candle {min(vanished)} missing in the answer"
                )
            con.executemany(
                "INSERT INTO candles(market, interval, ts, open, high, low, close, volume, "
                "source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(market, interval, *r, SOURCE, fetched_at) for r in new],
            )
            new_state = WindowState(
                "done" if done else "open", len(existing) + len(new), fetched_at
            )
            con.execute(
                'INSERT INTO windows(market, interval, start, "end", status, rows, fetched_at) '
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(market, interval, start) DO UPDATE SET "
                "status=excluded.status, rows=excluded.rows, fetched_at=excluded.fetched_at",
                (market, interval, start, end, new_state.status, new_state.rows, fetched_at),
            )
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        return len(new), new_state

    # -- reading (the backtest's view)

    def _select(self, market: str, interval: int, start: int, end: int) -> Iterator[RawCandle]:
        step_of(interval)
        for r in self._con.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE market=? AND interval=? AND ts>=? AND ts<? ORDER BY ts",
            (market, interval, start, end),
        ):
            yield _raw(r)

    def rows(self, market: str, interval: int, start: int, end: int) -> list[RawCandle]:
        """Rows opened in ``[start, end)`` (epoch s), ascending, shaped like ``/candles`` rows."""
        return list(self._select(market, interval, start, end))

    def last_rows(self, market: str, interval: int, end: int, limit: int) -> list[RawCandle]:
        """The newest ``limit`` rows opened before ``end``, ascending."""
        step_of(interval)
        found = [
            _raw(r)
            for r in self._con.execute(
                "SELECT ts, open, high, low, close, volume FROM candles "
                "WHERE market=? AND interval=? AND ts<? ORDER BY ts DESC LIMIT ?",
                (market, interval, end, limit),
            )
        ]
        found.reverse()
        return found

    def count_before(self, market: str, interval: int, epoch: int) -> int:
        """Rows opened before ``epoch``: the data lead of a quarter (AK4)."""
        step_of(interval)
        row = self._con.execute(
            "SELECT COUNT(*) FROM candles WHERE market=? AND interval=? AND ts<?",
            (market, interval, epoch),
        ).fetchone()
        return int(row[0])

    def checksum(self, market: str, interval: int, start: int, end: int) -> str:
        """sha256 hex over the rows in ``[start, end)``: a header line naming the series and
        range, then one compact JSON array per row (the ``rows`` shape), each with a newline."""
        digest = hashlib.sha256(
            json.dumps([market, interval, start, end], separators=(",", ":")).encode() + b"\n"
        )
        for row in self._select(market, interval, start, end):
            digest.update(json.dumps(row, separators=(",", ":")).encode() + b"\n")
        return digest.hexdigest()


# ---------------------------------------------------------------- fetching


@dataclass
class FetchResult:
    requests: int = 0
    windows_done: int = 0
    """Windows completed by this run."""
    windows_open: int = 0
    """Windows fetched but still reaching into the present."""
    windows_skipped: int = 0
    """Windows already complete before this run (never requested again)."""
    windows_before_data: int = 0
    """Windows before the first data of their series, skipped without a request."""
    rows_written: int = 0
    rows_outside: int = 0
    slowdowns: int = 0
    stopped: str = ""
    """``""`` (all requested windows handled), ``rate_limited`` or ``budget``."""
    retry_at: int | None = None
    now: int = 0

    @property
    def complete(self) -> bool:
        return not self.stopped

    def text(self) -> str:
        """Short German summary for the CLI."""
        lines = [
            f"Anfragen: {self.requests}, neue Kerzen: {self.rows_written}",
            f"Fenster fertig: {self.windows_done} neu, {self.windows_skipped} schon vorhanden, "
            f"{self.windows_open} noch offen, {self.windows_before_data} vor Datenbeginn",
        ]
        if self.slowdowns:
            lines.append(f"Gebremst (Rate-Limit unter {SLOW_BELOW}): {self.slowdowns}-mal")
        if self.stopped == "rate_limited" and self.retry_at is not None:
            lines.append(german_retry_message(self.retry_at, self.now))
        elif self.stopped == "budget":
            lines.append("Anfragen-Grenze erreicht; ein weiterer Aufruf setzt fort.")
        return "\n".join(lines)


def fetch_archive(
    archive: Archive,
    api: PublicApi,
    *,
    start: int,
    end: int,
    markets: Iterable[str] = MARKETS,
    intervals: Iterable[int] = INTERVALS,
    settle: int = SETTLE_SECONDS,
    max_requests: int | None = None,
    earliest: Mapping[tuple[str, int], int] = EARLIEST,
    progress: Callable[[str], None] | None = None,
) -> FetchResult:
    """Fetch every missing window overlapping ``[start, end)`` and store it.

    Safe to abort at any point and to call again: done windows are skipped, each window is
    committed together with its status. Stops at a 429 (and remembers until when) or when
    ``max_requests`` is reached; ``ArchiveError`` on data that does not fit.
    """
    if archive.readonly:
        raise ArchiveError("the archive was opened read-only")
    result = FetchResult(now=api.now())
    banned = archive.retry_at()
    if banned is not None and banned > result.now:
        result.stopped, result.retry_at = "rate_limited", banned
        return result
    first_request, first_slowdowns = api.requests, api.slowdowns
    try:
        for market in markets:
            for interval in intervals:
                _fetch_series(
                    archive,
                    api,
                    market,
                    interval,
                    start,
                    end,
                    settle,
                    max_requests,
                    result,
                    earliest.get((market, interval)),
                    first_request,
                )
                if result.stopped:
                    return result
                if progress is not None:
                    progress(f"{market} {TIMEFRAMES[interval]}: bis {_day(end)} geprüft")
    except RateLimited as exc:
        archive.set_meta("retry_at", str(exc.retry_at))
        result.stopped, result.retry_at = "rate_limited", exc.retry_at
        result.now = api.now()
    finally:
        result.requests = api.requests - first_request
        result.slowdowns = api.slowdowns - first_slowdowns
    return result


def _fetch_series(
    archive: Archive,
    api: PublicApi,
    market: str,
    interval: int,
    start: int,
    end: int,
    settle: int,
    max_requests: int | None,
    result: FetchResult,
    first_data: int | None,
    first_request: int,
) -> None:
    done = archive.windows_done(market, interval)
    step = step_of(interval)
    for w_start, w_end in windows(interval, start, end):
        if w_start + step + settle > result.now:
            return  # not even its first bar is settled yet
        if first_data is not None and w_end <= first_data:
            result.windows_before_data += 1
            continue
        if w_start in done:
            result.windows_skipped += 1
            continue
        if max_requests is not None and api.requests - first_request >= max_requests:
            result.stopped = "budget"
            return
        data, fetched_at = api.candles(market, interval, w_start, w_end)
        rows, outside, _unsettled = validate_rows(
            data, interval, w_start, w_end, writable_until=fetched_at - settle
        )
        written, state = archive.store_window(
            market, interval, w_start, rows, fetched_at=fetched_at, settle=settle
        )
        result.rows_written += written
        result.rows_outside += outside
        if state.status == "done":
            result.windows_done += 1
        else:
            result.windows_open += 1
        result.now = fetched_at


def _day(epoch: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


# ---------------------------------------------------------------- cross-check 1m vs. higher

_FIELDS = ("open", "high", "low", "close", "volume")


@dataclass
class CrossCheck:
    """1m aggregated to ``interval`` against Bitvavo's own ``interval`` candles."""

    market: str
    interval: int
    equal: int = 0
    different: int = 0
    missing_1m: int = 0
    """The higher interval has a bar, 1m has no row in its span."""
    missing_higher: int = 0
    """1m has rows in the span, the higher interval has no bar."""
    fields: dict[str, int] = field(default_factory=dict)
    """How often each OHLCV field differed."""
    examples: list[int] = field(default_factory=list)
    """Open times of the first differing or missing bars."""

    @property
    def compared(self) -> int:
        return self.equal + self.different + self.missing_1m + self.missing_higher

    def text(self) -> str:
        name = TIMEFRAMES[self.interval]
        if not self.compared:
            return f"{self.market} {name}: keine Überschneidung mit 1m"
        parts = (
            f"{self.market} {name}: {self.compared} Kerzen verglichen, {self.equal} gleich, "
            f"{self.different} verschieden, {self.missing_1m} ohne 1m, "
            f"{self.missing_higher} ohne {name}"
        )
        if self.fields:
            parts += " (" + ", ".join(f"{k} {v}" for k, v in sorted(self.fields.items())) + ")"
        return parts


def _aggregate(rows: Sequence[RawCandle]) -> tuple[Decimal, ...]:
    values = [[Decimal(str(v)) for v in r[1:6]] for r in rows]
    return (
        values[0][0],
        max(v[1] for v in values),
        min(v[2] for v in values),
        values[-1][3],
        sum((v[4] for v in values), Decimal(0)),
    )


def cross_check(
    archive: Archive,
    *,
    start: int,
    end: int,
    markets: Iterable[str] = MARKETS,
    intervals: Iterable[int] = (15, 60, 240),
    max_examples: int = 10,
) -> list[CrossCheck]:
    """Compare where both series are complete (done windows only), slot by slot in
    ``[start, end)``. Equality is numeric (Decimal), so ``"1.0"`` equals ``"1"``."""
    out = []
    for market in markets:
        done_1m = archive.windows_done(market, 1)
        for interval in intervals:
            if interval == 1:
                continue
            check = CrossCheck(market, interval)
            step = step_of(interval)
            for h_start in sorted(archive.windows_done(market, interval)):
                h_end = h_start + window_span(interval)
                lo, hi = max(h_start, start), min(h_end, end)
                if lo >= hi:
                    continue
                lo = -(-lo // step) * step
                higher = {int(r[0]) // 1000: r for r in archive.rows(market, interval, lo, hi)}
                minutes: dict[int, list[RawCandle]] = {}
                for r in archive.rows(market, 1, lo, hi):
                    slot = int(r[0]) // 1000 // step * step
                    minutes.setdefault(slot, []).append(r)
                for slot in range(lo, hi, step):
                    if slot + step > hi or window_start(1, slot) not in done_1m:
                        continue
                    _compare_slot(check, slot, higher.get(slot), minutes.get(slot), max_examples)
            out.append(check)
    return out


def _compare_slot(
    check: CrossCheck,
    slot: int,
    bar: RawCandle | None,
    minutes: list[RawCandle] | None,
    max_examples: int,
) -> None:
    if bar is None and not minutes:
        return
    if bar is None:
        check.missing_higher += 1
    elif not minutes:
        check.missing_1m += 1
    else:
        try:
            mine = _aggregate(minutes)
            theirs = tuple(Decimal(str(v)) for v in bar[1:6])
        except InvalidOperation as exc:  # rows passed the live parser; cannot happen
            raise ArchiveError(f"unreadable stored value at {slot}") from exc
        diff = [name for name, a, b in zip(_FIELDS, mine, theirs, strict=True) if a != b]
        if not diff:
            check.equal += 1
            return
        check.different += 1
        for name in diff:
            check.fields[name] = check.fields.get(name, 0) + 1
    if len(check.examples) < max_examples:
        check.examples.append(slot)


def cross_check_text(checks: Iterable[CrossCheck]) -> str:
    """German report of all checks, one line each."""
    lines = ["Abgleich 1m gegen höhere Intervalle (nur vollständig geladene Fenster):"]
    lines += [f"- {c.text()}" for c in checks]
    return "\n".join(lines)
