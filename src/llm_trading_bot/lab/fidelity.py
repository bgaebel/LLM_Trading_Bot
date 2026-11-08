"""Fidelity check of the lab against live (Spec 016 AK5, ADR-0026).

Every snapshot the live bot recorded is rebuilt from the candle archive at its own time
``inputs.utc`` through the unchanged live path (``HistoricalClient`` -> ``build_snapshots`` ->
``indicators`` -> ``run_strategy``) and compared with what live stored:

- classic decisions (``decisions`` of trend/breakout/meanrev/ensemble): action and target
  exactly, the reference signals of the payload, indicators ``round(rebuilt, 5)`` = stored
  within one rounding step, the closed bars;
- GPT snapshots (``llm_calls.context`` per market, or a GPT decision's ``inputs``): the
  reference signals with the depot's weight and ``llm_calls.trigger_signals`` with weight 0,
  as the cadence computes them; GPT's own answer is never rebuilt (ADR-0026: GPT only live);
- with the observations (Spec 015): the windows Bitvavo sent (rows, first/last open, forming
  flag, checksum of the closed rows) and the unrounded indicators (1e-12). The recorded
  forming flag makes the rebuilt window exact.

What counts (AK5, changed with the user's approval 2026-11-08): action and target must be
equal in every comparison; indicators must be exactly equal only where the recording proves
the same input, and every other deviation needs a proven cause. Per recorded window:

- equal: the input is the same;
- truncated: live got fewer rows than asked for (Bitvavo, ``LEARNINGS.md``) and the archive
  holds exactly those rows in live's ``[first_open, last_open]`` (count and checksum): the
  snapshot is rebuilt from that cut window (``HistoricalClient.row_limit``) - the same input;
- LATE: the last closed bar had less volume live than in the archive (late trades);
- OTHER_WINDOW: any other difference.

LATE and OTHER_WINDOW need proof that the difference is Bitvavo's, not the rebuild's choice of
rows: the rebuilt window (cut to live's row count) must equal what the archive holds for live's
request, read apart from ``closed_candles`` (``archive_answer``: the newest rows at the clock,
as many as live got, the forming bar as live had it). If it does not, the window keeps an
empty cause. A snapshot with a LATE or OTHER_WINDOW window, and none without a cause, has a
proven different input: its window deviations carry that cause, its data, indicator, signal
and action deviations ``Eingang verschieden (...)``. A deviation of a snapshot with the same
input keeps an empty cause and fails - a code bug is never explained away. Fidelity is
established for a market only with ``FIDELITY_MIN`` such exactly equal snapshots
(``FidelitySummary.exact``); exactly equal also needs the unrounded indicators recorded
(``decision_meta``), else the snapshot is listed in ``no_raw``.

Costs come from the stored quote (``quote_bid``/``quote_ask``) and the stored fee/slippage
percentages, the weight from the stored ``current_weight`` (rounded to 4 places; for a classic
HOLD the stored target is the unrounded weight). The quotes are never rebuilt: there is no
bid/ask history.

Without observations the archive cannot tell whether live already had the forming bar in its
answer (``history.py``). A deviation that disappears when the forming bar of some intervals is
dropped is marked "laufende Kerze (Naeherung)" and counted as equal - but only for intervals
whose forming bar live could have lacked: no closed 1m bar with a trade in it before the
snapshot. Of the rest, a candle field gets UNRECORDED_DATA (and the indicators then inherit
``Eingang verschieden (ohne Aufzeichnung)``), an indicator otherwise NOT_CHECKABLE; such
snapshots never count as exactly equal. Actions, signals and cost fields stay strict. A
STALE_QUOTE decision is compared with the fail-closed HOLD/0; a classic decision whose inputs
cannot be read is listed as unreadable (only buy & hold has no snapshot to compare). Below
``FIDELITY_MIN`` comparisons per market fidelity is "nicht belegt"
(``FidelitySummary.established``).

USDC depots (Spec 020): ``trend-usdc`` & co. are rebuilt like their classic twin (the same
rules on the same snapshot, ``paper.usdc.strategy_of``); their ETH costs come from the payload
as above. Their BTC decided on the route's costs, ``paper.usdc.roundtrip_cost`` over the
BTC-USDC and USDC-EUR quotes, which the payload does not hold: they are read from the round's
recorded books (``observations.db``, fetches ``start``/``recheck``, the ones the round's
depots read back). Without observations, or when the round's books lack one of the two
markets, such a decision is listed as skipped with the cause ``ROUTE_MISSING`` - never
compared on BTC-EUR costs, never dropped. ``buyhold-usdc`` has no snapshot, like buy & hold.

This is a technical check of the code path: nothing about returns is evaluated, so the sealed
period (ADR-0026) does not apply to it. It reads only copies of the live databases, opened
read-only (``mode=ro``, ``query_only``); a copy needs the user's consent (ADR-0026), and the
lab never runs on the server.
"""

import argparse
import contextlib
import json
import sqlite3
import sys
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from itertools import combinations
from pathlib import Path
from typing import Any, TextIO

from llm_trading_bot.lab.archive import SETTLE_SECONDS, Archive, ArchiveError
from llm_trading_bot.lab.archive import windows as archive_windows
from llm_trading_bot.lab.history import (
    CandleRow,
    CandleSource,
    ConstantSpreads,
    HistoricalClient,
    SpreadModel,
)
from llm_trading_bot.lab.model import FIDELITY_MIN, MARKETS, FidelitySummary
from llm_trading_bot.lab.paths import CANDLES_FILE, REPORTS_DIR, lab_dir
from llm_trading_bot.market.bitvavo import CANDLE_LIMIT
from llm_trading_bot.market.models import (
    Candle,
    ClosedCandles,
    MarketDataError,
    Quote,
    QuoteBook,
    Symbol,
)
from llm_trading_bot.market.snapshot import INTERVALS, MarketSnapshot, build_snapshots
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.cycle import STRATEGY_TIMEFRAME
from llm_trading_bot.paper.db import SchemaError, check_schema
from llm_trading_bot.paper.execution import BUYHOLD, REASON_MAX
from llm_trading_bot.paper.observations import rows_sha256
from llm_trading_bot.paper.observations_schema import check_observations_schema
from llm_trading_bot.strategy.indicators import Indicators, indicators
from llm_trading_bot.strategy.payload import cost_roundtrip
from llm_trading_bot.strategy.signals import Signal, run_strategy

FORMING = "laufende Kerze (Näherung)"
"""Cause of a deviation the forming-bar approximation explains (no observations)."""
ROUGH = "nur grob prüfbar"
"""Cause of a percent field outside one rounding step: listed apart, never failing."""
NOT_COVERED = "Archiv deckt den Zeitpunkt nicht ab"
UNREADABLE = "Eingaben nicht lesbar"
ROUTE_MISSING = "USDC-Quoten der Runde nicht aufgezeichnet"
"""Cause of a skipped BTC decision of a USDC depot: its costs need the round's BTC-USDC and
USDC-EUR quotes, which only observations.db holds (module doc)."""
LATE = "Kerze live vorläufig (späte Trades)"
"""Cause of a recorded window whose last closed bar had less volume live than in the archive:
fetched 5 s after its close, before late trades were added (``LEARNINGS.md``)."""
OTHER_WINDOW = "Live-Fenster anders als Archiv"
"""Cause of any other recorded window that differs from the archive's (rows, open times,
checksum)."""
UNRECORDED_DATA = "Eingangskerze anders als Archiv (ohne Aufzeichnung)"
"""Cause of a candle field (closed bars, last close, gap count) that differs in a snapshot
without a recorded round: its input differs from the archive, how cannot be told."""
NOT_CHECKABLE = "ohne Aufzeichnung nicht prüfbar"
"""Cause of an indicator deviation in a snapshot without a recorded round that neither the
forming bar nor a different input candle explains: without the windows it cannot be decided."""
UNRECORDED = "ohne Aufzeichnung"
DIFFERENT_INPUT = "Eingang verschieden ({})"
"""Cause inherited by the data, indicator, signal and action deviations of a snapshot whose
input is proven different (the window's cause in the brackets). Actions still count as
unequal (``equal_action`` stays strict)."""
ROUTE_PURPOSES = ("start", "recheck")
"""``books.purpose`` of the fetches whose quotes the round's classic and USDC depots read."""
BENCHMARKS = frozenset({BUYHOLD, usdc.BUYHOLD_USDC})
"""Depots without a snapshot behind their decision: they buy once on the quotes."""
DIGITS = 5
"""``feature_payload`` rounds the indicators to 5 places; one step is 1e-5."""
RAW_TOL = 1e-12
"""Unrounded indicators (observations): the same code on the same rows gives the same bits;
the tolerance only allows for a relative last-digit difference."""
ROUGH_DIGITS = {"returns_pct": 3, "quote_spread_bps": 2}
"""Percent fields of the payload and their rounding: compared within one step, apart."""
REFERENCE = ("trend", "breakout", "meanrev")
LEAD_FACTOR = 1.5
"""Rows loaded before the first snapshot per interval, in windows: gaps make 500 rows span
more than 500 intervals (ETH-1m nights)."""
EXIT_ESTABLISHED, EXIT_NOT_ESTABLISHED, EXIT_ERROR = 0, 1, 2
CACHE_SIZE = 64

# Kinds of a deviation (key "kind"); "rough" and "skipped" never fail the check.
ACTION, SIGNAL, INDICATOR, DATA, SNAPSHOT, WINDOW, RAW = (
    "action",
    "signal",
    "indicator",
    "data",
    "snapshot",
    "window",
    "raw",
)
ROUGH_KIND, SKIPPED = "rough", "skipped"
STALE_QUOTE = "STALE_QUOTE"
_FILLED = "filled_no_trade_1m_intervals_last_60"


@dataclass(frozen=True)
class Recorded:
    """One snapshot as live stored it, to be rebuilt and compared."""

    symbol: Symbol
    epoch: int
    utc: str
    agent: str
    payload: Mapping[str, Any]
    weight: float
    source: str
    """``decision`` (classic), ``llm_call`` or ``gpt_decision``."""
    action: str | None = None
    target: float | None = None
    confidence: float | None = None
    reason: str | None = None
    status: str = ""
    bar_ts: int | None = None
    triggers: Mapping[str, str] | None = None
    meta: tuple[str, int, str | None] | None = None
    """``decision_meta`` key: (agent, bar_ts, ts or None for any)."""

    @property
    def market(self) -> str:
        return self.symbol.market

    @property
    def strategy(self) -> str:
        """The rules behind the decision: a USDC depot follows its classic twin (Spec 020)."""
        return usdc.strategy_of(self.agent)

    @property
    def route(self) -> bool:
        """True for the BTC of a USDC depot: decided on the USDC route's costs."""
        return usdc.routed(self.agent, self.symbol)


@dataclass
class Rebuilt:
    snapshot: MarketSnapshot | None
    error: str
    windows: dict[int, ClosedCandles] = field(default_factory=dict)
    indicators: Indicators | None = None


@dataclass(frozen=True)
class ObservedWindow:
    rows: int
    first_open: int | None
    last_open: int | None
    forming: bool
    sha256: str
    last_closed_volume: float | None = None
    """Volume of the last closed bar live got; None if not recorded (NULL)."""


# ---------------------------------------------------------------- reading the live copies


def open_readonly(path: Path) -> sqlite3.Connection:
    """A copy, read-only: ``mode=ro`` cannot create or change the file (ADR-0016, ADR-0026).

    Without a ``-wal`` beside it also ``immutable=1``: SQLite then leaves no ``-shm``/``-wal``
    next to the copy (``mode=ro`` alone creates both for a WAL-mode file). With a non-empty
    ``-wal`` only ``mode=ro``, because ``immutable`` would ignore the rows in it.
    """
    if not path.is_file():
        raise FileNotFoundError(f"keine Datei: {path}")
    wal = path.with_name(path.name + "-wal")
    query = "?mode=ro" if wal.is_file() and wal.stat().st_size else "?mode=ro&immutable=1"
    con = sqlite3.connect(path.resolve().as_uri() + query, uri=True, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=1")
    except BaseException:
        con.close()
        raise
    return con


def _epoch(utc: str) -> int:
    stamp = datetime.fromisoformat(utc)
    if stamp.tzinfo is None:
        raise ValueError(f"utc without zone: {utc!r}")
    return int(stamp.timestamp())


def _symbol(name: str) -> Symbol:
    """``BTCEUR`` (bot.db) or ``BTC-EUR`` (llm_calls.context) -> Symbol."""
    for symbol in Symbol:
        if name in (symbol.name, symbol.market):
            return symbol
    raise ValueError(f"unknown market {name!r}")


_NUMBERS = (
    "quote_bid",
    "quote_ask",
    "taker_fee_per_side_pct",
    "slippage_per_side_pct",
    "current_weight",
)
_OBJECTS = ("indicators", "reference_signals", "returns_pct", "closed_bars_utc")


def _weight(payload: Mapping[str, Any]) -> float:
    """The stored weight, after checking every field the rebuild reads: an incomplete payload
    is listed as unreadable instead of failing the whole check."""
    if not isinstance(payload, dict):
        raise TypeError("inputs not an object")
    for name in _NUMBERS:
        if not _number(payload.get(name)):
            raise ValueError(f"inputs.{name} missing or not a number")
    for name in _OBJECTS:
        if not isinstance(payload.get(name), dict):
            raise ValueError(f"inputs.{name} missing")
    return float(payload["current_weight"])


def recorded_snapshots(bot: sqlite3.Connection) -> tuple[list[Recorded], list[dict[str, Any]]]:
    """Every recorded snapshot in id order, and the ones that cannot be read (skipped)."""
    out: list[Recorded] = []
    skipped: list[dict[str, Any]] = []
    gpt_decisions: dict[tuple[int, Symbol], list[sqlite3.Row]] = {}
    rows = bot.execute(
        "SELECT ts, agent, symbol, bar_ts, action, target, confidence, reason, inputs, status"
        " FROM decisions ORDER BY id"
    ).fetchall()
    for row in rows:
        agent = str(row["agent"])
        if agent in BENCHMARKS:
            continue  # no snapshot behind its decision: it buys once on the quotes
        try:
            payload = json.loads(row["inputs"])
            if not isinstance(payload, dict):
                raise TypeError("inputs not an object")
            symbol = _symbol(str(row["symbol"]))
            if usdc.strategy_of(agent) in STRATEGY_TIMEFRAME:
                stored = row["target"]
                cw = _weight(payload)
                # A classic HOLD (and a clamped BUY/SELL) stores the unrounded weight as target;
                # the payload has it only to 4 places.
                exact = (
                    row["status"] == "OK"
                    and isinstance(stored, float)
                    and round(stored, 4) == round(cw, 4)
                )
                out.append(
                    Recorded(
                        symbol,
                        _epoch(str(payload["utc"])),
                        str(payload["utc"]),
                        agent,
                        payload,
                        stored if exact else cw,
                        "decision",
                        action=str(row["action"]),
                        target=stored,
                        confidence=row["confidence"],
                        reason=str(row["reason"]),
                        status=str(row["status"]),
                        bar_ts=int(row["bar_ts"]),
                        meta=(agent, int(row["bar_ts"]), str(row["ts"])),
                    )
                )
            elif isinstance(payload.get("_llm"), dict):
                attempt = int(payload["_llm"]["attempt"])
                gpt_decisions.setdefault((attempt, symbol), []).append(row)
            else:
                # Neither a classic nor a GPT decision: its snapshot cannot be found, which the
                # check must say instead of comparing fewer rows than live recorded.
                raise ValueError("neither a classic decision nor GPT provenance (_llm)")
        except (ValueError, KeyError, TypeError) as exc:
            skipped.append(_skip(str(row["symbol"]), str(row["ts"]), agent, UNREADABLE, exc))

    seen: set[tuple[int, Symbol]] = set()
    for call in bot.execute(
        "SELECT id, ts, agent, context, trigger_signals FROM llm_calls ORDER BY id"
    ).fetchall():
        try:
            context = json.loads(call["context"])
            triggers = json.loads(call["trigger_signals"] or "{}")
            markets = context["markets"]
            if not isinstance(markets, dict) or not isinstance(triggers, dict):
                raise ValueError("context/trigger_signals not an object")
        except (ValueError, KeyError, TypeError) as exc:
            skipped.append(_skip("", str(call["ts"]), str(call["agent"]), UNREADABLE, exc))
            continue
        for market, payload in markets.items():
            try:
                symbol = _symbol(str(market))
                decided = gpt_decisions.get((int(call["id"]), symbol), [])
                meta = None
                if decided:
                    meta = (str(decided[0]["agent"]), int(decided[0]["bar_ts"]), None)
                seen.add((int(call["id"]), symbol))
                trig = triggers.get(symbol.market)
                out.append(
                    Recorded(
                        symbol,
                        _epoch(str(payload["utc"])),
                        str(payload["utc"]),
                        str(call["agent"]),
                        payload,
                        _weight(payload),
                        "llm_call",
                        triggers=trig if isinstance(trig, dict) else None,
                        meta=meta,
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                skipped.append(
                    _skip(str(market), str(call["ts"]), str(call["agent"]), UNREADABLE, exc)
                )
    # A GPT decision without its call row (not expected): its inputs are still a snapshot.
    for (attempt, symbol), decided in gpt_decisions.items():
        if (attempt, symbol) in seen:
            continue
        row = decided[0]
        try:
            payload = json.loads(row["inputs"])
            out.append(
                Recorded(
                    symbol,
                    _epoch(str(payload["utc"])),
                    str(payload["utc"]),
                    str(row["agent"]),
                    payload,
                    _weight(payload),
                    "gpt_decision",
                    meta=(str(row["agent"]), int(row["bar_ts"]), None),
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            skipped.append(_skip(symbol.name, str(row["ts"]), str(row["agent"]), UNREADABLE, exc))
    return out, skipped


def _skip(market: str, utc: str, agent: str, cause: str, why: object = "") -> dict[str, Any]:
    with contextlib.suppress(ValueError):
        market = _symbol(market).market
    return {
        "market": market,
        "utc": utc,
        "agent": agent,
        "field": "snapshot",
        "live": "",
        "rebuilt": str(why),
        "cause": cause,
        "kind": SKIPPED,
    }


@dataclass
class _WindowIndex:
    """The window rows of one (symbol, interval), sorted by round: ``latest[k]`` is the
    highest row id among the first ``k + 1`` rounds' rows."""

    rounds: list[int] = field(default_factory=list)
    latest: list[int] = field(default_factory=list)


class Observations:
    """What Spec 015 recorded for a snapshot: the windows of its round and the decision's
    unrounded indicators. None where the round or decision was not recorded.

    The copy has no index on ``rounds.ts`` and none that serves "latest window of a symbol
    and interval up to a round", and a read-only copy cannot get one: per snapshot those
    queries read the whole table, so months of observations took hours (quadratic). Both are
    read once here, as sorted keys only; every query per snapshot then goes through an index
    (``snapshots.round_id``, the window's row id, the unique key of ``decision_meta``).
    """

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con
        # Every observations schema so far has the column; a copy without it (hand-made, or a
        # shape check() reads without the schema check) reads as not recorded.
        columns = {str(r[1]) for r in con.execute("PRAGMA table_info(windows)")}
        volume = "last_closed_volume" if "last_closed_volume" in columns else "NULL"
        self._volume = f"{volume} AS last_closed_volume"
        # ISO strings compared as SQLite compares TEXT (bytewise): the same rounds as
        # ``WHERE ts<=? AND ts>=?`` would find.
        rounds = sorted((str(ts), int(rid)) for rid, ts in con.execute("SELECT id, ts FROM rounds"))
        self._round_ts = [ts for ts, _ in rounds]
        self._round_id = [rid for _, rid in rounds]
        grouped: dict[tuple[str, int], list[tuple[int, int]]] = {}
        for wid, rid, sym, interval in con.execute(
            "SELECT id, round_id, symbol, interval FROM windows"
        ):
            grouped.setdefault((str(sym), int(interval)), []).append((int(rid), int(wid)))
        self._windows: dict[tuple[str, int], _WindowIndex] = {}
        for key, pairs in grouped.items():
            pairs.sort()
            index = _WindowIndex()
            best = -1
            for rid, wid in pairs:
                best = max(best, wid)
                index.rounds.append(rid)
                index.latest.append(best)
            self._windows[key] = index

    def _round(self, epoch: int) -> int | None:
        """The last round (highest id) that began in ``[epoch - 30, epoch]``."""
        lo = bisect_left(self._round_ts, _iso(epoch - 30))
        hi = bisect_right(self._round_ts, _iso(epoch))
        return max(self._round_id[lo:hi]) if hi > lo else None

    def _window_id(self, symbol: Symbol, interval: int, round_id: int) -> int | None:
        """Windows are written only when they change (changed_windows): the one a round used
        is the latest row (highest id) of its interval up to that round."""
        index = self._windows.get((symbol.name, interval))
        if index is None:
            return None
        k = bisect_right(index.rounds, round_id)
        return index.latest[k - 1] if k else None

    def windows(self, symbol: Symbol, epoch: int) -> dict[int, ObservedWindow] | None:
        con = self._con
        # The round began before its snapshot (quotes first) and at most a few seconds earlier.
        round_id = self._round(epoch)
        if round_id is None:
            return None
        ok = con.execute(
            "SELECT 1 FROM snapshots WHERE round_id=? AND symbol=? AND status='ok'",
            (round_id, symbol.name),
        ).fetchone()
        if ok is None:
            return None
        out: dict[int, ObservedWindow] = {}
        for interval in INTERVALS:
            wid = self._window_id(symbol, interval, round_id)
            row = (
                None
                if wid is None
                else con.execute(
                    "SELECT rows, first_open, last_open, forming, rows_sha256, "
                    f"{self._volume} FROM windows WHERE id=?",
                    (wid,),
                ).fetchone()
            )
            if row is None:
                return None
            out[interval] = ObservedWindow(
                int(row["rows"]),
                row["first_open"],
                row["last_open"],
                bool(row["forming"]),
                str(row["rows_sha256"]),
                _volume(row["last_closed_volume"]),
            )
        return out

    def route_quotes(self, epoch: int) -> list[dict[Symbol, Quote]]:
        """The BTC-USDC and USDC-EUR quotes a USDC depot of the round at ``epoch`` may have
        decided on: after each ``start``/``recheck`` fetch of that round, the newest accepted
        quote of each route market so far (the depots read the stored quotes back, and a
        market a fetch rejected keeps its earlier quote). Rejected rows carry another purpose.
        In fetch order, repeats left out; empty if the round or a market was not recorded."""
        round_id = self._round(epoch)
        if round_id is None:
            return []
        markets = {m.name: m for m in usdc.ROUTE_MARKETS}
        fetches: dict[tuple[str, str], dict[Symbol, Quote]] = {}
        for row in self._con.execute(
            "SELECT ts, symbol, purpose, bid, ask FROM books WHERE round_id=?"
            f" AND purpose IN ({','.join('?' * len(ROUTE_PURPOSES))}) ORDER BY id",
            (round_id, *ROUTE_PURPOSES),
        ):
            symbol = markets.get(str(row["symbol"]))
            if symbol is not None:
                key = (str(row["ts"]), str(row["purpose"]))
                fetches.setdefault(key, {})[symbol] = Quote(float(row["bid"]), float(row["ask"]))
        out: list[dict[Symbol, Quote]] = []
        current: dict[Symbol, Quote] = {}
        for quotes in fetches.values():
            current.update(quotes)
            if len(current) == len(markets) and (not out or out[-1] != current):
                out.append(dict(current))
        return out

    def indicators(self, symbol: Symbol, meta: tuple[str, int, str | None]) -> dict[str, Any]:
        agent, bar_ts, ts = meta
        sql = "SELECT indicators FROM decision_meta WHERE agent=? AND symbol=? AND bar_ts=?"
        args: tuple[object, ...] = (agent, symbol.name, bar_ts)
        if ts is not None:
            # A restored bot.db can repeat a key; the time tells the decisions apart.
            sql += " AND ts=?"
            args = (*args, ts)
        row = self._con.execute(sql, args).fetchone()
        if row is None:
            return {}
        data = json.loads(row["indicators"])
        return data if isinstance(data, dict) else {}


def _volume(value: object) -> float | None:
    """The recorded volume as a number (REAL, or a number in TEXT); None if absent."""
    if not isinstance(value, int | float | str) or isinstance(value, bool):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- rebuilding


class _LeadSource:
    """Loads each interval only from its own lead before the first snapshot: one start for all
    intervals would load months of 1m rows that no window reaches."""

    def __init__(self, source: CandleSource, starts: Mapping[int, int]) -> None:
        self._source = source
        self._starts = starts

    def rows(self, market: str, interval: int, start: int, end: int) -> list[CandleRow]:
        return self._source.rows(market, interval, max(start, self._starts[interval]), end)


class _ArchiveRows:
    """The archive as a ``CandleSource`` (its rows are the ``/candles`` shape)."""

    def __init__(self, archive: Archive) -> None:
        self._archive = archive

    def rows(self, market: str, interval: int, start: int, end: int) -> list[CandleRow]:
        return [row for row in self._archive.rows(market, interval, start, end)]


class _OneMarket:
    """The historical candles of one market with the quote live stored for it."""

    def __init__(self, client: HistoricalClient, symbol: Symbol, quote: Quote) -> None:
        self._client = client
        self._symbol = symbol
        self._quote = quote

    def quotes(self) -> QuoteBook:
        return QuoteBook({self._symbol: self._quote})

    def closed_candles(self, symbol: Symbol, interval: int, *, now_epoch: int) -> ClosedCandles:
        if symbol is not self._symbol:
            # Only this market is rebuilt; build_snapshots reports the other one and goes on.
            raise MarketDataError("fidelity: other market not rebuilt", "other")
        return self._client.closed_candles(symbol, interval, now_epoch=now_epoch)


Hint = Mapping[int, bool | None]
Caps = Mapping[int, int]
"""Interval -> rows live got, where the recording proves a cut window (truncated)."""


class Rebuilder:
    """Rebuilds recorded snapshots from candle rows; one preload for the whole live period."""

    def __init__(
        self,
        source: CandleSource,
        epochs: Sequence[int],
        spreads: SpreadModel | None = None,
    ) -> None:
        first, last = min(epochs), max(epochs)
        starts = {i: first - int(CANDLE_LIMIT * LEAD_FACTOR) * i * 60 - 86_400 for i in INTERVALS}
        self._hint: Hint = {}
        self._caps: dict[int, int] = {}
        self.client = HistoricalClient(
            _LeadSource(source, starts),
            spreads or ConstantSpreads(),
            # Only the client's own quotes read this clock; every quote here is the stored one.
            lambda: float(first),
            start=min(starts.values()),
            end=last + 1,
            forming_hint=lambda _symbol, interval, _now: self._hint.get(interval),
            row_limit=lambda _symbol, interval, _now: self._caps.get(interval),
        )
        # The depots of one round share its snapshot; a few recent rebuilds suffice, and a
        # bounded cache keeps months of snapshots (500 bars x 4 intervals each) out of memory.
        self._cache: OrderedDict[tuple[object, ...], Rebuilt] = OrderedDict()

    def lead_ok(self, symbol: Symbol, epoch: int) -> bool:
        """Live always got CANDLE_LIMIT rows; fewer in the archive cannot be rebuilt."""
        return all(self.client.rows_before(symbol, i, epoch + 1) >= CANDLE_LIMIT for i in INTERVALS)

    def traded(self, symbol: Symbol, interval: int, epoch: int) -> bool:
        """True if the bar of ``interval`` forming at ``epoch`` had a trade before ``epoch``:
        a 1m row with volume that had closed by then (open in ``[forming_open, epoch - 60]``).
        The 1m bar still forming at ``epoch`` proves nothing: its trade may have come later."""
        step = interval * 60
        forming_open = epoch // step * step
        minutes = self.client.candles_between(symbol, 1, forming_open, epoch - 60 + 1)
        return any(c.volume > 0 for c in minutes)

    def rebuild(
        self,
        rec: Recorded,
        hint: Hint,
        route: Mapping[Symbol, Quote] | None = None,
        caps: Caps | None = None,
    ) -> Rebuilt:
        """``route``: the recorded route quotes for the BTC of a USDC depot (``rec.route``);
        its costs are then the route's (``paper.usdc.roundtrip_cost``), as live. ``caps``: the
        windows to cut to the rows live got (truncated)."""
        p = rec.payload
        quote = Quote(float(p["quote_bid"]), float(p["quote_ask"]))
        fee = float(p["taker_fee_per_side_pct"]) / 100
        slip = float(p["slippage_per_side_pct"]) / 100
        routes = tuple(sorted((s.name, q) for s, q in (route or {}).items()))
        cut = tuple(sorted((caps or {}).items()))
        key = (rec.symbol, rec.epoch, quote, fee, slip, tuple(sorted(hint.items())), routes, cut)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        self._hint = hint
        self._caps = dict(cut)
        windows: dict[int, ClosedCandles] = {}
        errors: dict[Symbol, Exception] = {}

        def on_window(symbol: Symbol, tf: int, rows: tuple[Candle, ...], _at: int) -> None:
            if symbol is rec.symbol and isinstance(rows, ClosedCandles):
                windows[tf] = rows

        # The unchanged live path, at the stored time and quote (build_snapshots validates).
        snaps = build_snapshots(
            _OneMarket(self.client, rec.symbol, quote),
            clock=lambda: rec.epoch,
            on_error=errors.__setitem__,
            on_window=on_window,
        )
        snap = snaps.get(rec.symbol)
        if snap is None:
            exc = errors.get(rec.symbol)
            result = Rebuilt(None, f"{type(exc).__name__}: {exc}", windows)
        else:
            if route is not None:
                # Spec 020 AK3: both legs' fees and slippage plus the spreads of both books.
                cost = usdc.roundtrip_cost(route)
            else:
                cost = cost_roundtrip(snap.quote.spread_bps, fee, slip)
            result = Rebuilt(snap, "", windows, indicators(snap, cost))
        self._cache[key] = result
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return result


# ---------------------------------------------------------------- comparing


def _steps(rebuilt: float, stored: float, digits: int) -> int:
    """Rounding steps between the rebuilt value, rounded like the payload, and the stored one.
    Counted in whole steps, so float noise in ``a - b`` cannot turn one step into two."""
    scale: int = 10**digits
    return abs(round(round(rebuilt, digits) * scale) - round(stored * scale))


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _float(value: object) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"not a number: {value!r}")
    return float(value)


def _expected(rec: Recorded, sig: Signal) -> tuple[str, float, bool]:
    """Action and target as record_decision stores them; True if clamped to the weight."""
    action, target = sig.action, sig.target_fraction
    if rec.status == STALE_QUOTE:
        # Fail closed: without fresh quotes record_decision stores HOLD with target 0 whatever
        # the signal said; any other stored value is a regression of that path.
        return "HOLD", 0.0, False
    if rec.status != "OK":
        return action, target, False  # PAUSED keeps the raw signal
    weight = rec.weight
    if action == "HOLD" or (action == "BUY" and target < weight):
        return action, weight, True
    if action == "SELL" and target > weight:
        return action, weight, True
    return action, target, False


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def compare(
    rec: Recorded,
    got: Rebuilt,
    observed: Mapping[int, ObservedWindow] | None = None,
    raw: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every difference between what live stored and the rebuild, without a cause yet."""
    out: list[dict[str, Any]] = []

    def add(kind: str, name: str, live: object, rebuilt: object) -> None:
        out.append(
            {
                "market": rec.market,
                "utc": rec.utc,
                "agent": rec.agent,
                "field": name,
                "live": live,
                "rebuilt": rebuilt,
                "cause": "",
                "kind": kind,
            }
        )

    if observed is not None:
        for tf, want in observed.items():
            have = got.windows.get(tf)
            if have is None:
                add(WINDOW, f"window.{tf}m", "recorded", "missing")
                continue
            for name, live, rebuilt in (
                ("rows", want.rows, have.received),
                ("first_open", want.first_open, have.first_open),
                ("last_open", want.last_open, have.last_open),
                ("forming", want.forming, have.forming),
                ("rows_sha256", want.sha256, rows_sha256(have)),
            ):
                if live != rebuilt:
                    add(WINDOW, f"window.{tf}m.{name}", live, rebuilt)
    snap, ind = got.snapshot, got.indicators
    if snap is None or ind is None:
        add(SNAPSHOT, "snapshot", "ok", got.error)
        return out
    p = rec.payload
    bars = p.get("closed_bars_utc", {})
    for tf in INTERVALS:
        if bars.get(str(tf)) != _utc(snap.bars[tf][-1].ts):
            add(DATA, f"closed_bars_utc.{tf}", bars.get(str(tf)), _utc(snap.bars[tf][-1].ts))
    if rec.bar_ts is not None:
        own = snap.bars[STRATEGY_TIMEFRAME[rec.strategy]][-1].ts
        if own != rec.bar_ts:
            add(DATA, "bar_ts", rec.bar_ts, own)
    last_close = snap.bars[1][-1].close
    if p.get("latest_completed_1m_close") != last_close:
        add(DATA, "latest_completed_1m_close", p.get("latest_completed_1m_close"), last_close)
    filled = sum(b.volume == 0 for b in snap.bars[1][-60:])
    if p.get(_FILLED) != filled:
        add(DATA, _FILLED, p.get(_FILLED), filled)
    if rec.route:
        # The rebuild costs the route with paper.usdc's numbers; live must have stored them.
        for name, rate in (
            ("taker_fee_per_side_pct", usdc.ROUTE_FEE),
            ("slippage_per_side_pct", usdc.ROUTE_SLIPPAGE),
        ):
            stored_rate = round(100 * rate, 3)
            if p.get(name) != stored_rate:
                add(DATA, name, p.get(name), stored_rate)

    stored_ind = p.get("indicators", {})
    for name, value in ind.items():
        live = stored_ind.get(name)
        if _number(value) and _number(live):
            if _steps(_float(value), _float(live), DIGITS) > 1:
                add(INDICATOR, f"indicators.{name}", live, round(_float(value), DIGITS))
        elif live != value:
            add(INDICATOR, f"indicators.{name}", live, value)
    mine_raw: Mapping[str, object] = ind
    for name, value in (raw or {}).items():
        mine = mine_raw.get(name)
        if _number(value) and _number(mine):
            a, b = _float(value), _float(mine)
            if abs(a - b) > RAW_TOL * max(1.0, abs(a)):
                add(RAW, f"indicators_raw.{name}", value, mine)
        elif value != mine:
            add(RAW, f"indicators_raw.{name}", value, mine)

    for name in REFERENCE:
        mine_action = run_strategy(name, snap, ind, rec.weight).action
        live_action = p.get("reference_signals", {}).get(name)
        if live_action != mine_action:
            add(ACTION, f"reference_signals.{name}", live_action, mine_action)
        if rec.triggers is not None:
            trig = run_strategy(name, snap, ind, 0.0).action
            if rec.triggers.get(name) != trig:
                add(ACTION, f"trigger_signals.{name}", rec.triggers.get(name), trig)

    if rec.source == "decision":
        sig = run_strategy(rec.strategy, snap, ind, rec.weight)
        action, target, clamped = _expected(rec, sig)
        if rec.action != action:
            add(ACTION, "action", rec.action, action)
        stored = rec.target
        same = stored == target or (
            clamped and stored is not None and round(stored, 4) == round(target, 4)
        )
        if not same:
            add(ACTION, "target", stored, target)
        if rec.confidence != sig.confidence:
            add(SIGNAL, "confidence", rec.confidence, sig.confidence)
        # A classic STALE_QUOTE began as OK in record_decision: its reason was trimmed too.
        trimmed = rec.status in ("OK", STALE_QUOTE)
        reason = sig.reason.strip()[:REASON_MAX] if trimmed else sig.reason
        if rec.reason != reason:
            add(SIGNAL, "reason", rec.reason, reason)

    # Percent fields: computed from the stored quote and the closes, rounded coarser.
    mid = snap.quote.mid
    returns = p.get("returns_pct", {})
    for minutes, tf, n in ((15, 1, 15), (60, 15, 4), (240, 60, 4), (1440, 60, 24)):
        mine_pct = 100 * (mid / snap.bars[tf][-1 - n].close - 1)
        live_pct = returns.get(str(minutes))
        if not _number(live_pct) or _steps(mine_pct, _float(live_pct), 3) > 1:
            add(ROUGH_KIND, f"returns_pct.{minutes}", live_pct, round(mine_pct, 3))
    spread = p.get("quote_spread_bps")
    if not _number(spread) or _steps(snap.quote.spread_bps, _float(spread), 2) > 1:
        add(ROUGH_KIND, "quote_spread_bps", spread, round(snap.quote.spread_bps, 2))
    return out


_STRICT = frozenset({ACTION, SIGNAL, INDICATOR, DATA, SNAPSHOT, WINDOW, RAW})


def _failing(deviations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in deviations if d["kind"] in _STRICT]


def _counts(deviations: Sequence[Mapping[str, Any]]) -> tuple[bool, bool]:
    """(action and target equal, indicators within one step)."""
    kinds = {d["kind"] for d in deviations}
    return not kinds & {ACTION, SNAPSHOT}, not kinds & {INDICATOR, SNAPSHOT}


def _alternatives(
    rec: Recorded, rebuilder: Rebuilder, got: Rebuilt
) -> Iterator[dict[int, bool | None]]:
    """Forming-bar counts to try, fewest flips first: only intervals whose forming bar the
    default rebuild counted and live could have lacked. Bitvavo leaves the forming bar out
    only while it has no trade; a closed 1m bar with volume inside it proves live had it, and
    then dropping it would hide a real deviation (a window-start bug, say)."""
    flippable = [
        tf
        for tf, w in sorted(got.windows.items())
        if w.forming and not rebuilder.traded(rec.symbol, tf, rec.epoch)
    ]
    for size in range(1, len(flippable) + 1):
        for chosen in combinations(flippable, size):
            yield dict.fromkeys(chosen, False)


def _explain(
    rec: Recorded,
    rebuilder: Rebuilder,
    got: Rebuilt,
    raw: Mapping[str, Any] | None,
    found: list[dict[str, Any]],
    route: Mapping[Symbol, Quote] | None = None,
) -> list[dict[str, Any]] | None:
    """Without observations: the forming-bar count that leaves the fewest deviations, if it
    leaves only some of ``found``. Those it removes get the cause FORMING (in place); the rest
    stay unexplained. Returns that rebuild's deviations, or None if no count helps."""
    failing = {d["field"]: d for d in _failing(found)}
    best: tuple[dict[int, bool | None], list[dict[str, Any]]] | None = None
    for alt in _alternatives(rec, rebuilder, got):
        again = compare(rec, rebuilder.rebuild(rec, alt, route), None, raw)
        left = {d["field"] for d in _failing(again)}
        # Only a strict improvement over the same fields: a count that trades one deviation
        # for another explains nothing.
        if left < failing.keys() and (best is None or len(left) < len(_failing(best[1]))):
            best = (alt, again)
            if not left:
                break
    if best is None:
        return None
    alt, again = best
    left = {d["field"] for d in _failing(again)}
    flipped = ", ".join(f"{tf}m" for tf in sorted(alt))
    for name, d in failing.items():
        if name not in left:
            d["cause"] = FORMING
            d["detail"] = f"gleich ohne laufende Kerze in {flipped}"
    return again


def _same_window(want: ObservedWindow, have: ClosedCandles) -> bool:
    return (
        want.rows == have.received
        and want.first_open == have.first_open
        and want.last_open == have.last_open
        and want.forming == have.forming
        and want.sha256 == rows_sha256(have)
    )


def _same_rows(a: ClosedCandles, b: ClosedCandles) -> bool:
    return (
        a.received == b.received
        and a.first_open == b.first_open
        and a.last_open == b.last_open
        and a.forming == b.forming
        and rows_sha256(a) == rows_sha256(b)
    )


def _truncated(
    rec: Recorded,
    rebuilder: Rebuilder,
    hint: Hint,
    route: Mapping[Symbol, Quote] | None,
    windows: Mapping[int, ObservedWindow],
    got: Rebuilt,
) -> tuple[dict[int, int], dict[int, ClosedCandles]]:
    """The windows live got shorter than the rebuild and whose rows the archive holds exactly:
    cut to live's row count (same newest row), the rebuilt window must equal the recorded one
    in rows, open times, forming flag and checksum. Only then is the input the same; a cut that
    does not reproduce live's window is never applied. Also returns every cut window tried
    (the rebuild's window for live's row count, which ``_window_cause`` checks)."""
    wanted = {
        tf: w.rows
        for tf, w in windows.items()
        if (have := got.windows.get(tf)) is not None and w.rows < have.received
    }
    if not wanted:
        return {}, {}
    trial = rebuilder.rebuild(rec, hint, route, wanted)
    tried = {tf: have for tf in wanted if (have := trial.windows.get(tf)) is not None}
    caps = {
        tf: n for tf, n in wanted.items() if tf in tried and _same_window(windows[tf], tried[tf])
    }
    return caps, tried


def archive_answer(
    client: HistoricalClient, symbol: Symbol, epoch: int, interval: int, want: ObservedWindow
) -> ClosedCandles:
    """What the archive holds for live's request as the recording describes it: the newest
    ``want.rows`` rows at the clock, the forming bar only if live had it. Read with
    ``newest_candles``, not through ``closed_candles``: a bug in the rebuild's choice of rows
    must show as a difference of the rebuild to this, never as one of live to the archive."""
    step = interval * 60
    rows = client.newest_candles(
        symbol, interval, epoch if want.forming else epoch - step, want.rows
    )
    closed = tuple(c for c in rows if c.ts <= epoch - step)
    return ClosedCandles(
        closed,
        received=len(rows),
        first_open=rows[0].ts if rows else None,
        last_open=rows[-1].ts if rows else None,
        forming=len(closed) < len(rows),
    )


def _when(epoch: int | None) -> str:
    return "-" if epoch is None else _iso(epoch)


def _shape(w: ClosedCandles | None) -> str:
    if w is None:
        return "kein Fenster"
    return (
        f"{w.received} Zeilen {_when(w.first_open)} bis {_when(w.last_open)}, "
        f"Prüfsumme {rows_sha256(w)[:12]}"
    )


def _late_only(want: ObservedWindow, archive: ClosedCandles, volume: float) -> bool:
    """True if live's window is the archive's with only the last closed bar's volume live's."""
    late = (*archive[:-1], replace(archive[-1], volume=volume))
    return rows_sha256(late) == want.sha256


def _window_cause(
    tf: int, want: ObservedWindow, lab: ClosedCandles | None, archive: ClosedCandles
) -> tuple[str, str]:
    """Why a recorded window differs from the rebuilt one; an empty cause if nothing is proven.

    ``lab`` is the rebuild's window for live's row count, ``archive`` what the archive holds for
    live's request (``archive_answer``). The rebuild must equal the archive: otherwise it chose
    other rows than the archive holds - a code bug, never a cause, whatever live got. Only
    where it does is live's difference one of Bitvavo's answer to the archive: LATE if the
    window is the archive's one (same rows, open times, forming flag) and its last closed bar
    had less volume live - late trades are only ever added, so more volume live is no late
    bar; else OTHER_WINDOW (the archive's rows over live's request differ from live's)."""
    if lab is None or not _same_rows(lab, archive):
        # Also where the archive holds exactly what live got: the rebuild should have
        # reproduced it. (Equal to the archive, the rebuild differs from live only where the
        # archive does: a deviating window then cannot equal the archive's.)
        return "", f"{tf}m: Nachbau {_shape(lab)}; Archiv für die Live-Anfrage {_shape(archive)}"
    cut = "" if want.rows == CANDLE_LIMIT else f"; Live-Fenster gekürzt auf {want.rows} Zeilen"
    live = want.last_closed_volume
    if (
        live is not None
        and len(archive) > 0
        and (want.rows, want.first_open, want.last_open, want.forming)
        == (archive.received, archive.first_open, archive.last_open, archive.forming)
        and live < archive[-1].volume
    ):
        rest = (
            "übrige Werte gleich"
            if _late_only(want, archive, live)
            else "auch Preise der Kerze oder weitere Zeilen anders"
        )
        return LATE, (
            f"{tf}m: Volumen der letzten geschlossenen Kerze ({_iso(archive[-1].ts)}) live "
            f"{live} statt {archive[-1].volume} im Archiv, {rest}{cut}"
        )
    return OTHER_WINDOW, (
        f"{tf}m: live {want.rows} Zeilen {_when(want.first_open)} bis {_when(want.last_open)}, "
        f"Prüfsumme {want.sha256[:12]}; Archiv {_shape(archive)}"
    )


_CANDLE_DATA = ("bar_ts", "latest_completed_1m_close", _FILLED)
_INHERITS = frozenset({DATA, INDICATOR, RAW, ACTION, SIGNAL})


def _from_candles(d: Mapping[str, Any]) -> bool:
    """True for a deviation a different input candle can cause. Not the cost inputs: the route
    fees and ``roundtrip_cost_pct`` come from the stored quotes and rates, never from candles,
    so a different candle never explains them."""
    name = str(d["field"])
    if d["kind"] == DATA:
        return name.startswith("closed_bars_utc.") or name in _CANDLE_DATA
    return not name.endswith(".roundtrip_cost_pct")


def _interval(name: str) -> int:
    """``window.60m.rows`` -> 60."""
    return int(name.split(".")[1].removesuffix("m"))


def _observed_cause(
    found: list[dict[str, Any]],
    windows: Mapping[int, ObservedWindow],
    got: Rebuilt,
    lab: Mapping[int, ClosedCandles],
    archive: Callable[[int, ObservedWindow], ClosedCandles],
) -> str | None:
    """With a recorded round: None if every window is the same input, or if a window's
    difference is not proven to be Bitvavo's (``_window_cause``: the snapshot then only fails);
    else the proven cause (OTHER_WINDOW over LATE), written into the deviations in place
    (module doc). ``lab``: the rebuild's window per interval for live's row count."""
    causes: dict[int, tuple[str, str]] = {}
    for tf, want in windows.items():
        have = got.windows.get(tf)
        if have is None or not _same_window(want, have):
            causes[tf] = _window_cause(tf, want, lab.get(tf), archive(tf, want))
    if not causes:
        return None
    for d in found:
        if d["kind"] == WINDOW:
            d["cause"], d["detail"] = causes[_interval(str(d["field"]))]
    if not all(c for c, _ in causes.values()):
        # One window the rebuild got wrong: nothing about this snapshot's input is proven, so
        # none of its deviations may inherit a cause (a code bug is never explained away).
        return None
    cause = OTHER_WINDOW if any(c == OTHER_WINDOW for c, _ in causes.values()) else LATE
    inherited = DIFFERENT_INPUT.format(cause)
    for d in found:
        if d["kind"] in _INHERITS and not d["cause"] and _from_candles(d):
            d["cause"] = inherited
    return cause


def _unrecorded_cause(found: list[dict[str, Any]]) -> str | None:
    """Without a recorded round, after the forming-bar approximation: a candle field that
    still differs proves another input (UNRECORDED_DATA, inherited by the rest); otherwise an
    indicator deviation cannot be decided (NOT_CHECKABLE). Actions, signals and cost fields
    keep an empty cause there."""
    data = [d for d in found if d["kind"] == DATA and not d["cause"] and _from_candles(d)]
    for d in data:
        d["cause"] = UNRECORDED_DATA
    if data:
        inherited = DIFFERENT_INPUT.format(UNRECORDED)
        for d in found:
            if d["kind"] in _INHERITS and not d["cause"] and _from_candles(d):
                d["cause"] = inherited
        return UNRECORDED_DATA
    for d in found:
        if d["kind"] in (INDICATOR, RAW) and not d["cause"] and _from_candles(d):
            d["cause"] = NOT_CHECKABLE
    return None


@dataclass
class _Tally:
    summary: FidelitySummary
    skipped: int = 0
    unreadable: int = 0
    """Of ``skipped``: rows whose inputs could not be read (never compared)."""
    explained: int = 0
    unobserved: int = 0
    """With observations: snapshots whose round was not recorded (compared as without)."""
    routed: int = 0
    """BTC decisions of USDC depots compared on the recorded route quotes."""
    no_route: int = 0
    """Of ``skipped``: BTC decisions of USDC depots without recorded route quotes."""

    def count(self, market: str, equal: bool, ind_ok: bool) -> None:
        s = self.summary
        s.compared[market] = s.compared.get(market, 0) + 1
        s.equal_action[market] = s.equal_action.get(market, 0) + int(equal)
        s.indicators_ok[market] = s.indicators_ok.get(market, 0) + int(ind_ok)

    def classify(
        self,
        market: str,
        *,
        observed: bool,
        cause: str | None,
        cut: bool,
        clean: bool,
        raw: bool,
    ) -> None:
        """One comparison into exact / no_raw / different / unrecorded (``FidelitySummary``);
        one with a recorded, equal input that still deviates goes nowhere: it fails as
        unexplained. ``raw``: its unrounded indicators were recorded and all compared."""
        s = self.summary
        if cause is not None:
            causes = s.different.setdefault(market, {})
            causes[cause] = causes.get(cause, 0) + 1
        elif not observed:
            s.unrecorded[market] = s.unrecorded.get(market, 0) + 1
        elif clean and not raw:
            # AK5 needs the unrounded indicators at 1e-12 for "exakt gleich"; the rounded
            # payload alone lets a code bug below one rounding step through.
            s.no_raw[market] = s.no_raw.get(market, 0) + 1
        elif clean:
            s.exact[market] = s.exact.get(market, 0) + 1
            s.truncated[market] = s.truncated.get(market, 0) + int(cut)


def check(
    bot: sqlite3.Connection,
    source: CandleSource,
    *,
    obs: sqlite3.Connection | None = None,
    covered: Callable[[str, int], bool] | None = None,
    spreads: SpreadModel | None = None,
    progress: TextIO | None = None,
) -> FidelitySummary:
    """Compare every recorded snapshot of ``bot`` with its rebuild from ``source``.

    ``covered(market, epoch)`` says whether the archive holds the rows up to that time;
    snapshots outside are listed as skipped, never compared. The connections are only read.
    """
    recs, unreadable = recorded_snapshots(bot)
    summary = FidelitySummary(with_observations=obs is not None)
    tally = _Tally(summary)
    for market in MARKETS:
        summary.compared[market] = summary.equal_action[market] = 0
        summary.indicators_ok[market] = summary.exact[market] = 0
        summary.truncated[market] = summary.unrecorded[market] = summary.no_raw[market] = 0
    summary.deviations.extend(unreadable)
    tally.skipped += len(unreadable)
    tally.unreadable = len(unreadable)
    observations = Observations(obs) if obs is not None else None
    rebuilder = Rebuilder(source, [r.epoch for r in recs], spreads) if recs else None
    for n, rec in enumerate(recs, 1):
        assert rebuilder is not None
        if progress is not None and n % 1000 == 0:
            progress.write(f"  {n} von {len(recs)} Snapshots verglichen\n")
        if (covered is not None and not covered(rec.market, rec.epoch)) or not (
            rebuilder.lead_ok(rec.symbol, rec.epoch)
        ):
            summary.deviations.append(_skip(rec.market, rec.utc, rec.agent, NOT_COVERED))
            tally.skipped += 1
            continue
        routes: list[dict[Symbol, Quote]] = []
        if rec.route:
            routes = [] if observations is None else observations.route_quotes(rec.epoch)
            if not routes:
                why = (
                    "ohne observations.db"
                    if observations is None
                    else "Runde ohne BTC-USDC/USDC-EUR"
                )
                summary.deviations.append(_skip(rec.market, rec.utc, rec.agent, ROUTE_MISSING, why))
                tally.skipped += 1
                tally.no_route += 1
                continue
            tally.routed += 1
        windows = None if observations is None else observations.windows(rec.symbol, rec.epoch)
        raw = None
        if observations is not None and rec.meta is not None:
            raw = observations.indicators(rec.symbol, rec.meta)
        hint: dict[int, bool | None] = {}
        if observations is not None and windows is None:
            tally.unobserved += 1
        if windows is not None:
            hint = {tf: w.forming for tf, w in windows.items()}
        route = routes[0] if routes else None
        got = rebuilder.rebuild(rec, hint, route)
        caps: dict[int, int] = {}
        tried: dict[int, ClosedCandles] = {}
        if windows is not None:
            caps, tried = _truncated(rec, rebuilder, hint, route, windows, got)
            if caps:
                got = rebuilder.rebuild(rec, hint, route, caps)
        found = compare(rec, got, windows, raw)
        # A round that fetched again (recheck) offers more than one quote set; the decision
        # read one of them. The first that leaves nothing failing is the one it read.
        for other in routes[1:] if _failing(found) else []:
            again = rebuilder.rebuild(rec, hint, other, caps)
            alt = compare(rec, again, windows, raw)
            if not _failing(alt):
                route, got, found = other, again, alt
                break
        equal, ind_ok = _counts(found)
        if _failing(found) and windows is None:
            better = _explain(rec, rebuilder, got, raw, found, route)
            if better is not None:
                equal, ind_ok = _counts(better)
                tally.explained += not _failing(better)
        for d in found:
            if d["kind"] == ROUGH_KIND:
                d["cause"] = ROUGH
        if windows is not None:
            # The rebuild's window for live's row count: the cut one where live got fewer rows.
            lab = {**got.windows, **{tf: w for tf, w in tried.items() if tf not in caps}}
            answer = partial(archive_answer, rebuilder.client, rec.symbol, rec.epoch)
            cause = _observed_cause(found, windows, got, lab, answer)
        else:
            cause = _unrecorded_cause(found)
        summary.deviations.extend(found)
        tally.count(rec.market, equal, ind_ok)
        tally.classify(
            rec.market,
            observed=windows is not None,
            cause=cause,
            cut=bool(caps),
            clean=not _failing(found),
            raw=_raw_complete(raw, got.indicators),
        )
    summary.note = _note(tally, obs is not None)
    return summary


def _raw_complete(raw: Mapping[str, Any] | None, ind: Indicators | None) -> bool:
    """True if the unrounded indicators were recorded for every indicator the rebuild has:
    only then did ``compare`` check all of them at ``RAW_TOL``."""
    if not raw or ind is None:
        return False
    return all(name in raw for name in ind)


def _note(tally: _Tally, with_obs: bool) -> str:
    parts = [
        "Technischer Abgleich des Live-Codes mit dem Nachbau aus dem Kerzenarchiv; es werden "
        "keine Renditen bewertet, daher gilt die Versiegelung ab 2025-10-01 hier nicht "
        "(ADR-0026).",
        "Kosten aus der gespeicherten Quote, Gewicht aus current_weight (4 Stellen).",
    ]
    if with_obs:
        parts.append(
            "Mit Beobachtungen: Fenster und ungerundete Indikatoren geprüft, laufende Kerze "
            "laut Aufzeichnung. Exakt gleich muss nur sein, wo die Aufzeichnung gleichen "
            "Eingang belegt; ein kürzeres Live-Fenster, dessen Zeilen das Archiv genau so hat, "
            "gilt als gleicher Eingang."
        )
        if tally.unobserved:
            parts.append(
                f"{tally.unobserved} Snapshots ohne aufgezeichnete Runde, wie ohne "
                f"Beobachtungen geprüft; {tally.explained} davon nur mit der Näherung "
                "laufende Kerze erklärt."
            )
    else:
        parts.append(
            "Ohne Beobachtungen: ob live die laufende Kerze mitkam, ist unbekannt (Näherung); "
            f"{tally.explained} Vergleiche nur damit erklärt. Ohne Aufzeichnung ist kein "
            "Eingang belegt gleich, die Treue also nie belegt."
        )
    parts.append(
        f"Belegt ist die Treue erst ab {FIDELITY_MIN} exakt gleichen Snapshots mit belegt "
        "gleichem Eingang je Markt."
    )
    if tally.routed:
        parts.append(
            f"{tally.routed} BTC-Entscheidungen der USDC-Depots mit den Kosten des USDC-Wegs aus "
            "den aufgezeichneten BTC-USDC- und USDC-EUR-Quoten der Runde verglichen."
        )
    if tally.skipped:
        parts.append(f"{tally.skipped} Snapshots übersprungen (nicht verglichen).")
    if tally.no_route:
        parts.append(
            f"{tally.no_route} davon BTC-Entscheidungen der USDC-Depots ohne aufgezeichnete "
            "USDC-Quoten (nur mit observations.db nachbaubar)."
        )
    if tally.unreadable:
        parts.append(
            f"{tally.unreadable} davon mit nicht lesbaren Eingaben: diese Entscheidungen "
            "konnten nicht nachgebaut werden."
        )
    return " ".join(parts)


# ---------------------------------------------------------------- the archive and the files


def archive_coverage(archive: Archive) -> Callable[[str, int], bool]:
    """True if, for every interval, each archive window the rebuild at ``epoch`` reads is
    complete, or was fetched after the bars it must hold had closed and settled.

    The rebuild reads the newest ``CANDLE_LIMIT`` rows (one bar further back without the
    forming bar), and these reach into earlier windows. A window left open by an earlier
    fetch has a gap there; the 500 rows then start earlier than live's, and the deviations
    would blame the live code for a hole in the archive. The 1m rows also have to cover the
    forming bar of every interval (``Rebuilder.traded``).
    """
    states = {
        (str(m), int(i), int(s)): (str(status), int(fetched))
        for m, i, s, status, fetched in archive.db.execute(
            "SELECT market, interval, start, status, fetched_at FROM windows"
        )
    }
    reach: dict[tuple[str, int, int], int | None] = {}
    cache: dict[tuple[str, int], bool] = {}

    def first_open(market: str, interval: int, last: int) -> int | None:
        """Open of the oldest of the newest CANDLE_LIMIT rows opened at or before ``last``
        (the primary key serves it); None without a row."""
        key = (market, interval, last)
        if key not in reach:
            row = archive.db.execute(
                "SELECT MIN(ts) FROM (SELECT ts FROM candles WHERE market=? AND interval=?"
                " AND ts<=? ORDER BY ts DESC LIMIT ?)",
                (market, interval, last, CANDLE_LIMIT),
            ).fetchone()
            reach[key] = None if row is None or row[0] is None else int(row[0])
        return reach[key]

    def window_ok(market: str, interval: int, epoch: int, start: int) -> bool:
        step = interval * 60
        forming_open = epoch // step * step
        for w, w_end in archive_windows(interval, start, epoch + 1):
            state = states.get((market, interval, w))
            if state is None:
                return False
            status, fetched_at = state
            # Bars it must hold: all of an earlier window, up to the forming bar of the last.
            needed = min(w_end, forming_open + step)
            if status != "done" and fetched_at < needed + SETTLE_SECONDS:
                return False
        return True

    def covered(market: str, epoch: int) -> bool:
        key = (market, epoch)
        if key not in cache:
            ok = True
            for interval in INTERVALS:
                step = interval * 60
                start = first_open(market, interval, epoch // step * step - step)
                if start is None:
                    ok = False
                    break
                if interval == 1:
                    start = min(start, *(epoch // (i * 60) * (i * 60) for i in INTERVALS))
                if not window_ok(market, interval, epoch, start):
                    ok = False
                    break
            cache[key] = ok
        return cache[key]

    return covered


def run(
    bot_db: Path,
    archive_path: Path,
    *,
    obs_db: Path | None = None,
    spreads: SpreadModel | None = None,
    out: TextIO | None = None,
) -> FidelitySummary:
    """The fidelity check over copies of ``bot.db`` (and ``observations.db``), all read-only.

    ``spreads`` only satisfies the client: every quote comes from the stored inputs.
    """
    bot = open_readonly(bot_db)
    obs: sqlite3.Connection | None = None
    try:
        check_schema(bot)
        if obs_db is not None:
            obs = open_readonly(obs_db)
            check_observations_schema(obs)
        with Archive.open_readonly(archive_path) as archive:
            return check(
                bot,
                _ArchiveRows(archive),
                obs=obs,
                covered=archive_coverage(archive),
                spreads=spreads,
                progress=out,
            )
    finally:
        bot.close()
        if obs is not None:
            obs.close()


def write_report(summary: FidelitySummary, folder: Path, *, now: float | None = None) -> Path:
    """``fidelity-<UTC>.json`` in ``folder``; an existing file is never overwritten."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time() if now is None else now, UTC)
    base = f"fidelity-{stamp:%Y%m%dT%H%M%SZ}"
    for n in range(1, 1000):
        path = folder / (f"{base}.json" if n == 1 else f"{base}-{n}.json")
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(summary.to_json() + "\n")
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"too many reports named {base} in {folder}")


def causes_text(causes: Mapping[str, int]) -> str:
    """``{cause: n}`` as ``n (cause a, cause b)`` in German, largest first."""
    total = sum(causes.values())
    if not total:
        return "0"
    parts = sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))
    return f"{total} (" + ", ".join(f"{cause}: {n}" for cause, n in parts) + ")"


def summary_text(summary: FidelitySummary, *, limit: int = 20) -> str:
    """German summary for the console and the report; never "100 %"."""
    lines = ["Treue-Test gegen live (Spec 016 AK5)"]
    lines.append("mit Beobachtungen" if summary.with_observations else "ohne Beobachtungen")
    for market in sorted(summary.compared):
        n = summary.compared[market]
        ok, why = summary.verdict(market)
        exact = summary.exact.get(market, 0)
        cut = summary.truncated.get(market, 0)
        lines.append(f"{market}: {n} Vergleiche")
        lines.append(f"  Aktion und Ziel gleich: {summary.equal_action.get(market, 0)}")
        lines.append(
            f"  exakt gleich bei belegt gleichem Eingang: {exact}"
            + (f" (davon {cut} mit gekürztem Live-Fenster)" if cut else "")
        )
        lines.append(
            "  gleicher Eingang, ungerundete Indikatoren nicht aufgezeichnet: "
            f"{summary.no_raw.get(market, 0)}"
        )
        lines.append("  Eingang verschieden: " + causes_text(summary.different.get(market, {})))
        lines.append(f"  ohne Aufzeichnung nicht prüfbar: {summary.unrecorded.get(market, 0)}")
        lines.append(f"  Indikatoren gleich (Information): {summary.indicators_ok.get(market, 0)}")
        verdict = "Treue belegt" if ok else f"Treue nicht belegt ({why})"
        lines.append(f"  {market}: {verdict}")
    groups = (
        ("Ungeklärte Abweichungen", [d for d in summary.deviations if _unexplained(d)]),
        ("Erklärt durch die laufende Kerze", _by_cause(summary, FORMING)),
        ("Eingang verschieden (Ursache belegt)", _different(summary)),
        ("Ohne Aufzeichnung nicht prüfbar", _by_cause(summary, NOT_CHECKABLE)),
        ("Nur grob prüfbar (Prozentfelder)", _by_cause(summary, ROUGH)),
        ("Übersprungen", [d for d in summary.deviations if d.get("kind") == SKIPPED]),
    )
    for title, items in groups:
        lines.append(f"{title}: {len(items)}")
        for d in items[:limit]:
            lines.append(
                f"  {d['market']} {d['utc']} {d['agent']} {d['field']}: "
                f"live {d['live']!r}, Nachbau {d['rebuilt']!r}"
                + (f" [{d['cause']}]" if d.get("cause") else "")
            )
        if len(items) > limit:
            lines.append(f"  ... und {len(items) - limit} weitere (siehe JSON)")
    lines.append(summary.note)
    return "\n".join(lines) + "\n"


_DIFFERENT_CAUSES = frozenset(
    {LATE, OTHER_WINDOW, UNRECORDED_DATA}
    | {DIFFERENT_INPUT.format(c) for c in (LATE, OTHER_WINDOW, UNRECORDED)}
)


def _different(summary: FidelitySummary) -> list[dict[str, Any]]:
    return [d for d in summary.deviations if d.get("cause") in _DIFFERENT_CAUSES]


def _unexplained(d: Mapping[str, Any]) -> bool:
    return not d.get("cause")


def _by_cause(summary: FidelitySummary, cause: str) -> list[dict[str, Any]]:
    return [d for d in summary.deviations if d.get("cause") == cause]


def cli_main(command: str, argv: Sequence[str], out: TextIO) -> int:
    """``trading-bot lab fidelity --bot <copy> [--obs <copy>] [--dir <lab>]``.

    Exit code 0: fidelity established for every market; 1: not established; 2: an input could
    not be read. Only copies of the live databases, never the files of the running service
    (ADR-0026); they are opened read-only.
    """
    if command != "fidelity":
        out.write(f"unbekannter Befehl: {command}\n")
        return EXIT_ERROR
    parser = argparse.ArgumentParser(
        prog="trading-bot lab fidelity",
        description="Treue-Test: Live-Snapshots aus dem Kerzenarchiv nachbauen und vergleichen "
        "(nur Kopien der Live-Daten, schreibgeschützt geöffnet).",
    )
    parser.add_argument("--bot", type=Path, required=True, help="Kopie von bot.db")
    parser.add_argument("--obs", type=Path, default=None, help="Kopie von observations.db")
    parser.add_argument("--dir", type=Path, default=None, help="Labor-Ordner (candles.db)")
    args = parser.parse_args(list(argv))
    folder: Path = args.dir if args.dir is not None else lab_dir()
    try:
        summary = run(args.bot, folder / CANDLES_FILE, obs_db=args.obs, out=out)
    except (OSError, sqlite3.Error, SchemaError, ArchiveError, ValueError) as exc:
        out.write(f"Treue-Test nicht möglich: {type(exc).__name__}: {exc}\n")
        return EXIT_ERROR
    path = write_report(summary, folder / REPORTS_DIR)
    out.write(summary_text(summary))
    out.write(f"Bericht: {path}\n")
    ok = bool(summary.established) and all(summary.established.values())
    return EXIT_ESTABLISHED if ok else EXIT_NOT_ESTABLISHED


if __name__ == "__main__":  # pragma: no cover - manual use
    raise SystemExit(cli_main("fidelity", sys.argv[1:], sys.stdout))
