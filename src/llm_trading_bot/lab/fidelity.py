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

Costs come from the stored quote (``quote_bid``/``quote_ask``) and the stored fee/slippage
percentages, the weight from the stored ``current_weight`` (rounded to 4 places; for a classic
HOLD the stored target is the unrounded weight). The quotes are never rebuilt: there is no
bid/ask history.

Without observations the archive cannot tell whether live already had the forming bar in its
answer (``history.py``). A deviation that disappears when the forming bar of some intervals is
counted the other way is marked "laufende Kerze (Naeherung)" and counted as equal; any other
deviation keeps an empty cause (unexplained) and fails the check. Below ``FIDELITY_MIN``
comparisons per market fidelity is "nicht belegt" (``FidelitySummary.established``).

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
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, TextIO

from llm_trading_bot.lab.archive import SETTLE_SECONDS, Archive, ArchiveError, window_start
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
from llm_trading_bot.paper.cycle import STRATEGY_TIMEFRAME
from llm_trading_bot.paper.db import SchemaError, check_schema
from llm_trading_bot.paper.execution import REASON_MAX
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
        try:
            payload = json.loads(row["inputs"])
            if not isinstance(payload, dict) or "utc" not in payload:
                continue  # buy & hold: no snapshot behind its decision
            symbol = _symbol(str(row["symbol"]))
            if agent in STRATEGY_TIMEFRAME:
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


class Observations:
    """What Spec 015 recorded for a snapshot: the windows of its round and the decision's
    unrounded indicators. None where the round or decision was not recorded."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con

    def windows(self, symbol: Symbol, epoch: int) -> dict[int, ObservedWindow] | None:
        con = self._con
        # The round began before its snapshot (quotes first) and at most a few seconds earlier.
        round_row = con.execute(
            "SELECT id FROM rounds WHERE ts<=? AND ts>=? ORDER BY id DESC LIMIT 1",
            (_iso(epoch), _iso(epoch - 30)),
        ).fetchone()
        if round_row is None:
            return None
        round_id = int(round_row["id"])
        ok = con.execute(
            "SELECT 1 FROM snapshots WHERE round_id=? AND symbol=? AND status='ok'",
            (round_id, symbol.name),
        ).fetchone()
        if ok is None:
            return None
        out: dict[int, ObservedWindow] = {}
        for interval in INTERVALS:
            # Windows are written only when they change (changed_windows): the one a round used
            # is the latest row of its interval up to that round.
            row = con.execute(
                "SELECT rows, first_open, last_open, forming, rows_sha256 FROM windows"
                " WHERE symbol=? AND interval=? AND round_id<=? ORDER BY id DESC LIMIT 1",
                (symbol.name, interval, round_id),
            ).fetchone()
            if row is None:
                return None
            out[interval] = ObservedWindow(
                int(row["rows"]),
                row["first_open"],
                row["last_open"],
                bool(row["forming"]),
                str(row["rows_sha256"]),
            )
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
        self.client = HistoricalClient(
            _LeadSource(source, starts),
            spreads or ConstantSpreads(),
            # Only the client's own quotes read this clock; every quote here is the stored one.
            lambda: float(first),
            start=min(starts.values()),
            end=last + 1,
            forming_hint=lambda _symbol, interval, _now: self._hint.get(interval),
        )
        # The depots of one round share its snapshot; a few recent rebuilds suffice, and a
        # bounded cache keeps months of snapshots (500 bars x 4 intervals each) out of memory.
        self._cache: OrderedDict[tuple[object, ...], Rebuilt] = OrderedDict()

    def lead_ok(self, symbol: Symbol, epoch: int) -> bool:
        """Live always got CANDLE_LIMIT rows; fewer in the archive cannot be rebuilt."""
        return all(self.client.rows_before(symbol, i, epoch + 1) >= CANDLE_LIMIT for i in INTERVALS)

    def rebuild(self, rec: Recorded, hint: Hint) -> Rebuilt:
        p = rec.payload
        quote = Quote(float(p["quote_bid"]), float(p["quote_ask"]))
        fee = float(p["taker_fee_per_side_pct"]) / 100
        slip = float(p["slippage_per_side_pct"]) / 100
        key = (rec.symbol, rec.epoch, quote, fee, slip, tuple(sorted(hint.items())))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        self._hint = hint
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
            ind = indicators(snap, cost_roundtrip(snap.quote.spread_bps, fee, slip))
            result = Rebuilt(snap, "", windows, ind)
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
        own = snap.bars[STRATEGY_TIMEFRAME[rec.agent]][-1].ts
        if own != rec.bar_ts:
            add(DATA, "bar_ts", rec.bar_ts, own)
    last_close = snap.bars[1][-1].close
    if p.get("latest_completed_1m_close") != last_close:
        add(DATA, "latest_completed_1m_close", p.get("latest_completed_1m_close"), last_close)
    filled = sum(b.volume == 0 for b in snap.bars[1][-60:])
    if p.get(_FILLED) != filled:
        add(DATA, _FILLED, p.get(_FILLED), filled)

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

    if rec.source == "decision" and rec.status != "STALE_QUOTE":
        sig = run_strategy(rec.agent, snap, ind, rec.weight)
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
        reason = sig.reason.strip()[:REASON_MAX] if rec.status == "OK" else sig.reason
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


def _alternatives(got: Rebuilt) -> Iterator[dict[int, bool | None]]:
    """Forming-bar counts to try, fewest flips first: only intervals whose forming bar the
    default rebuild counted (live may not have had a trade in it yet)."""
    flippable = [tf for tf, w in sorted(got.windows.items()) if w.forming]
    for size in range(1, len(flippable) + 1):
        for chosen in combinations(flippable, size):
            yield dict.fromkeys(chosen, False)


def _explain(
    rec: Recorded,
    rebuilder: Rebuilder,
    got: Rebuilt,
    raw: Mapping[str, Any] | None,
    found: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Without observations: the forming-bar count that leaves the fewest deviations, if it
    leaves only some of ``found``. Those it removes get the cause FORMING (in place); the rest
    stay unexplained. Returns that rebuild's deviations, or None if no count helps."""
    failing = {d["field"]: d for d in _failing(found)}
    best: tuple[dict[int, bool | None], list[dict[str, Any]]] | None = None
    for alt in _alternatives(got):
        again = compare(rec, rebuilder.rebuild(rec, alt), None, raw)
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


@dataclass
class _Tally:
    summary: FidelitySummary
    skipped: int = 0
    explained: int = 0
    unobserved: int = 0
    """With observations: snapshots whose round was not recorded (compared as without)."""

    def count(self, market: str, equal: bool, ind_ok: bool) -> None:
        s = self.summary
        s.compared[market] = s.compared.get(market, 0) + 1
        s.equal_action[market] = s.equal_action.get(market, 0) + int(equal)
        s.indicators_ok[market] = s.indicators_ok.get(market, 0) + int(ind_ok)


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
        summary.indicators_ok[market] = 0
    summary.deviations.extend(unreadable)
    tally.skipped += len(unreadable)
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
        windows = None if observations is None else observations.windows(rec.symbol, rec.epoch)
        raw = None
        if observations is not None and rec.meta is not None:
            raw = observations.indicators(rec.symbol, rec.meta)
        hint: dict[int, bool | None] = {}
        if observations is not None and windows is None:
            tally.unobserved += 1
        if windows is not None:
            hint = {tf: w.forming for tf, w in windows.items()}
        got = rebuilder.rebuild(rec, hint)
        found = compare(rec, got, windows, raw)
        equal, ind_ok = _counts(found)
        if _failing(found) and windows is None:
            better = _explain(rec, rebuilder, got, raw, found)
            if better is not None:
                equal, ind_ok = _counts(better)
                tally.explained += not _failing(better)
        for d in found:
            if d["kind"] == ROUGH_KIND:
                d["cause"] = ROUGH
        summary.deviations.extend(found)
        tally.count(rec.market, equal, ind_ok)
    summary.note = _note(tally, obs is not None)
    return summary


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
            "laut Aufzeichnung."
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
            f"{tally.explained} Vergleiche nur damit erklärt."
        )
    if tally.skipped:
        parts.append(f"{tally.skipped} Snapshots übersprungen (nicht verglichen).")
    return " ".join(parts)


# ---------------------------------------------------------------- the archive and the files


def archive_coverage(archive: Archive) -> Callable[[str, int], bool]:
    """True if the archive window holding the bar that was forming at ``epoch`` is complete,
    or was fetched after that bar had closed and settled, for every interval."""
    cache: dict[tuple[str, int], bool] = {}

    def covered(market: str, epoch: int) -> bool:
        key = (market, epoch)
        if key not in cache:
            ok = True
            for interval in INTERVALS:
                step = interval * 60
                forming_open = epoch // step * step
                state = archive.window_state(market, interval, window_start(interval, forming_open))
                if state is None or (
                    state.status != "done"
                    and state.fetched_at < forming_open + step + SETTLE_SECONDS
                ):
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


def summary_text(summary: FidelitySummary, *, limit: int = 20) -> str:
    """German summary for the console and the report; never "100 %"."""
    lines = ["Treue-Test gegen live (Spec 016 AK5)"]
    lines.append("mit Beobachtungen" if summary.with_observations else "ohne Beobachtungen")
    established = summary.established
    for market in sorted(summary.compared):
        n = summary.compared[market]
        verdict = "Treue belegt" if established.get(market) else "Treue nicht belegt"
        why = ""
        if not established.get(market):
            if n < FIDELITY_MIN:
                why = f" (unter {FIDELITY_MIN} Vergleichen)"
            elif summary.equal_action.get(market, 0) != n:
                why = " (Aktion oder Ziel abweichend)"
            else:
                why = " (ungeklärte Abweichungen)"
        lines.append(
            f"{market}: {n} Vergleiche, Aktion und Ziel gleich in "
            f"{summary.equal_action.get(market, 0)}, Indikatoren gleich in "
            f"{summary.indicators_ok.get(market, 0)} - {verdict}{why}"
        )
    groups = (
        ("Ungeklärte Abweichungen", [d for d in summary.deviations if _unexplained(d)]),
        ("Erklärt durch die laufende Kerze", _by_cause(summary, FORMING)),
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
