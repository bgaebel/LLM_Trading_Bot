# Origin: LLM-Trading-Arena@7563724 apps/arena/reporting.py (adapted) - accounting (FIFO with
#   fees allocated to lots, unknown opening inventory gives no realized PnL)
# Origin: LLM-Trading-Arena@7563724 apps/arena/research.py (adapted) - performance (net return,
#   max drawdown incl. a full loss, fees, turnover, exposure, excess over buy & hold)
"""Metrics of the strategy lab (Spec 016 AK7, analytic part of AK6): pure functions over
``QuarterResult`` lists, no I/O, standard library only.

Conventions the report relies on:

- Returns are fractions (0.01 = 1 %). ``START_CAPITAL`` is each quarter's fresh capital (AK4).
- **Daily returns** of a depot: day 1 against the start capital, every later day against the
  previous day's value (``DayPoint`` = value at the day's last round, at mid).
- **Benchmark days:** ``QuarterResult.daily_mid`` gives one mid per day; a day's market return is
  ``mid_day / mid_previous_entry - 1``. The first entry is only the base. To compare day 1 too,
  the backtest puts the mid at the quarter's first round in front, labelled with the previous
  day (e.g. ``2021-03-31``). Strategy and benchmark are compared on the days both have.
- **Gross** = net plus the fees and slippage the trades paid (slippage rebuilt from the fill:
  ``notional * s / (1 + s)`` for a buy, ``notional * s / (1 - s)`` for a sell). The spread stays
  inside gross. Decisions and quantities are held fixed (first order, no new run).
- Only graded quarters (``QuarterResult.graded``) enter pooled numbers; the per-quarter table
  lists every quarter that has depot results.
- Randomness: only ``random.Random(seed).randrange`` (LEARNING "random.gauss rechnet je Plattform
  anders"); integer draws are identical on every platform, so pinned values hold in the CI.
"""

import math
import operator
import random
from bisect import insort
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from llm_trading_bot.lab.model import DepotResult, QuarterResult, Trade, Variant

START_CAPITAL = 10_000.0
DAYS_PER_YEAR = 365
"""Crypto trades every day; annualising with 252 would understate the year."""
BOOTSTRAP_DRAWS = 5_000
BOOTSTRAP_BLOCK = 7
"""Mean block length in days (Spec 016 AK7): keeps a week of volatility clustering together."""
BOOTSTRAP_SEED = 16_016
BAND = 0.95
Z95 = 1.959963984540054
"""Two-sided 95 % normal quantile for the per-cell band (trades treated as independent)."""
FEW_TRADES = 30
"""Cells with fewer round trips are flagged as not robust (methodology, regime split)."""
VOL_MIN_HISTORY = 30
"""Earlier vol values a market needs before a trade gets a tercile; before that ``n/a``."""
QTY_EPS = 1e-10
"""Quantities below this are rounding dust (Arena accounting)."""
UNKNOWN = "n/a"
NO_VERDICT = "kein Urteil"
INDICATIVE = "indikativ"
RELIABLE = "belastbar"
_SELLS = ("SELL", "RISK_SELL")


# --- small statistics helpers -------------------------------------------------------------


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation between order statistics (``numpy`` default, "inclusive")."""
    if not sorted_values:
        raise ValueError("quantile of an empty sample")
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: Sequence[float]) -> float:
    """Sample standard deviation; 0 below two values."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def verdict(round_trips: int) -> str:
    """Report label by sample size (Spec 016 AK10, methodology section 7)."""
    if round_trips < 100:
        return NO_VERDICT
    if round_trips <= 300:
        return INDICATIVE
    return RELIABLE


# --- FIFO round trips (Arena reporting.accounting) ---------------------------------------


@dataclass(frozen=True)
class RoundTrip:
    """One FIFO match of (part of) a buy lot with (part of) a sell, fees included."""

    agent: str
    market: str
    opened: int
    closed: int
    qty: float
    cost: float
    """EUR paid for ``qty`` including its share of the buy fee."""
    proceeds: float
    """EUR received for ``qty`` after its share of the sell fee."""
    pnl: float
    net_return: float
    """``pnl / cost``."""
    context: dict[str, Any] = field(default_factory=dict)
    """The opening trade's context (regime, vol, direction at entry)."""


@dataclass(frozen=True)
class OpenLots:
    quantity: float
    cost_basis: float


@dataclass(frozen=True)
class Fifo:
    round_trips: list[RoundTrip]
    realized_pnl: float | None
    """None if a sell found no opening lot: no invented profit (Arena)."""
    sell_events: int
    opening_inventory_unknown: bool
    open_lots: dict[str, OpenLots]


def fifo(trades: Iterable[Trade]) -> Fifo:
    """Pair buys and sells per agent and market first in, first out; partial fills split lots.

    Trades are taken in time order; equal timestamps keep their input (execution) order.
    """
    lots: dict[tuple[str, str], deque[list[Any]]] = {}
    trips: list[RoundTrip] = []
    realized = 0.0
    sells = 0
    missing = False
    for t in sorted(trades, key=operator.attrgetter("ts")):
        qty = float(t.qty)
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError(f"invalid trade quantity: {t!r}")
        if t.side == "BUY":
            unit = (t.notional + t.fee) / qty
        elif t.side in _SELLS:
            unit = (t.notional - t.fee) / qty
        else:
            raise ValueError(f"unsupported trade side: {t.side!r}")
        if not math.isfinite(unit):
            raise ValueError(f"invalid trade value: {t!r}")
        queue = lots.setdefault((t.agent, t.market), deque())
        if t.side == "BUY":
            queue.append([qty, unit, t.ts, t.context])
            continue
        sells += 1
        while qty > QTY_EPS and queue:
            lot = queue[0]
            matched = min(qty, lot[0])
            cost, proceeds = matched * lot[1], matched * unit
            realized += proceeds - cost
            trips.append(
                RoundTrip(
                    t.agent,
                    t.market,
                    lot[2],
                    t.ts,
                    matched,
                    cost,
                    proceeds,
                    proceeds - cost,
                    (proceeds - cost) / cost if cost > 0 else 0.0,
                    dict(lot[3]),
                )
            )
            qty -= matched
            lot[0] -= matched
            if lot[0] <= QTY_EPS:
                queue.popleft()
        if qty > QTY_EPS:
            missing = True
    open_lots = {
        market: OpenLots(sum(lot[0] for lot in q), sum(lot[0] * lot[1] for lot in q))
        for (_, market), q in sorted(lots.items())
    }
    return Fifo(trips, None if missing else realized, sells, missing, open_lots)


# --- per depot and quarter ---------------------------------------------------------------


def slippage_cost(trade: Trade, slippage: float) -> float:
    """EUR the fill lost against bid/ask; the fill already contains ``1 +/- slippage``."""
    if trade.side == "BUY":
        return trade.notional * slippage / (1 + slippage)
    return trade.notional * slippage / (1 - slippage)


def daily_returns(depot: DepotResult, capital: float = START_CAPITAL) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    prev = capital
    for p in depot.days:
        # After a full loss nothing is left to gain or lose.
        out.append((p.day, p.value / prev - 1 if prev > 0 else 0.0))
        prev = p.value
    return out


def max_drawdown(values: Iterable[float]) -> float:
    """Largest fall from a running peak as a fraction; a full loss is 1.0 (Arena)."""
    peak = 0.0
    worst = 0.0
    for v in values:
        if not math.isfinite(v) or v < 0:
            raise ValueError(f"invalid equity value: {v!r}")
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1 - v / peak)
    return worst


def mean_weights(depot: DepotResult) -> dict[str, float]:
    """ē_m: the depot's mean weight per market over its daily points."""
    if not depot.days:
        return {}
    markets = sorted({m for p in depot.days for m in p.weights})
    n = len(depot.days)
    return {m: sum(p.weights.get(m, 0.0) for p in depot.days) / n for m in markets}


@dataclass(frozen=True)
class Benchmark:
    """Buy & hold with the strategy's mean quote, rebalanced daily (AK7); cash returns 0."""

    agent: str
    quarter: str
    exposure: dict[str, float]
    cost_per_side: dict[str, float]
    """fee + slippage + half the assumed spread, per market."""
    days: list[str]
    returns: list[float]
    returns_cost: list[float]
    total: float
    total_cost: float
    cash: float = 0.0


def _market_returns(rows: Sequence[tuple[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for (_, before), (day, after) in pairwise(rows):
        if before <= 0:
            raise ValueError(f"invalid mid before {day}: {before!r}")
        out[day] = after / before - 1
    return out


def benchmark(result: QuarterResult, agent: str) -> Benchmark:
    """``r_bench,t = Σ_m ē_m · r_BH,m,t`` and the same after daily rebalancing costs.

    Holding ē_m through a day with portfolio return R drifts the weight to
    ``ē_m (1 + r_m) / (1 + R)``; restoring it trades ``ē_m |r_m - R|`` of the start-of-day value
    per market. The first day also pays the entry ``Σ_m c_m ē_m``; the exit is not charged (the
    mid valuation of the strategy does not pay one either).
    """
    v = result.variant
    weights = mean_weights(result.depots[agent])
    exposure = {m: w for m, w in weights.items() if w > 0}
    cost = {m: v.fee_rate + v.slippage + result.spread.get(m, 0.0) / 2 for m in exposure}
    per_market = {m: _market_returns(result.daily_mid.get(m, [])) for m in exposure}
    if exposure:
        days = sorted(set.intersection(*(set(r) for r in per_market.values())))
    else:
        days = sorted({d for rows in result.daily_mid.values() for d, _ in rows[1:]})
    plain: list[float] = []
    costed: list[float] = []
    for i, day in enumerate(days):
        r = {m: per_market[m][day] for m in exposure}
        total = sum(exposure[m] * r[m] for m in exposure)
        drag = sum(cost[m] * exposure[m] * abs(r[m] - total) for m in exposure)
        if i == 0:
            drag += sum(cost[m] * exposure[m] for m in exposure)
        plain.append(total)
        costed.append(total - drag)
    return Benchmark(
        agent,
        result.quarter.label,
        exposure,
        cost,
        days,
        plain,
        costed,
        math.prod(1 + x for x in plain) - 1,
        math.prod(1 + x for x in costed) - 1,
    )


@dataclass(frozen=True)
class DepotMetrics:
    agent: str
    quarter: str
    graded: bool
    capital: float
    end_mid: float
    end_liquidated: float
    net_return_mid: float
    net_return_liquidated: float
    gross_pnl: float
    """EUR at mid valuation with fees and slippage added back (spread stays in)."""
    gross_return: float
    fees: float
    slippage: float
    trades: int
    turnover: float
    """Traded notional / capital (Arena)."""
    roundtrip_volume: float
    """Half the traded notional in EUR: one euro bought and sold again is one euro of volume."""
    round_trips: int
    realized_pnl: float | None
    max_drawdown: float
    mean_quote: float
    """Mean of the summed market weights over the days (mean investment quote)."""
    mean_weights: dict[str, float]
    benchmark: float
    benchmark_cost: float
    excess: float
    """net_return_mid - benchmark_cost."""
    halted_at: int | None
    days: int


def depot_metrics(
    result: QuarterResult, agent: str, capital: float = START_CAPITAL
) -> DepotMetrics:
    depot = result.depots[agent]
    v = result.variant
    fees = sum(t.fee for t in depot.trades)
    slip = sum(slippage_cost(t, v.slippage) for t in depot.trades)
    notional = sum(abs(t.notional) for t in depot.trades)
    gross_pnl = depot.end_mid - capital + fees + slip
    acc = fifo(depot.trades)
    weights = mean_weights(depot)
    days = len(depot.days)
    quote = sum(sum(p.weights.values()) for p in depot.days) / days if days else 0.0
    bench = benchmark(result, agent)
    net_mid = depot.end_mid / capital - 1
    return DepotMetrics(
        agent=agent,
        quarter=result.quarter.label,
        graded=result.graded,
        capital=capital,
        end_mid=depot.end_mid,
        end_liquidated=depot.end_liquidated,
        net_return_mid=net_mid,
        net_return_liquidated=depot.end_liquidated / capital - 1,
        gross_pnl=gross_pnl,
        gross_return=gross_pnl / capital,
        fees=fees,
        slippage=slip,
        trades=len(depot.trades),
        turnover=notional / capital,
        roundtrip_volume=notional / 2,
        round_trips=len(acc.round_trips),
        realized_pnl=acc.realized_pnl,
        max_drawdown=max_drawdown([capital, *(p.value for p in depot.days)]),
        mean_quote=quote,
        mean_weights=weights,
        benchmark=bench.total,
        benchmark_cost=bench.total_cost,
        excess=net_mid - bench.total_cost,
        halted_at=depot.halted_at,
        days=days,
    )


# --- break-even at fixed decisions (AK6) -------------------------------------------------


@dataclass(frozen=True)
class BreakEven:
    """``net(k) = gross - k * roundtrip_volume``; k is the round-trip cost besides the spread.

    ``model_cost`` is the variant's 2 * (fee + slippage), so ``net(model_cost)`` is the run's net
    result up to second-order slippage terms. The spread is already inside gross; adding the
    volume-weighted assumed spread makes ``break_even_total`` comparable with the full round-trip
    cost (fee, slippage and spread).
    """

    agent: str
    quarters: int
    gross_pnl: float
    roundtrip_volume: float
    model_cost: float
    spread: float
    break_even: float | None
    """None without trades; negative means a loss even at zero cost."""
    break_even_total: float | None
    ratio: float | None
    """break_even_total / (model_cost + spread); the promotion rule wants >= 2."""

    def net(self, k: float) -> float:
        return self.gross_pnl - k * self.roundtrip_volume


def break_even(
    results: Iterable[QuarterResult], agent: str, capital: float = START_CAPITAL
) -> BreakEven:
    """Pooled over the graded quarters of one variant, from the run's own trade list."""
    graded = [r for r in results if r.graded and agent in r.depots]
    variant = _one_variant(graded)
    gross = 0.0
    volume = 0.0
    spread_volume = 0.0
    for r in graded:
        m = depot_metrics(r, agent, capital)
        gross += m.gross_pnl
        volume += m.roundtrip_volume
        for t in r.depots[agent].trades:
            spread_volume += abs(t.notional) / 2 * r.spread.get(t.market, 0.0)
    spread = spread_volume / volume if volume > 0 else 0.0
    model = variant.roundtrip if variant else 0.0
    k = gross / volume if volume > 0 else None
    total = k + spread if k is not None else None
    full = model + spread
    return BreakEven(
        agent,
        len(graded),
        gross,
        volume,
        model,
        spread,
        k,
        total,
        total / full if total is not None and full > 0 else None,
    )


def _one_variant(results: Sequence[QuarterResult]) -> Variant | None:
    names = {r.variant for r in results}
    if len(names) > 1:
        raise ValueError(f"metrics mix variants: {sorted(v.name for v in names)}")
    return next(iter(names), None)


# --- uncertainty: stationary block bootstrap over pooled graded days ---------------------


@dataclass(frozen=True)
class Band:
    estimate: float
    low: float
    high: float
    median: float
    compatible_with_chance: bool
    """True if the 95 % band contains 0 ("mit Zufall vereinbar: ja")."""


@dataclass(frozen=True)
class Uncertainty:
    agent: str
    days: int
    draws: int
    mean_block: int
    seed: int
    mean_return: Band
    """Annualised arithmetic mean daily return (against cash = 0)."""
    sharpe: Band
    """Annualised: mean / sd * sqrt(365)."""
    excess: Band
    """Annualised mean daily return over the benchmark with costs."""
    excess_no_cost: Band
    """Same against the benchmark without costs."""


class TooFewDays(ValueError):
    """The bootstrap needs at least two pooled graded days."""


def stationary_indices(n: int, mean_block: int, rng: random.Random) -> list[int]:
    """Politis-Romano: each next day continues the block (circularly) or, with probability
    1 / mean_block, jumps to a uniform start. Integer draws only (platform-identical)."""
    i = rng.randrange(n)
    out = [i]
    while len(out) < n:
        i = rng.randrange(n) if rng.randrange(mean_block) == 0 else (i + 1) % n
        out.append(i)
    return out


def chance_text(band: Band) -> str:
    return "ja" if band.compatible_with_chance else "nein"


def pooled_days(
    results: Iterable[QuarterResult], agent: str, capital: float = START_CAPITAL
) -> list[tuple[str, float, float, float]]:
    """(day, strategy, benchmark, benchmark with costs) for every graded day both have."""
    rows: list[tuple[str, float, float, float]] = []
    for r in sorted((r for r in results if r.graded and agent in r.depots), key=_start):
        own = dict(daily_returns(r.depots[agent], capital))
        b = benchmark(r, agent)
        for day, plain, costed in zip(b.days, b.returns, b.returns_cost, strict=True):
            if day in own:
                rows.append((day, own[day], plain, costed))
    return rows


def _start(r: QuarterResult) -> int:
    return r.quarter.start


def _sharpe(total: float, squares: float, n: int) -> float:
    m = total / n
    var = (squares - n * m * m) / (n - 1)
    return m / math.sqrt(var) * math.sqrt(DAYS_PER_YEAR) if var > 0 else 0.0


def _band(estimate: float, draws: list[float]) -> Band:
    s = sorted(draws)
    low, high = quantile(s, (1 - BAND) / 2), quantile(s, (1 + BAND) / 2)
    return Band(estimate, low, high, quantile(s, 0.5), low <= 0 <= high)


def uncertainty_all(
    results: Sequence[QuarterResult],
    agents: Sequence[str],
    *,
    capital: float = START_CAPITAL,
    draws: int = BOOTSTRAP_DRAWS,
    mean_block: int = BOOTSTRAP_BLOCK,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Uncertainty]:
    """All depots resampled with the same day indices, so their bands are comparable.

    Days are the graded days every given depot has (in a backtest they all share them).
    Raises TooFewDays below two days.
    """
    per_agent = {a: {d[0]: d[1:] for d in pooled_days(results, a, capital)} for a in agents}
    days = sorted(set.intersection(*(set(v) for v in per_agent.values()))) if agents else []
    n = len(days)
    if n < 2:
        raise TooFewDays(f"bootstrap needs at least two graded days, got {n}")
    cols: dict[str, tuple[list[float], list[float], list[float], list[float]]] = {}
    for a in agents:
        own = [per_agent[a][d][0] for d in days]
        cols[a] = (
            own,
            [x * x for x in own],
            [per_agent[a][d][0] - per_agent[a][d][2] for d in days],
            [per_agent[a][d][0] - per_agent[a][d][1] for d in days],
        )
    sampled: dict[str, list[list[float]]] = {a: [[], [], [], []] for a in agents}
    rng = random.Random(seed)
    for _ in range(draws):
        pick = operator.itemgetter(*stationary_indices(n, mean_block, rng))
        for a in agents:
            own, sq, ex, ex0 = cols[a]
            s = sum(pick(own))
            out = sampled[a]
            out[0].append(s / n * DAYS_PER_YEAR)
            out[1].append(_sharpe(s, sum(pick(sq)), n))
            out[2].append(sum(pick(ex)) / n * DAYS_PER_YEAR)
            out[3].append(sum(pick(ex0)) / n * DAYS_PER_YEAR)
    result: dict[str, Uncertainty] = {}
    for a in agents:
        own, sq, ex, ex0 = cols[a]
        out = sampled[a]
        result[a] = Uncertainty(
            a,
            n,
            draws,
            mean_block,
            seed,
            _band(sum(own) / n * DAYS_PER_YEAR, out[0]),
            _band(_sharpe(sum(own), sum(sq), n), out[1]),
            _band(sum(ex) / n * DAYS_PER_YEAR, out[2]),
            _band(sum(ex0) / n * DAYS_PER_YEAR, out[3]),
        )
    return result


def uncertainty(results: Sequence[QuarterResult], agent: str, **kwargs: Any) -> Uncertainty:
    return uncertainty_all(results, [agent], **kwargs)[agent]


# --- per-quarter spread ------------------------------------------------------------------


@dataclass(frozen=True)
class Spread:
    n: int
    mean: float
    median: float
    stdev: float
    low: float
    high: float


def spread(values: Sequence[float]) -> Spread:
    if not values:
        return Spread(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    s = sorted(values)
    return Spread(len(s), _mean(s), quantile(s, 0.5), _stdev(s), s[0], s[-1])


@dataclass(frozen=True)
class QuarterSpread:
    """How much the plain per-quarter numbers scatter (graded quarters only)."""

    agent: str
    net_return_mid: Spread
    excess: Spread


def quarter_spread(rows: Iterable[DepotMetrics], agent: str) -> QuarterSpread:
    mine = [r for r in rows if r.agent == agent and r.graded]
    return QuarterSpread(
        agent, spread([r.net_return_mid for r in mine]), spread([r.excess for r in mine])
    )


# --- market-phase cells ------------------------------------------------------------------


def vol_terciles(
    history: Iterable[tuple[int, str, float]],
    queries: Sequence[tuple[int, str, float | None]],
    min_history: int = VOL_MIN_HISTORY,
) -> list[str]:
    """Tercile (low/mid/high) of each query's vol among earlier vols of the same market.

    Only history with a timestamp strictly before the query counts (an expanding window over
    earlier trades), so a trade never sees its own bar or the future. Thresholds are the 1/3
    and 2/3 quantiles; fewer than ``min_history`` earlier values give ``n/a``.
    """
    past = sorted(history, key=operator.itemgetter(0))
    order = sorted(range(len(queries)), key=lambda i: queries[i][0])
    seen: dict[str, list[float]] = {}
    out = [UNKNOWN] * len(queries)
    j = 0
    for i in order:
        ts, market, vol = queries[i]
        while j < len(past) and past[j][0] < ts:
            insort(seen.setdefault(past[j][1], []), past[j][2])
            j += 1
        values = seen.get(market, [])
        if vol is None or len(values) < max(min_history, 1):
            continue
        low, high = quantile(values, 1 / 3), quantile(values, 2 / 3)
        out[i] = "low" if vol <= low else "mid" if vol <= high else "high"
    return out


def _vol(context: Mapping[str, Any]) -> float | None:
    v = context.get("vol")
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return None
    return float(v)


def _direction(context: Mapping[str, Any]) -> str:
    """A past return (number, sign) or a label from the backtest."""
    d = context.get("direction")
    if isinstance(d, str) and d:
        return d
    if isinstance(d, int | float) and not isinstance(d, bool) and math.isfinite(d):
        return "up" if d > 0 else "down" if d < 0 else "flat"
    return UNKNOWN


@dataclass(frozen=True)
class Cell:
    agent: str
    regime: str
    vol: str
    direction: str
    count: int
    mean: float
    """Mean net return per round trip."""
    low: float
    high: float
    """95 % band: mean ± 1.96 · sd / sqrt(n) (round trips treated as independent)."""
    pnl: float
    few: bool
    """Fewer than FEW_TRADES round trips: not robust."""


def market_cells(
    results: Iterable[QuarterResult],
    agent: str,
    *,
    min_history: int = VOL_MIN_HISTORY,
) -> list[Cell]:
    """Round trips of the graded quarters by regime x vol tercile x direction at entry.

    The vol history is every trade of every depot in the graded quarters (``context['vol']``),
    per market; a round trip's tercile uses only those before its opening trade.
    """
    graded = sorted((r for r in results if r.graded), key=_start)
    history: list[tuple[int, str, float]] = []
    for r in graded:
        for depot in r.depots.values():
            for t in depot.trades:
                v = _vol(t.context)
                if v is not None:
                    history.append((t.ts, t.market, v))
    trips = [
        rt for r in graded if agent in r.depots for rt in fifo(r.depots[agent].trades).round_trips
    ]
    terciles = vol_terciles(
        history, [(rt.opened, rt.market, _vol(rt.context)) for rt in trips], min_history
    )
    groups: dict[tuple[str, str, str], list[RoundTrip]] = {}
    for rt, tercile in zip(trips, terciles, strict=True):
        regime = rt.context.get("regime")
        key = (
            regime if isinstance(regime, str) and regime else UNKNOWN,
            tercile,
            _direction(rt.context),
        )
        groups.setdefault(key, []).append(rt)
    cells = []
    for (regime, vol, direction), rts in sorted(groups.items()):
        rets = [rt.net_return for rt in rts]
        m = _mean(rets)
        half = Z95 * _stdev(rets) / math.sqrt(len(rets))
        cells.append(
            Cell(
                agent,
                regime,
                vol,
                direction,
                len(rts),
                m,
                m - half,
                m + half,
                sum(rt.pnl for rt in rts),
                len(rts) < FEW_TRADES,
            )
        )
    return cells


# --- data quality ------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityRow:
    quarter: str
    """Quarter label, or ``all`` for the sum over every quarter given."""
    market: str
    due: int
    decided: int
    share: float
    no_decision: dict[str, int]
    no_decision_share: dict[str, float]
    """Rounds without a decision per error code, relative to ``due``."""
    blocked_rounds: int
    blocked_hours: float
    """Blocked rounds are minutes (ADR-0017)."""
    eligible: bool | None
    """None for the ``all`` row."""


def quality(results: Iterable[QuarterResult]) -> list[QualityRow]:
    rows: list[QualityRow] = []
    total: dict[str, list[Any]] = {}
    for r in sorted(results, key=_start):
        eligible = r.eligible
        for market, q in sorted(r.quality.items()):
            rows.append(
                _quality_row(
                    r.quarter.label,
                    market,
                    q.due,
                    q.decided,
                    q.no_decision,
                    q.blocked_rounds,
                    eligible.get(market),
                )
            )
            t = total.setdefault(market, [0, 0, {}, 0])
            t[0] += q.due
            t[1] += q.decided
            for code, n in q.no_decision.items():
                t[2][code] = t[2].get(code, 0) + n
            t[3] += q.blocked_rounds
    for market, (due, decided, codes, blocked) in sorted(total.items()):
        rows.append(_quality_row("all", market, due, decided, codes, blocked, None))
    return rows


def _quality_row(
    quarter: str,
    market: str,
    due: int,
    decided: int,
    codes: Mapping[str, int],
    blocked: int,
    eligible: bool | None,
) -> QualityRow:
    return QualityRow(
        quarter,
        market,
        due,
        decided,
        decided / due if due else 0.0,
        dict(sorted(codes.items())),
        {c: n / due if due else 0.0 for c, n in sorted(codes.items())},
        blocked,
        blocked / 60,
        eligible,
    )


# --- everything for the report -----------------------------------------------------------


@dataclass(frozen=True)
class LabMetrics:
    variant: str
    depots: list[DepotMetrics]
    """Every quarter with depot results, graded or not, in time order."""
    spreads: dict[str, QuarterSpread]
    uncertainty: dict[str, Uncertainty]
    """Empty if fewer than two graded days exist."""
    break_even: dict[str, BreakEven]
    cells: dict[str, list[Cell]]
    quality: list[QualityRow]
    round_trips: dict[str, int]
    """Over the graded quarters."""
    labels: dict[str, str]
    """verdict() of round_trips."""


def summarize(
    results: Sequence[QuarterResult],
    *,
    capital: float = START_CAPITAL,
    draws: int = BOOTSTRAP_DRAWS,
    mean_block: int = BOOTSTRAP_BLOCK,
    seed: int = BOOTSTRAP_SEED,
    min_history: int = VOL_MIN_HISTORY,
) -> LabMetrics:
    """All AK7 metrics for the results of one variant (raises ValueError on a mix)."""
    variant = _one_variant(list(results))
    ordered = sorted(results, key=_start)
    agents = sorted({a for r in ordered for a in r.depots})
    rows = [depot_metrics(r, a, capital) for r in ordered for a in sorted(r.depots)]
    graded_agents = sorted({a for r in ordered if r.graded for a in r.depots})
    trips = {
        a: sum(m.round_trips for m in rows if m.agent == a and m.graded) for a in graded_agents
    }
    try:
        unc = uncertainty_all(
            ordered, graded_agents, capital=capital, draws=draws, mean_block=mean_block, seed=seed
        )
    except TooFewDays:
        unc = {}
    return LabMetrics(
        variant=variant.name if variant else "",
        depots=rows,
        spreads={a: quarter_spread(rows, a) for a in agents},
        uncertainty=unc,
        break_even={a: break_even(ordered, a, capital) for a in graded_agents},
        cells={a: market_cells(ordered, a, min_history=min_history) for a in graded_agents},
        quality=quality(ordered),
        round_trips=trips,
        labels={a: verdict(n) for a, n in trips.items()},
    )
