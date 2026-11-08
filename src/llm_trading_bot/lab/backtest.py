"""Backtest of the strategy lab (Spec 016 AK4, AK6 cost grid, AK9, ADR-0026).

Each quarter is its own section with fresh capital: the UNCHANGED live ``paper_cycle`` runs once
per simulated minute at ``start + k*60 + 5`` s (ADR-0017) over the candle archive, with the four
classic depots and buy & hold, no GPT, ``Controls()`` without pause and an in-memory bot DB. The
historical client (``lab.history``) answers like Bitvavo would have at the simulated clock.

Process model: ``run_quarter`` is a top-level function, so a ``ProcessPoolExecutor`` can run
quarters in parallel. A worker opens the archive read-only and only returns its
``QuarterResult``; the main process records every result in the trial register and writes
nothing else (``run_quarters``). Workers finish in any order, but the register (and the
progress) gets the results in a fixed order - variant by variant, quarters in time order - so
the JSONL chain of the same run is the same every time; a crashed quarter is recorded as
aborted in its own place of that order.

Variants: ``cost-0.6`` (``cost_grid``) has exactly the live costs, so it is the same
configuration as ``base``. The CLI selections never run it twice: ``base`` is the 0.6 % point
of the cost grid (``variants``, ``skipped_as_base``); a second, renamed run would cost time and
count as one more trial in the register without being one.

What a result means (the conventions ``lab.metrics`` and the report rely on):

- **Sealed:** a quarter that reaches into the period from 2025-10-01 (``Quarter.sealed``) is
  refused before any row is read (status ``sealed``). Candidates of Spec 018 come later.
- **Data lead:** at the quarter start every market and interval needs ``LEAD_ROWS`` archived
  rows (status ``no_lead`` otherwise), so the first round already sees the full live window.
  The preload holds exactly those rows plus the quarter's own rows: counted in rows, not in
  intervals, because Bitvavo omits intervals without trades and the live window of 500 rows
  then reaches further back. An archive whose windows over that range are not all ``done``
  (still being fetched) gives status ``incomplete``: missing rows would look like exchange gaps.
- **Quality per market** (``MarketQuality``):
  - ``due``: bars of each classic depot's own timeframe (``STRATEGY_TIMEFRAME``) that close in
    ``[start, end)``, summed over the depots - a bar closing at ``end`` is decided in the next
    quarter's first round. For a depot stopped by the drawdown stop only bars closing before
    the stop's round count: a stopped depot decides nothing by design (ADR-0021).
  - ``decided``: decisions stored for exactly those bars with status ``OK``.
  - ``rounds``: rounds in which the cycle reported the market's snapshot outcome (every round,
    ``ok`` or a failure code): the base of the ``no_decision`` shares, which count rounds, not
    bars - ``due`` counts bars and must never be their denominator.
  - ``no_decision``: rounds in which the market got no snapshot, by the code the live cycle
    gives the failure (``gap_1m``, ``stale``, ``quote_missing`` ...), read through the
    observation hook of ``paper_cycle`` (Spec 015), which never changes what is traded.
    The codes stay exactly the live ones, also where the backtest knows more: a round that
    fails because the newest 15m/1h/4h bar is missing is counted as ``stale``, like a stale
    quote or 1m bar, because that is what the live cycle reports.
  - ``blocked_rounds``: the rounds a higher interval alone blocked. The snapshot checks the
    quote first and then each interval in the order 1m, 15m, 1h, 4h (count, order, gaps,
    freshness), so the first failing check names the cause. Blocked are the codes ``gap_15m``,
    ``gap_60m`` and ``gap_240m`` and the ``stale`` rounds whose failing check was the freshness
    of a 15m, 1h or 4h bar (read from the live message, ``stale_interval``): in both cases the
    quote and the 1m bars were fine. One missing 1h bar therefore blocks first as ``stale``
    (while its slot would be the newest closed bar, about one hour minus the live freshness
    slack) and then as ``gap_60m`` (while the hole is among the newest 60 bars).
    ``blocked_rounds`` minus the ``gap_*`` counts of the higher intervals is the part of
    ``stale`` that came from them; the rest of ``stale`` is quote or 1m staleness.
- **Depots:** one ``DayPoint`` per UTC day: value and cash of the depot's last equity point
  that day (valued at mid); ``weights`` the time-weighted mean exposure over that day's valued
  rounds (each round's ``qty * mid / value`` with the mid of that round, summed and divided by
  the depot's valued rounds of the day). Not the last round: a 15m strategy that holds for
  hours and is flat at midnight was invested, and the benchmark (AK7) must see that quote.
  ``end_mid`` is the last equity value; ``end_liquidated`` sells every holding at
  ``bid * (1 - slippage)`` minus the fee at the last round. ``halted_at`` is the round of the
  drawdown stop; the depot is cash afterwards, and only in this quarter, because the next
  quarter starts with fresh capital.
- **Trade context** (market-phase cells, AK7), from the decision's stored ``inputs``
  (``strategy.payload.feature_payload``), so only facts known before the trade:
  ``regime`` = ``indicators.regime`` (TREND/RANGE/TRANSITION from the closed 1h ADX),
  ``vol`` = ``indicators.atr14_1h_pct`` (ATR(14) of the closed 1h bars over the last 1h close,
  a fraction, rounded to 5 decimals as stored), ``direction`` = ``returns_pct["1440"]`` (the
  24h return in percent: quote mid at the decision against the close 24 closed 1h bars back).
  Trades without such a decision (the buy & hold purchase, the drawdown stop's sales) get an
  empty context.
- **daily_mid** per market: first the mid of the market's first quoted round, labelled with the
  previous day (the base of day 1), then each day's mid at its last quoted round.
- ``dataset_sha256``: sha256 over ``Archive.checksum`` of every preloaded market and interval
  range, so a repeat on other data is a different trial (AK8).

Execution over USDC (AK14, from Spec 020 AK8): the variant ``usdc`` runs the four USDC depots
(``trend-usdc`` ...) instead of v0.3.1, through the same unchanged cycle with an explicit
``BotConfig`` (never the ``BOT_USDC_AGENTS`` default); buy & hold runs along as live always
does, ``buyhold-usdc`` with it. Signals come from the same BTC-EUR/ETH-EUR archive, the BTC is
traded over the synthetic route quotes of ``history.UsdcRoute`` (BTC-USDC only exists from
2024-07-01). Its own trial in the register, with version ``v0.3.1-usdc``. Every other variant
runs exactly the v0.3.1 depots and buy & hold, with ``usdc=()`` written out.

- A routed BTC trade is stored live under BTC-USDC; the lab books it under BTC-EUR, the
  market it was decided on, so FIFO, cells and benchmark line up with the base run. Its
  costs (``Variant.side_cost``) and ``spread["BTC-EUR"]`` are those of the route.
- Weights, day values and ``end_liquidated`` value and sell that BTC over the route
  (``paper.usdc.btc_mid``/``sell``), like the live depot.

Diagnostic run (AK9): a variant with ``gap_window`` validates the higher intervals (15m, 1h, 4h)
with that gap tolerance instead of the live ``GAP_WINDOW``; the 1m bars keep the live rule. The
module global is read at call time, so it is set only while one of those intervals is checked,
only inside the process that runs the quarter, and restored afterwards. Never a strategy
version; the register marks the run as diagnostic.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

from llm_trading_bot.lab import archive as archive_mod
from llm_trading_bot.lab.archive import (
    Archive,
    ArchiveError,
    LabArgumentParser,
    add_dir_option,
    parse_cli,
)
from llm_trading_bot.lab.gitstate import git_runner
from llm_trading_bot.lab.history import CandleRow, HistoricalClient, MemorySource, UsdcRoute
from llm_trading_bot.lab.model import (
    CLASSIC,
    FIRST_GRADED,
    LEAD_ROWS,
    MARKETS,
    SEAL_EPOCH,
    DayPoint,
    DepotResult,
    MarketQuality,
    Quarter,
    QuarterResult,
    Trade,
    Variant,
)
from llm_trading_bot.lab.paths import CANDLES_FILE, CHAIN_FILE, RUNS_FILE, lab_dir
from llm_trading_bot.lab.register import Register, RegisterError
from llm_trading_bot.lab.spread import QuarterSpreads, stored_quarter_spreads
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.market import snapshot
from llm_trading_bot.market.models import QUOTED, SYMBOLS, Quote, Symbol
from llm_trading_bot.market.snapshot import INTERVALS, MarketSnapshot
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.cycle import STRATEGY_TIMEFRAME, paper_cycle
from llm_trading_bot.paper.db import SchemaError, open_db
from llm_trading_bot.paper.execution import Costs, fresh_quotes
from llm_trading_bot.paper.observations import ObservationBuffer
from llm_trading_bot.paper.report import CycleReport, Equity
from llm_trading_bot.paper.risk import Controls

OFFSET = 5
"""Seconds into each minute a round runs, like the service (ADR-0017)."""
MINUTE = 60
DAY = 86_400
HIGHER_INTERVALS = frozenset(tf for tf in INTERVALS if tf != 1)
BLOCKING_CODES = frozenset(f"gap_{tf}m" for tf in HIGHER_INTERVALS)
"""Snapshot failures that come only from a gap in a higher interval (see module doc)."""
_STALE_BARS = re.compile(r"\b(\d+)m data stale or in the future\b")
"""The live freshness message of one interval (``MarketSnapshot._validate_bars``); a test
builds it through the live code, so a changed wording fails there instead of undercounting."""
LIVE_RATIO = (5, 1)
"""Fee : slippage per side in the live defaults (0.25 % : 0.05 %); the cost grid keeps it."""
COST_GRID = (0.003, 0.006, 0.009, 0.012)
"""Round trips (fee and slippage, buy plus sell) of the full reruns (AK6)."""
DIAGNOSTIC_GAP_WINDOW = 4
"""Gap tolerance of the diagnostic run: only the newest 4 bars of 15m, 1h and 4h must be gap-free
(3 steps). Short enough that a single missing bar no longer blocks for days (60 bars of 1h =
60 h, of 4h = 10 days), long enough that the newest bars every signal looks at are contiguous."""
VARIANT_SETS = ("base", "cost-grid", "diagnostic", "usdc", "all")
EXIT_OK = 0
"""At least one quarter was graded and none aborted."""
EXIT_ABORTED = 1
"""A quarter aborted, or no quarter was graded: the run gives the report nothing to show."""
EXIT_NOT_GRADED = EXIT_ABORTED
EXIT_REFUSED = 2
"""Nothing ran: sealed span, bad input or no archive."""

BASE = Variant()
USDC = Variant(name="usdc", usdc=True)
"""AK14: the USDC depots at the live costs (ETH-EUR as base, BTC over the route)."""


def cost_variant(roundtrip: float) -> Variant:
    """A full rerun at another model cost, fee and slippage in the live ratio, e.g. cost-0.6."""
    fee_part, slip_part = LIVE_RATIO
    per_side = roundtrip / 2
    total = fee_part + slip_part
    # Rounded so cost-0.6 is bit-identical to the live 0.0025 / 0.0005.
    fee = round(per_side * fee_part / total, 12)
    slip = round(per_side * slip_part / total, 12)
    return Variant(name=f"cost-{roundtrip * 100:.1f}", fee_rate=fee, slippage=slip)


def cost_grid() -> list[Variant]:
    """The whole grid of AK6, including ``cost-0.6``, which equals ``base`` (``variants``)."""
    return [cost_variant(k) for k in COST_GRID]


def same_as_base(variant: Variant) -> bool:
    """True if ``variant`` runs exactly what ``base`` runs, whatever its name."""
    return replace(variant, name=BASE.name) == BASE


def diagnostic_variant(window: int = DIAGNOSTIC_GAP_WINDOW) -> Variant:
    return Variant(name=f"diagnostic-gap-{window}", gap_window=window, diagnostic=True)


def _selection(selection: str) -> list[Variant]:
    if selection == "base":
        return [BASE]
    if selection == "cost-grid":
        return cost_grid()
    if selection == "diagnostic":
        return [diagnostic_variant()]
    if selection == "usdc":
        return [USDC]
    if selection == "all":
        return [BASE, *cost_grid(), diagnostic_variant(), USDC]
    raise ValueError(f"unknown variant set {selection!r}, expected one of {VARIANT_SETS}")


def variants(selection: str) -> list[Variant]:
    """The variants of a CLI selection (``VARIANT_SETS``), without any that only repeats
    ``base`` under another name (``skipped_as_base``): base is the grid's 0.6 % point."""
    return [v for v in _selection(selection) if v is BASE or not same_as_base(v)]


def skipped_as_base(selection: str) -> list[str]:
    """Names ``variants`` leaves out because they equal ``base`` (the CLI says so)."""
    return [v.name for v in _selection(selection) if v is not BASE and same_as_base(v)]


# ---------------------------------------------------------------- one quarter (worker side)


class _SimClock:
    """The simulated round time: constant within a round, like a round that takes no time."""

    __slots__ = ("now",)

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _FailureCodes(ObservationBuffer):
    """Keeps only each market's snapshot outcome of a round; every other observation is
    dropped, so observing costs next to nothing (the cycle's observing never changes trading,
    Spec 015 AK4)."""

    def __init__(self) -> None:
        super().__init__()
        self.codes: dict[str, str] = {}
        self.blocked: set[str] = set()

    def start(self) -> None:
        self.codes = {}
        self.blocked = set()

    def book(self, purpose: str, at: float, book: Mapping[Symbol, Quote]) -> None:
        return None

    def window(self, symbol: Symbol, interval: int, at: float, candles: Any) -> None:
        return None

    def decision(self, *args: Any, **kwargs: Any) -> None:
        return None

    def llm_attempt(self, *args: Any, **kwargs: Any) -> None:
        return None

    def snapshot(self, symbol: Symbol, status: str, message: str | None, decided: bool) -> None:
        self.codes[symbol.market] = status
        if blocked_by_higher_interval(status, message):
            self.blocked.add(symbol.market)


def stale_interval(message: str | None) -> int | None:
    """The interval (minutes) whose newest bar failed the live freshness check, read from the
    snapshot's failure message; None if the stale check was the quote's (age, clock drift)."""
    found = _STALE_BARS.search(message or "")
    return int(found.group(1)) if found else None


def blocked_by_higher_interval(code: str, message: str | None) -> bool:
    """A failure only a 15m/1h/4h bar caused (``blocked_rounds``, module doc)."""
    if code in BLOCKING_CODES:
        return True
    return code == "stale" and stale_interval(message) in HIGHER_INTERVALS


@contextmanager
def gap_tolerance(window: int | None) -> Iterator[None]:
    """Validate 15m, 1h and 4h with ``window`` as ``GAP_WINDOW`` (AK9); 1m keeps the live value.

    ``MarketSnapshot._validate_bars`` reads the module global at call time, so the value is set
    just around each higher-interval check. Only for the process running the diagnostic quarter;
    everything is restored on exit, also on errors.
    """
    if window is None:
        yield
        return
    if window < 1:
        raise ValueError(f"gap window must be at least 1: {window}")
    live = snapshot.GAP_WINDOW
    original = MarketSnapshot._validate_bars

    def validate_bars(self: MarketSnapshot, tf: int, seq: tuple[Any, ...], minimum: int) -> None:
        snapshot.GAP_WINDOW = live if tf == 1 else window
        try:
            original(self, tf, seq, minimum)
        finally:
            snapshot.GAP_WINDOW = live

    MarketSnapshot._validate_bars = validate_bars  # type: ignore[method-assign]
    try:
        yield
    finally:
        MarketSnapshot._validate_bars = original  # type: ignore[method-assign]
        snapshot.GAP_WINDOW = live


def _day(epoch: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def _epoch(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp())


def _bar_opens(step: int, start: int, end: int, before: int | None = None) -> tuple[int, int]:
    """Open times ``[lo, hi)`` of the bars closing in ``[start, end)``; with ``before`` only
    those closing before it (see ``MarketQuality.due`` in the module doc)."""
    first_close = -(-start // step) * step
    lo = first_close - step
    last = end if before is None else min(end, before)
    hi = -(-last // step) * step - step
    return lo, max(lo, hi)


def _context(inputs: str | None) -> dict[str, Any]:
    """Past-only facts of the decision behind a trade (module doc); {} without one."""
    if not inputs:
        return {}
    data = json.loads(inputs)
    ind = data.get("indicators")
    if not isinstance(ind, dict):
        return {}
    out: dict[str, Any] = {}
    if isinstance(ind.get("regime"), str):
        out["regime"] = ind["regime"]
    vol = ind.get("atr14_1h_pct")
    if isinstance(vol, int | float) and not isinstance(vol, bool):
        out["vol"] = float(vol)
    returns = data.get("returns_pct")
    if isinstance(returns, dict):
        direction = returns.get("1440")
        if isinstance(direction, int | float) and not isinstance(direction, bool):
            out["direction"] = float(direction)
    return out


@dataclass
class _Preload:
    source: MemorySource = field(default_factory=MemorySource)
    first_open: int = 0
    ranges: list[tuple[str, int, int]] = field(default_factory=list)


def _lead_problems(arch: Archive, quarter: Quarter) -> list[str]:
    problems = []
    for market in MARKETS:
        for interval in INTERVALS:
            n = arch.count_before(market, interval, quarter.start)
            if n < LEAD_ROWS:
                problems.append(f"{market} {interval}m: {n} von {LEAD_ROWS} Kerzen")
    return problems


def _preload(arch: Archive, quarter: Quarter) -> tuple[_Preload, list[str]]:
    """The lead rows plus the quarter's rows of every series; problems if a window over that
    range is not complete in the archive."""
    pre = _Preload()
    firsts = []
    problems = []
    for market in MARKETS:
        for interval in INTERVALS:
            lead = arch.last_rows(market, interval, quarter.start, LEAD_ROWS)
            first = int(str(lead[0][0])) // 1000
            rows: list[CandleRow] = [
                *lead,
                *arch.rows(market, interval, quarter.start, quarter.end),
            ]
            pre.source.add(market, interval, rows)
            pre.ranges.append((market, interval, first))
            firsts.append(first)
            for w_start, _ in archive_mod.windows(interval, first, quarter.end):
                state = arch.window_state(market, interval, w_start)
                if state is None or state.status != "done":
                    problems.append(
                        f"{market} {interval}m: ab {_day(w_start)} noch nicht fertig geladen"
                    )
                    break
    pre.first_open = min(firsts)
    return pre, problems


def _dataset_sha256(arch: Archive, pre: _Preload, end: int) -> str:
    digest = hashlib.sha256()
    for market, interval, first in pre.ranges:
        line = [market, interval, first, end, arch.checksum(market, interval, first, end)]
        digest.update(json.dumps(line, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def holding_mid(agent: str, symbol: Symbol, qs: Mapping[Symbol, Quote]) -> float | None:
    """EUR per unit as the live depot values it: the route's mid for the BTC of a USDC depot
    (``paper.usdc.btc_mid``), else the market's own; None without the quotes it needs."""
    if any(m not in qs for m in usdc.route_markets(agent, symbol)):
        return None
    return usdc.btc_mid(qs) if usdc.routed(agent, symbol) else qs[symbol].mid


def round_weights(eq: Equity, qs: Mapping[Symbol, Quote]) -> dict[str, float]:
    """Market -> share of the depot value held in it in one round, at that round's mid."""
    weights = {}
    for symbol, qty in ((Symbol.BTCEUR, eq.btc), (Symbol.ETHEUR, eq.eth)):
        mid = holding_mid(eq.agent, symbol, qs)
        worth = qty * mid if mid is not None and qty else 0.0
        weights[symbol.market] = worth / eq.value if eq.value > 0 else 0.0
    return weights


@dataclass
class _Exposure:
    """Sum of one depot's per-round weights over the valued rounds of the current day."""

    sums: dict[str, float] = field(default_factory=dict)
    rounds: int = 0

    def add(self, weights: Mapping[str, float]) -> None:
        for market, w in weights.items():
            self.sums[market] = self.sums.get(market, 0.0) + w
        self.rounds += 1

    def mean(self) -> dict[str, float]:
        return {m: s / self.rounds for m, s in self.sums.items()} if self.rounds else {}


@dataclass
class _Tracker:
    """Per-round bookkeeping of the main loop: day points, daily mids, stops, failure codes."""

    costs: Costs
    days: dict[str, list[DayPoint]] = field(default_factory=dict)
    pending: dict[str, tuple[int, Equity, dict[Symbol, Quote]]] = field(default_factory=dict)
    exposure: dict[str, _Exposure] = field(default_factory=dict)
    """Per depot: the weights of the day not yet emitted (``DayPoint.weights``)."""
    halted_at: dict[str, int] = field(default_factory=dict)
    mids: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    mid_day: dict[str, tuple[int, float]] = field(default_factory=dict)
    observed: dict[str, int] = field(default_factory=dict)
    """Per market: rounds with a snapshot outcome (``MarketQuality.rounds``)."""
    no_decision: dict[str, dict[str, int]] = field(default_factory=dict)
    blocked: dict[str, int] = field(default_factory=dict)

    def round(
        self,
        t: int,
        report: CycleReport,
        codes: Mapping[str, str],
        blocked: set[str],
        qs: dict[Symbol, Quote],
    ) -> None:
        day = t // DAY
        for market, code in codes.items():
            self.observed[market] = self.observed.get(market, 0) + 1
            if code != "ok":
                per = self.no_decision.setdefault(market, {})
                per[code] = per.get(code, 0) + 1
                if market in blocked:
                    self.blocked[market] = self.blocked.get(market, 0) + 1
        for stop in report.risk_stops:
            self.halted_at.setdefault(stop.agent, t)
        for eq in report.equity:
            old = self.pending.get(eq.agent)
            if old is not None and old[0] != day:
                self._emit(eq.agent, old)
            self.pending[eq.agent] = (day, eq, qs)
            self.exposure.setdefault(eq.agent, _Exposure()).add(round_weights(eq, qs))
        for symbol, quote in qs.items():
            if symbol not in SYMBOLS:
                continue  # the route's quote-only markets have no benchmark of their own
            market = symbol.market
            mid = quote.mid
            seen = self.mid_day.get(market)
            if seen is None:
                # The base of day 1 (metrics: the first entry only anchors the first return).
                self.mids.setdefault(market, []).append((_day((day - 1) * DAY), mid))
            elif seen[0] != day:
                self.mids[market].append((_day(seen[0] * DAY), seen[1]))
            self.mid_day[market] = (day, mid)

    def _emit(self, agent: str, item: tuple[int, Equity, dict[Symbol, Quote]]) -> None:
        day, eq, _ = item
        # The day's mean exposure (module doc), then a fresh sum for the next day.
        weights = self.exposure.pop(agent, _Exposure()).mean()
        self.days.setdefault(agent, []).append(
            DayPoint(_day(day * DAY), eq.value, eq.cash, weights)
        )

    def finish(self) -> None:
        for agent, item in sorted(self.pending.items()):
            self._emit(agent, item)
        for market, (day, mid) in sorted(self.mid_day.items()):
            self.mids[market].append((_day(day * DAY), mid))

    def end_values(self, agent: str) -> tuple[float, float]:
        """(at mid, sold at bid with slippage and fee) at the agent's last equity point."""
        item = self.pending.get(agent)
        if item is None:
            return 0.0, 0.0
        _, eq, qs = item
        sold = eq.cash
        for symbol, qty in ((Symbol.BTCEUR, eq.btc), (Symbol.ETHEUR, eq.eth)):
            if qty > 0 and usdc.routed(agent, symbol):
                # Over the route at both bids with both legs' fees, as the live depot sells.
                fill = usdc.sell(qty, qs)
                sold += fill.notional - fill.fee
            elif qty > 0:
                gross = qty * qs[symbol].bid * (1 - self.costs.slippage)
                sold += gross - gross * self.costs.fee_rate
        return float(eq.value), float(sold)


def _trades(con: sqlite3.Connection) -> dict[str, list[Trade]]:
    out: dict[str, list[Trade]] = {}
    for r in con.execute(
        "SELECT t.ts, t.agent, t.symbol, t.side, t.qty, t.fill, t.notional, t.fee, d.inputs"
        " FROM trades t LEFT JOIN decisions d ON d.id = t.decision_id ORDER BY t.id"
    ):
        symbol = Symbol[r["symbol"]]
        out.setdefault(r["agent"], []).append(
            Trade(
                ts=_epoch(r["ts"]),
                agent=r["agent"],
                # The decided market (module doc): a routed BTC trade is stored as BTC-USDC.
                market=Symbol.BTCEUR.market if symbol is Symbol.BTCUSDC else symbol.market,
                side=r["side"],
                qty=float(r["qty"]),
                fill=float(r["fill"]),
                notional=float(r["notional"]),
                fee=float(r["fee"]),
                context=_context(r["inputs"]),
            )
        )
    return out


def _actions(con: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in con.execute(
        "SELECT agent, action, status, COUNT(*) AS n FROM decisions"
        " GROUP BY agent, action, status ORDER BY agent, action, status"
    ):
        out.setdefault(r["agent"], {})[f"{r['action']}/{r['status']}"] = int(r["n"])
    return out


def _quality(
    con: sqlite3.Connection,
    quarter: Quarter,
    track: _Tracker,
    strategies: Sequence[str] = CLASSIC,
) -> dict[str, MarketQuality]:
    out = {}
    for symbol in SYMBOLS:
        market = symbol.market
        due = decided = 0
        for agent in strategies:
            step = STRATEGY_TIMEFRAME[usdc.strategy_of(agent)] * MINUTE
            halted = track.halted_at.get(agent)
            # A stop in round t: bars decided in earlier rounds closed at or before t - 60.
            before = None if halted is None else halted - OFFSET
            lo, hi = _bar_opens(step, quarter.start, quarter.end, before)
            due += (hi - lo) // step
            decided += con.execute(
                "SELECT COUNT(*) FROM decisions WHERE agent=? AND symbol=? AND status='OK'"
                " AND bar_ts>=? AND bar_ts<?",
                (agent, symbol.name, lo, hi),
            ).fetchone()[0]
        out[market] = MarketQuality(
            market,
            due=due,
            decided=decided,
            no_decision=dict(sorted(track.no_decision.get(market, {}).items())),
            blocked_rounds=track.blocked.get(market, 0),
            rounds=track.observed.get(market, 0),
        )
    return out


def run_quarter(
    archive_path: Path,
    quarter: Quarter,
    variant: Variant,
    spreads: Mapping[tuple[str, str], float] | None = None,
) -> QuarterResult:
    """One quarter through the unchanged live cycle (module doc). Writes nothing anywhere.

    ``quarter`` may be any span (tests use a few hours); rounds run at ``start + k*60 + 5`` for
    every time before ``end``. Raises on a bug; the orchestrator records that as aborted.
    """
    if quarter.sealed:
        # No number at all, not even the assumed spread: the register refuses a sealed entry
        # with any (ADR-0026), and the refusal must still be recorded.
        return QuarterResult(
            quarter,
            variant,
            status="sealed",
            error="reicht in den versiegelten Prüfzeitraum ab 2025-10-01 (ADR-0026)",
        )
    began = time.perf_counter()
    model = QuarterSpreads(spreads or {})
    route = UsdcRoute() if variant.usdc else None
    spread = {m: model.spread(m, quarter.start) for m in MARKETS}
    if route is not None:
        # What a BTC trade pays in this variant (QuarterResult.spread): the route's books.
        spread[Symbol.BTCEUR.market] = route.spread
    result = QuarterResult(quarter, variant, spread=spread)
    if quarter.start % MINUTE or quarter.end <= quarter.start:
        raise ValueError(f"quarter must start on a minute and not be empty: {quarter}")
    with Archive.open_readonly(archive_path) as arch:
        if problems := _lead_problems(arch, quarter):
            result.status, result.error = "no_lead", "; ".join(problems)
            return result
        pre, problems = _preload(arch, quarter)
        result.dataset_sha256 = _dataset_sha256(arch, pre, quarter.end)
    if problems:
        result.status, result.error = "incomplete", "; ".join(problems)
        return result
    clock = _SimClock(quarter.start + OFFSET)
    client = HistoricalClient(
        pre.source, model, clock, start=pre.first_open, end=quarter.end, route=route
    )
    costs = Costs(variant.fee_rate, variant.slippage)
    # Written out, never the BOT_USDC_AGENTS default (module doc).
    if variant.usdc:
        config = BotConfig(classic=(), llm=None, usdc=variant.strategies)
    else:
        config = BotConfig(classic=CLASSIC, llm=None, usdc=())
    depots = variant.depots
    if set(config.depots) != set(depots):
        raise ValueError(f"variant {variant.name}: cycle runs {config.depots}, not {depots}")
    stored = QUOTED if variant.usdc else SYMBOLS
    controls = Controls()
    codes = _FailureCodes()
    track = _Tracker(costs)
    con = open_db(Path(":memory:"), agents=depots)
    try:
        with gap_tolerance(variant.gap_window):
            for t in range(quarter.start + OFFSET, quarter.end, MINUTE):
                clock.now = t
                codes.start()
                report = paper_cycle(
                    con,
                    client,
                    config,
                    costs=costs,
                    clock=clock,
                    controls=controls,
                    observations=codes,
                )
                quotes = fresh_quotes(con, now_epoch=t, markets=stored)
                track.round(t, report, codes.codes, codes.blocked, quotes)
                result.rounds += 1
        track.finish()
        trades = _trades(con)
        actions = _actions(con)
        result.quality = _quality(con, quarter, track, variant.strategies)
    finally:
        con.close()
    for agent in depots:
        end_mid, end_sold = track.end_values(agent)
        result.depots[agent] = DepotResult(
            agent,
            days=track.days.get(agent, []),
            trades=trades.get(agent, []),
            halted_at=track.halted_at.get(agent),
            end_mid=end_mid,
            end_liquidated=end_sold,
            actions=actions.get(agent, {}),
        )
    result.daily_mid = {m: track.mids.get(m, []) for m in MARKETS}
    result.seconds = time.perf_counter() - began
    return result


# ---------------------------------------------------------------- orchestration (main side)


class Recorder(Protocol):
    """What the orchestrator needs of the trial register (``lab.register.Register``)."""

    def record(self, result: QuarterResult) -> object: ...

    def record_aborted(self, quarter: Quarter, variant: Variant, error: str) -> object: ...


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


def run_quarters(
    archive_path: Path,
    quarters: Sequence[Quarter],
    variant_list: Sequence[Variant],
    *,
    workers: int,
    register: Recorder,
    spreads: Mapping[tuple[str, str], float] | None = None,
    progress: Callable[[QuarterResult], None] | None = None,
) -> list[QuarterResult]:
    """Every quarter x variant; the results in variant then time order.

    Workers (``workers > 1``: separate processes) only compute; this process records each
    result in ``register``, a crashed quarter with ``record_aborted``. Results are held back
    until every earlier one in variant-then-time order is recorded, so the register and
    ``progress`` always see the same order, however the workers finish. Sealed quarters are
    refused here without a worker and recorded as such, so the attempt stays visible.
    ``workers <= 1`` runs in this process, one quarter after another.
    """
    order = [(i, j) for i in range(len(variant_list)) for j in range(len(quarters))]
    ready: dict[tuple[int, int], QuarterResult | Exception] = {}
    done: dict[tuple[int, int], QuarterResult] = {}

    def flush() -> None:
        while len(done) < len(order) and order[len(done)] in ready:
            key = order[len(done)]
            i, j = key
            item = ready.pop(key)
            if isinstance(item, Exception):
                error = f"{type(item).__name__}: {item}"
                register.record_aborted(quarters[j], variant_list[i], error)
                result = QuarterResult(quarters[j], variant_list[i], status="aborted", error=error)
            else:
                register.record(item)
                result = item
            done[key] = result
            if progress is not None:
                progress(result)

    pending: list[tuple[int, int]] = []
    for i, j in order:
        if quarters[j].sealed:
            ready[(i, j)] = run_quarter(archive_path, quarters[j], variant_list[i], spreads)
        else:
            pending.append((i, j))
    flush()
    if workers <= 1:
        for i, j in pending:
            try:
                ready[(i, j)] = run_quarter(archive_path, quarters[j], variant_list[i], spreads)
            except Exception as exc:
                ready[(i, j)] = exc
            flush()
    elif pending:
        with ProcessPoolExecutor(max_workers=min(workers, len(pending))) as pool:
            futures: dict[Future[QuarterResult], tuple[int, int]] = {}
            for i, j in pending:
                job = pool.submit(run_quarter, archive_path, quarters[j], variant_list[i], spreads)
                futures[job] = (i, j)
            for future in as_completed(futures):
                key = futures[future]
                try:
                    ready[key] = future.result()
                except Exception as exc:
                    ready[key] = exc
                flush()
    return [done[key] for key in order]


# ---------------------------------------------------------------- CLI


def _percent(x: float) -> str:
    return f"{100 * x:.1f}".replace(".", ",") + " %"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}".replace(".", ",") + " min"


def _brief(error: str) -> str:
    """The first of the ``; ``-joined parts of an error and how many more there are: a
    missing lead usually hits all eight series at once, and one line must stay readable."""
    parts = [p for p in error.split("; ") if p]
    if len(parts) <= 1:
        return error
    return f"{parts[0]} und {len(parts) - 1} weitere"


def describe(result: QuarterResult) -> str:
    """One short German progress line per quarter (CLI)."""
    head = f"{result.quarter.label} {result.variant.name}"
    if result.status == "sealed":
        return f"{head}: verweigert - versiegelter Prüfzeitraum ab 2025-10-01"
    if result.status == "no_lead":
        why = _brief(result.error)
        return f"{head}: nicht gewertet - zu wenig Kerzen vor Quartalsbeginn ({why})"
    if result.status == "incomplete":
        return f"{head}: nicht gewertet - Kerzenarchiv unvollständig ({_brief(result.error)})"
    if result.status == "aborted":
        return f"{head}: abgebrochen - Programmfehler ({result.error})"
    shares = ", ".join(f"{m} {_percent(q.share)}" for m, q in sorted(result.quality.items()))
    if result.graded:
        verdict = "gewertet"
    elif result.quarter.start < Quarter.parse(FIRST_GRADED).start:
        verdict = f"nicht gewertet (vor {FIRST_GRADED})"
    else:
        verdict = "nicht gewertet (unter 90 % der fälligen Entscheidungen)"
    return f"{head}: {verdict}; Entscheidungen {shares}; Laufzeit {_minutes(result.seconds)}"


def _spreads_of(path: Path) -> dict[tuple[str, str], float]:
    """The sampled spread of every whole quarter before the seal, read-only.

    Through ``stored_quarter_spreads``, not the ``spreads`` table itself: a row left by older
    code, or by a sampling run that stopped early, may stand for a partly sampled quarter.
    """
    with Archive.open_readonly(path) as arch:
        stored = stored_quarter_spreads(arch)
    out = {}
    for (market, label), (value, _hours) in stored.items():
        # The seal also holds for the spread samples (ADR-0026): never use a value from there.
        if not Quarter.parse(label).sealed and math.isfinite(value):
            out[(market, label)] = value
    return out


def _archive_refusal(path: Path, exc: Exception) -> str:
    """German refusal for a candle archive the backtest cannot read (``cli_main``)."""
    if isinstance(exc, sqlite3.Error):
        detail = archive_mod.database_error_message(exc)
    else:
        detail = f"Die Datei ist kein Kerzenarchiv, das dieser Code lesen kann ({exc})."
    return f"Kerzenarchiv {path} nicht lesbar - nichts gerechnet. {detail}"


def _parser(out: TextIO) -> LabArgumentParser:
    p = LabArgumentParser(
        out=out,
        prog="trading-bot lab backtest",
        description="Backtest der Strategien v0.3.1 je Quartal über das Kerzenarchiv (Spec 016).",
    )
    p.add_argument(
        "--from", dest="first", default=FIRST_GRADED, help="erstes Quartal, z. B. 2021Q2"
    )
    p.add_argument("--to", dest="last", default="2025Q3", help="letztes Quartal, z. B. 2025Q3")
    p.add_argument(
        "--variant",
        choices=VARIANT_SETS,
        default="base",
        help="base = Live-Kosten; cost-grid = andere Kosten; diagnostic = Lückentoleranz; "
        "usdc = USDC-Depots, BTC über USDC; all = alle",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=default_workers(),
        help="Quartale, die gleichzeitig rechnen (Standard: Prozessorkerne minus 1)",
    )
    add_dir_option(p)
    return p


def _skip_note(selection: str, chosen: Sequence[Variant]) -> str | None:
    skipped = skipped_as_base(selection)
    if not skipped:
        return None
    note = (
        f"{', '.join(skipped)} entfällt: gleiche Kosten wie base (live); base ist der "
        "0,6-%-Punkt des Kostenrasters."
    )
    if BASE not in chosen:
        note += " Diesen Punkt liefert ein Lauf mit --variant base."
    return note


def cli_main(command: str, argv: Sequence[str], out: TextIO) -> int:
    """``trading-bot lab backtest``: run, record in the register, report progress in German.

    Exit codes: ``EXIT_OK`` (0) only if at least one quarter was graded and none aborted;
    ``EXIT_ABORTED`` (1) if a quarter aborted or none was graded (the run has nothing for the
    report, said in one line); ``EXIT_REFUSED`` (2) if nothing ran.
    """
    if command != "backtest":
        print(f"Unbekannter Befehl für das Backtest-Modul: {command}", file=out)
        return EXIT_REFUSED
    args = parse_cli(_parser(out), argv)
    if isinstance(args, int):
        return EXIT_OK if args == 0 else EXIT_REFUSED
    try:
        quarters = Quarter.span(args.first, args.last)
    except ValueError as exc:
        print(f"Ungültiges Quartal: {exc}", file=out)
        return EXIT_REFUSED
    if not quarters:
        print(f"Kein Quartal zwischen {args.first} und {args.last}.", file=out)
        return EXIT_REFUSED
    sealed = [q.label for q in quarters if q.sealed]
    if sealed:
        print(
            f"Verweigert: {', '.join(sealed)} liegt im versiegelten Prüfzeitraum ab "
            f"{_day(SEAL_EPOCH)} (ADR-0026). Der Basisbericht endet mit 2025Q3.",
            file=out,
        )
        return EXIT_REFUSED
    if args.workers < 1:
        print("--workers muss mindestens 1 sein.", file=out)
        return EXIT_REFUSED
    folder: Path = args.dir or lab_dir()
    archive_path = folder / CANDLES_FILE
    if not archive_path.is_file():
        print(f"Kein Kerzenarchiv unter {archive_path} - zuerst 'trading-bot lab fetch'.", file=out)
        return EXIT_REFUSED
    chosen = variants(args.variant)
    # Archive and register are opened here, before any quarter runs: a damaged file, an old
    # schema or a locked database is a refusal in one German line, never a traceback.
    try:
        spreads = _spreads_of(archive_path)
    except (ArchiveError, SchemaError, sqlite3.Error) as exc:
        print(_archive_refusal(archive_path, exc), file=out)
        return EXIT_REFUSED
    try:
        register = Register.open(folder / RUNS_FILE, folder / CHAIN_FILE, runner=git_runner)
    except (RegisterError, SchemaError, sqlite3.Error, OSError) as exc:
        print(
            f"Prüfregister unter {folder / RUNS_FILE} nicht nutzbar ({exc}); nichts gerechnet.",
            file=out,
        )
        return EXIT_REFUSED
    print(
        f"Backtest {quarters[0].label} bis {quarters[-1].label}, Varianten "
        f"{', '.join(v.name for v in chosen)}, {args.workers} Prozess(e) ...",
        file=out,
        flush=True,
    )
    if note := _skip_note(args.variant, chosen):
        print(note, file=out, flush=True)

    def progress(result: QuarterResult) -> None:
        print(describe(result), file=out, flush=True)

    started = time.perf_counter()
    with register:
        results = run_quarters(
            archive_path,
            quarters,
            chosen,
            workers=args.workers,
            register=register,
            spreads=spreads,
            progress=progress,
        )
        trials = register.trial_count()
        runs = len(register.list_runs())
        clean = register.code.clean
    graded = sum(r.graded for r in results)
    aborted = sum(r.status == "aborted" for r in results)
    print(
        f"Fertig: {graded} von {len(results)} Quartalsläufen gewertet, {aborted} abgebrochen, "
        f"Laufzeit {_minutes(time.perf_counter() - started)}.",
        file=out,
    )
    print(f"Prüfregister: {folder / RUNS_FILE}, Kette {folder / CHAIN_FILE}.", file=out)
    # Named, because trial_count counts only runs with a result (status ok): a bare number
    # right after quarters were refused looked as if nothing had been recorded.
    print(
        f"Im Register: ausgewertete Versuche: {trials}, alle Läufe: {runs} "
        "(auch nicht gewertete und abgebrochene).",
        file=out,
    )
    if not clean:
        print(
            "Hinweis: Der Code war nicht nachweislich unverändert (git); das Register vermerkt "
            "den Lauf als nicht sauber.",
            file=out,
        )
    if not graded:
        print(
            "Kein Quartal gewertet: Dieser Lauf liefert keine Zahlen für den Bericht "
            "(Gründe in den Zeilen oben).",
            file=out,
        )
        return EXIT_NOT_GRADED
    return EXIT_ABORTED if aborted else EXIT_OK
