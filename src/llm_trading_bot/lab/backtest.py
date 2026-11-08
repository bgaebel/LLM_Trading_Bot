"""Backtest of the strategy lab (Spec 016 AK4, AK6 cost grid, AK9, ADR-0026).

Each quarter is its own section with fresh capital: the UNCHANGED live ``paper_cycle`` runs once
per simulated minute at ``start + k*60 + 5`` s (ADR-0017) over the candle archive, with the four
classic depots and buy & hold, no GPT, ``Controls()`` without pause and an in-memory bot DB. The
historical client (``lab.history``) answers like Bitvavo would have at the simulated clock.

Process model: ``run_quarter`` is a top-level function, so a ``ProcessPoolExecutor`` can run
quarters in parallel. A worker opens the archive read-only and only returns its
``QuarterResult``; the main process records every result in the trial register and writes
nothing else (``run_quarters``).

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
  - ``no_decision``: rounds in which the market got no snapshot, by the code the live cycle
    gives the failure (``gap_1m``, ``stale``, ``quote_missing`` ...), read through the
    observation hook of ``paper_cycle`` (Spec 015), which never changes what is traded.
  - ``blocked_rounds``: those rounds whose code is ``gap_15m``, ``gap_60m`` or ``gap_240m``.
    The snapshot checks the quote first and then the intervals in the order 1m, 15m, 1h, 4h, so
    such a code means the quote and the 1m bars were fine: only the higher interval blocked.
- **Depots:** one ``DayPoint`` per UTC day (the depot's last equity point that day, valued at
  mid, weights ``qty * mid / value`` with the mid of that round); ``end_mid`` is the last equity
  value; ``end_liquidated`` sells every holding at ``bid * (1 - slippage)`` minus the fee at the
  last round. ``halted_at`` is the round of the drawdown stop; the depot is cash afterwards, and
  only in this quarter, because the next quarter starts with fresh capital.
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

Diagnostic run (AK9): a variant with ``gap_window`` validates the higher intervals (15m, 1h, 4h)
with that gap tolerance instead of the live ``GAP_WINDOW``; the 1m bars keep the live rule. The
module global is read at call time, so it is set only while one of those intervals is checked,
only inside the process that runs the quarter, and restored afterwards. Never a strategy
version; the register marks the run as diagnostic.
"""

import argparse
import hashlib
import importlib
import json
import math
import os
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

from llm_trading_bot.lab import archive as archive_mod
from llm_trading_bot.lab.archive import Archive
from llm_trading_bot.lab.history import CandleRow, HistoricalClient, MemorySource
from llm_trading_bot.lab.model import (
    CLASSIC,
    DEPOTS,
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
from llm_trading_bot.lab.register import GitRunner, Register
from llm_trading_bot.lab.spread import METHOD, QuarterSpreads
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.market import snapshot
from llm_trading_bot.market.models import SYMBOLS, Quote, Symbol
from llm_trading_bot.market.snapshot import INTERVALS, MarketSnapshot
from llm_trading_bot.paper.cycle import STRATEGY_TIMEFRAME, paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import Costs, fresh_quotes
from llm_trading_bot.paper.observations import ObservationBuffer
from llm_trading_bot.paper.report import CycleReport, Equity
from llm_trading_bot.paper.risk import Controls

OFFSET = 5
"""Seconds into each minute a round runs, like the service (ADR-0017)."""
MINUTE = 60
DAY = 86_400
BLOCKING_CODES = frozenset({"gap_15m", "gap_60m", "gap_240m"})
"""Snapshot failures that come only from a gap in a higher interval (see module doc)."""
LIVE_RATIO = (5, 1)
"""Fee : slippage per side in the live defaults (0.25 % : 0.05 %); the cost grid keeps it."""
COST_GRID = (0.003, 0.006, 0.009, 0.012)
"""Round trips (fee and slippage, buy plus sell) of the full reruns (AK6)."""
DIAGNOSTIC_GAP_WINDOW = 4
"""Gap tolerance of the diagnostic run: only the newest 4 bars of 15m, 1h and 4h must be gap-free
(3 steps). Short enough that a single missing bar no longer blocks for days (60 bars of 1h =
60 h, of 4h = 10 days), long enough that the newest bars every signal looks at are contiguous."""
VARIANT_SETS = ("base", "cost-grid", "diagnostic", "all")
EXIT_OK = 0
EXIT_ABORTED = 1
EXIT_REFUSED = 2

BASE = Variant()


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
    return [cost_variant(k) for k in COST_GRID]


def diagnostic_variant(window: int = DIAGNOSTIC_GAP_WINDOW) -> Variant:
    return Variant(name=f"diagnostic-gap-{window}", gap_window=window, diagnostic=True)


def variants(selection: str) -> list[Variant]:
    """The variants of a CLI selection (``VARIANT_SETS``)."""
    if selection == "base":
        return [BASE]
    if selection == "cost-grid":
        return cost_grid()
    if selection == "diagnostic":
        return [diagnostic_variant()]
    if selection == "all":
        return [BASE, *cost_grid(), diagnostic_variant()]
    raise ValueError(f"unknown variant set {selection!r}, expected one of {VARIANT_SETS}")


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

    def start(self) -> None:
        self.codes = {}

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
                problems.append(f"{market} {interval}m: {n} rows before the start (< {LEAD_ROWS})")
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
                    problems.append(f"{market} {interval}m: window {_day(w_start)} not complete")
                    break
    pre.first_open = min(firsts)
    return pre, problems


def _dataset_sha256(arch: Archive, pre: _Preload, end: int) -> str:
    digest = hashlib.sha256()
    for market, interval, first in pre.ranges:
        line = [market, interval, first, end, arch.checksum(market, interval, first, end)]
        digest.update(json.dumps(line, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


@dataclass
class _Tracker:
    """Per-round bookkeeping of the main loop: day points, daily mids, stops, failure codes."""

    costs: Costs
    days: dict[str, list[DayPoint]] = field(default_factory=dict)
    pending: dict[str, tuple[int, Equity, dict[Symbol, Quote]]] = field(default_factory=dict)
    halted_at: dict[str, int] = field(default_factory=dict)
    mids: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    mid_day: dict[str, tuple[int, float]] = field(default_factory=dict)
    no_decision: dict[str, dict[str, int]] = field(default_factory=dict)
    blocked: dict[str, int] = field(default_factory=dict)

    def round(
        self, t: int, report: CycleReport, codes: Mapping[str, str], qs: dict[Symbol, Quote]
    ) -> None:
        day = t // DAY
        for market, code in codes.items():
            if code != "ok":
                per = self.no_decision.setdefault(market, {})
                per[code] = per.get(code, 0) + 1
                if code in BLOCKING_CODES:
                    self.blocked[market] = self.blocked.get(market, 0) + 1
        for stop in report.risk_stops:
            self.halted_at.setdefault(stop.agent, t)
        for eq in report.equity:
            old = self.pending.get(eq.agent)
            if old is not None and old[0] != day:
                self._emit(eq.agent, old)
            self.pending[eq.agent] = (day, eq, qs)
        for symbol, quote in qs.items():
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
        day, eq, qs = item
        weights = {}
        for symbol, qty in ((Symbol.BTCEUR, eq.btc), (Symbol.ETHEUR, eq.eth)):
            quote = qs.get(symbol)
            worth = qty * quote.mid if quote is not None and qty else 0.0
            weights[symbol.market] = worth / eq.value if eq.value > 0 else 0.0
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
            if qty > 0:
                gross = qty * qs[symbol].bid * (1 - self.costs.slippage)
                sold += gross - gross * self.costs.fee_rate
        return float(eq.value), float(sold)


def _trades(con: sqlite3.Connection) -> dict[str, list[Trade]]:
    out: dict[str, list[Trade]] = {}
    for r in con.execute(
        "SELECT t.ts, t.agent, t.symbol, t.side, t.qty, t.fill, t.notional, t.fee, d.inputs"
        " FROM trades t LEFT JOIN decisions d ON d.id = t.decision_id ORDER BY t.id"
    ):
        out.setdefault(r["agent"], []).append(
            Trade(
                ts=_epoch(r["ts"]),
                agent=r["agent"],
                market=Symbol[r["symbol"]].market,
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
    con: sqlite3.Connection, quarter: Quarter, track: _Tracker
) -> dict[str, MarketQuality]:
    out = {}
    for symbol in SYMBOLS:
        market = symbol.market
        due = decided = 0
        for agent in CLASSIC:
            step = STRATEGY_TIMEFRAME[agent] * MINUTE
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
    began = time.perf_counter()
    model = QuarterSpreads(spreads or {})
    result = QuarterResult(
        quarter,
        variant,
        spread={m: model.spread(m, quarter.start) for m in MARKETS},
    )
    if quarter.sealed:
        result.status = "sealed"
        result.error = "quarter reaches into the sealed period from 2025-10-01 (ADR-0026)"
        return result
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
    client = HistoricalClient(pre.source, model, clock, start=pre.first_open, end=quarter.end)
    costs = Costs(variant.fee_rate, variant.slippage)
    config = BotConfig(classic=CLASSIC, llm=None)
    controls = Controls()
    codes = _FailureCodes()
    track = _Tracker(costs)
    con = open_db(Path(":memory:"), agents=DEPOTS)
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
                track.round(t, report, codes.codes, fresh_quotes(con, now_epoch=t))
                result.rounds += 1
        track.finish()
        trades = _trades(con)
        actions = _actions(con)
        result.quality = _quality(con, quarter, track)
    finally:
        con.close()
    for agent in DEPOTS:
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
    result in ``register`` as it arrives, a crashed quarter with ``record_aborted``. Sealed
    quarters are refused here without a worker and recorded as such, so the attempt stays
    visible. ``workers <= 1`` runs in this process, one quarter after another.
    """
    done: dict[tuple[int, int], QuarterResult] = {}

    def finish(i: int, j: int, result: QuarterResult) -> None:
        register.record(result)
        done[(i, j)] = result
        if progress is not None:
            progress(result)

    def failed(i: int, j: int, exc: BaseException) -> None:
        variant, quarter = variant_list[i], quarters[j]
        error = f"{type(exc).__name__}: {exc}"
        register.record_aborted(quarter, variant, error)
        result = QuarterResult(quarter, variant, status="aborted", error=error)
        done[(i, j)] = result
        if progress is not None:
            progress(result)

    pending: list[tuple[int, int]] = []
    for i, variant in enumerate(variant_list):
        for j, quarter in enumerate(quarters):
            if quarter.sealed:
                finish(i, j, run_quarter(archive_path, quarter, variant, spreads))
            else:
                pending.append((i, j))
    if workers <= 1:
        for i, j in pending:
            try:
                result = run_quarter(archive_path, quarters[j], variant_list[i], spreads)
            except Exception as exc:
                failed(i, j, exc)
            else:
                finish(i, j, result)
    elif pending:
        with ProcessPoolExecutor(max_workers=min(workers, len(pending))) as pool:
            futures: dict[Future[QuarterResult], tuple[int, int]] = {}
            for i, j in pending:
                job = pool.submit(run_quarter, archive_path, quarters[j], variant_list[i], spreads)
                futures[job] = (i, j)
            for future in as_completed(futures):
                i, j = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failed(i, j, exc)
                else:
                    finish(i, j, result)
    return [done[key] for key in sorted(done)]


# ---------------------------------------------------------------- CLI


def _percent(x: float) -> str:
    return f"{100 * x:.1f}".replace(".", ",") + " %"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}".replace(".", ",") + " min"


def describe(result: QuarterResult) -> str:
    """One German progress line per quarter (CLI)."""
    head = f"{result.quarter.label} {result.variant.name}"
    if result.status == "sealed":
        return f"{head}: verweigert - versiegelter Prüfzeitraum ab 2025-10-01"
    if result.status == "no_lead":
        return f"{head}: nicht gewertet - Datenvorlauf fehlt ({result.error})"
    if result.status == "incomplete":
        return f"{head}: nicht gewertet - Archiv unvollständig ({result.error})"
    if result.status == "aborted":
        return f"{head}: abgebrochen ({result.error})"
    shares = ", ".join(f"{m} {_percent(q.share)}" for m, q in sorted(result.quality.items()))
    if result.graded:
        verdict = "gewertet"
    elif result.quarter.start < Quarter.parse(FIRST_GRADED).start:
        verdict = f"nicht gewertet (vor {FIRST_GRADED})"
    else:
        verdict = "nicht gewertet (unter 90 % der fälligen Entscheidungen)"
    return f"{head}: {verdict}; Entscheidungen {shares}; Laufzeit {_minutes(result.seconds)}"


def _runner() -> GitRunner | None:
    """The runner of ``lab.gitstate`` if that module exists (clean-tree flag, AK8); without it
    the register reads the commit from the files and records the tree as not clean."""
    try:
        module = importlib.import_module("llm_trading_bot.lab.gitstate")
    except ModuleNotFoundError:
        return None
    runner: GitRunner | None = getattr(module, "git", None)
    return runner


def _spreads_of(path: Path) -> dict[tuple[str, str], float]:
    with Archive.open_readonly(path) as arch:
        rows = arch.db.execute(
            "SELECT market, quarter, spread FROM spreads WHERE method=?", (METHOD,)
        ).fetchall()
    out = {}
    for market, label, value in rows:
        # The seal also holds for the spread samples (ADR-0026): never use a value from there.
        if not Quarter.parse(str(label)).sealed and math.isfinite(float(value)):
            out[(str(market), str(label))] = float(value)
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trading-bot lab backtest",
        description="Backtest v0.3.1 je Quartal über das Kerzenarchiv (Spec 016).",
    )
    p.add_argument(
        "--from", dest="first", default=FIRST_GRADED, help="erstes Quartal, z. B. 2021Q2"
    )
    p.add_argument("--to", dest="last", default="2025Q3", help="letztes Quartal, z. B. 2025Q3")
    p.add_argument("--variant", choices=VARIANT_SETS, default="base")
    p.add_argument("--workers", type=int, default=default_workers())
    p.add_argument("--dir", type=Path, default=None, help="Labor-Ordner (Standard: lab_dir())")
    return p


def cli_main(command: str, argv: Sequence[str], out: TextIO) -> int:
    """``trading-bot lab backtest``: run, record in the register, report progress in German."""
    if command != "backtest":
        print(f"Unbekannter Befehl für das Backtest-Modul: {command}", file=out)
        return EXIT_REFUSED
    try:
        args = _parser().parse_args(list(argv))
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_REFUSED
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
    spreads = _spreads_of(archive_path)
    print(
        f"Backtest {quarters[0].label}-{quarters[-1].label}, Varianten "
        f"{', '.join(v.name for v in chosen)}, {args.workers} Prozess(e) ...",
        file=out,
        flush=True,
    )

    def progress(result: QuarterResult) -> None:
        print(describe(result), file=out, flush=True)

    started = time.perf_counter()
    with Register.open(folder / RUNS_FILE, folder / CHAIN_FILE, runner=_runner()) as register:
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
        clean = register.code.clean
    graded = sum(r.graded for r in results)
    aborted = sum(r.status == "aborted" for r in results)
    print(
        f"Fertig: {graded} von {len(results)} Abschnitten gewertet, {aborted} abgebrochen, "
        f"Laufzeit {_minutes(time.perf_counter() - started)}.",
        file=out,
    )
    print(
        f"Ergebnisse im Prüfregister {folder / RUNS_FILE} mit Kette {folder / CHAIN_FILE}; "
        f"Versuche bisher: {trials}.",
        file=out,
    )
    if not clean:
        print("Hinweis: Arbeitsbaum nicht als sauber belegt (im Register vermerkt).", file=out)
    return EXIT_ABORTED if aborted else EXIT_OK
