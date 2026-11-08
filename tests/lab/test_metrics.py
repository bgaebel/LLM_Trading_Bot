"""Spec 016 AK7 and AK6 (break-even): lab metrics against hand-computed reference values."""

import itertools
import json
import math
import random
import statistics
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import Any

import pytest

from llm_trading_bot.lab import metrics
from llm_trading_bot.lab.metrics import (
    NO_VERDICT,
    RELIABLE,
    benchmark,
    break_even,
    depot_metrics,
    fifo,
    market_cells,
    quality,
    summarize,
    uncertainty,
    verdict,
    vol_terciles,
)
from llm_trading_bot.lab.model import (
    DayPoint,
    DepotResult,
    MarketQuality,
    Quarter,
    QuarterResult,
    Trade,
    Variant,
)

Q = Quarter.parse("2022Q1")
BTC, ETH = "BTC-EUR", "ETH-EUR"


def trade(
    ts: int,
    side: str,
    qty: float,
    notional: float,
    fee: float,
    *,
    market: str = BTC,
    agent: str = "trend",
    **context: Any,
) -> Trade:
    return Trade(Q.start + ts, agent, market, side, qty, notional / qty, notional, fee, context)


def fitted(depots: dict[str, DepotResult]) -> Quarter:
    """2022Q1 cut to end after the depots' last day point, so a short hand-made series is a
    complete quarter (metrics flags missing days up to the quarter's end)."""
    last = max((p.day for d in depots.values() for p in d.days), default="2022-01-01")
    n = (date.fromisoformat(last) - date(2022, 1, 1)).days + 1
    return Quarter(Q.label, Q.start, Q.start + max(n, 1) * 86_400)


def result(
    depots: dict[str, DepotResult],
    *,
    quarter: Quarter | None = None,
    daily_mid: dict[str, list[tuple[str, float]]] | None = None,
    spread: dict[str, float] | None = None,
    variant: Variant | None = None,
) -> QuarterResult:
    return QuarterResult(
        quarter or fitted(depots),
        variant or Variant(),
        depots=depots,
        quality={m: MarketQuality(m, 100, 100) for m in (BTC, ETH)},
        daily_mid=daily_mid or {},
        spread=spread or {},
    )


def days(values: list[float], weights: list[dict[str, float]]) -> list[DayPoint]:
    return [
        DayPoint(f"2022-01-{i + 1:02d}", v, v * (1 - sum(w.values())), w)
        for i, (v, w) in enumerate(zip(values, weights, strict=True))
    ]


# --- FIFO --------------------------------------------------------------------------------


def test_fifo_cases_from_arena() -> None:
    # Arena test_fifo_realized_profit_allocates_buy_and_sell_fees: 0.5 * (118.8 - 101) = 8.9.
    a = fifo([trade(0, "BUY", 1, 100, 1), trade(60, "SELL", 0.5, 60, 0.6)])
    assert a.realized_pnl == pytest.approx(8.9)
    assert a.open_lots[("trend", BTC)].quantity == 0.5
    assert a.open_lots[("trend", BTC)].cost_basis == pytest.approx(50.5)
    (only,) = a.round_trips
    assert [only.qty, only.cost, only.proceeds] == pytest.approx([0.5, 50.5, 59.4])
    assert a.round_trips[0].net_return == pytest.approx(8.9 / 50.5)
    assert not a.opening_inventory_unknown and a.sell_events == 1

    # Arena test_missing_opening_trade_does_not_fabricate_realized_profit.
    b = fifo([trade(60, "SELL", 1, 100, 1)])
    assert b.realized_pnl is None and b.opening_inventory_unknown and not b.round_trips

    # Partial fill over two lots: units 101, 121.2; sell unit (165 - 1.65) / 1.5 = 108.9.
    c = fifo(
        [
            trade(0, "BUY", 1, 100, 1, regime="TREND"),
            trade(60, "BUY", 1, 120, 1.2, regime="RANGE"),
            trade(120, "SELL", 1.5, 165, 1.65),
        ]
    )
    first, second = c.round_trips
    assert [first.qty, first.cost, first.proceeds, first.pnl] == pytest.approx([1, 101, 108.9, 7.9])
    assert [second.qty, second.cost, second.proceeds, second.pnl] == pytest.approx(
        [0.5, 60.6, 54.45, -6.15]
    )
    assert [first.context, second.context] == [{"regime": "TREND"}, {"regime": "RANGE"}]
    assert [t.opened - Q.start for t in c.round_trips] == [0, 60]
    assert c.realized_pnl == pytest.approx(1.75)
    assert c.open_lots[("trend", BTC)].quantity == pytest.approx(0.5)
    assert c.open_lots[("trend", BTC)].cost_basis == pytest.approx(60.6)

    # Lots are per agent and market; the drawdown stop's RISK_SELL closes like a SELL.
    d = fifo(
        [
            trade(0, "BUY", 1, 100, 1),
            trade(0, "BUY", 2, 20, 0.2, market=ETH),
            trade(5, "RISK_SELL", 2, 18, 0.18, market=ETH),
            trade(9, "SELL", 1, 100, 1, agent="meanrev"),
        ]
    )
    assert d.realized_pnl is None and d.opening_inventory_unknown
    assert [(t.market, t.agent) for t in d.round_trips] == [(ETH, "trend")]
    assert d.round_trips[0].pnl == pytest.approx(17.82 - 20.2)
    # Only open positions are listed: ETH is closed, meanrev never held anything.
    assert list(d.open_lots) == [("trend", BTC)] and d.open_lots[("trend", BTC)].quantity == 1

    with pytest.raises(ValueError, match="side"):
        fifo([trade(0, "SHORT", 1, 100, 1)])
    with pytest.raises(ValueError, match="quantity"):
        fifo([Trade(Q.start, "trend", BTC, "BUY", 0.0, 1.0, 1.0, 0.0)])


# --- per depot ---------------------------------------------------------------------------


def test_depot_metrics_hand_case() -> None:
    v = Variant()
    s = v.slippage
    trades = [trade(0, "BUY", 0.1, 4000, 10), trade(86_400, "SELL", 0.1, 4100, 10.25)]
    end = 10_000 - 4000 - 10 + 4100 - 10.25
    depot = DepotResult(
        "trend",
        days([9_900.0, 10_200.0, end], [{BTC: 0.4}, {BTC: 0.0}, {BTC: 0.0}]),
        trades,
        end_mid=end,
        end_liquidated=end,
    )
    m = depot_metrics(result({"trend": depot}), "trend")
    slip = 4000 * s / (1 + s) + 4100 * s / (1 - s)
    assert m.net_return_mid == pytest.approx(end / 10_000 - 1)
    assert m.fees == pytest.approx(20.25)
    assert m.slippage == pytest.approx(slip)
    assert m.gross_pnl == pytest.approx(end - 10_000 + 20.25 + slip)
    assert m.turnover == pytest.approx(8100 / 10_000)
    assert m.roundtrip_volume == pytest.approx(4050)
    assert m.round_trips == 1 and m.trades == 2
    assert m.drawdown_basis == "Tagesende"
    assert m.realized_pnl == pytest.approx(4100 - 10.25 - 4010)
    # Peak 10,200 on day 2, then 10,079.75.
    assert m.max_drawdown == pytest.approx(1 - end / 10_200)
    assert m.mean_quote == pytest.approx(0.4 / 3)
    assert m.mean_weights == {BTC: pytest.approx(0.4 / 3)}
    assert m.graded and m.days == 3


def test_full_loss_is_a_valid_drawdown() -> None:
    assert metrics.max_drawdown([10_000, 0]) == 1.0
    assert metrics.max_drawdown([10_000, 11_000, 9_900, 12_000]) == pytest.approx(0.1)
    with pytest.raises(ValueError):
        metrics.max_drawdown([10_000, -1])


def test_benchmark_formula() -> None:
    v = Variant(fee_rate=0.0025, slippage=0.0005)
    depot = DepotResult(
        "trend",
        days(
            [10_000.0, 10_000.0],
            [{BTC: 0.4, ETH: 0.1}, {BTC: 0.2, ETH: 0.1}],
        ),
        end_mid=10_000.0,
    )
    # The first daily_mid entry is the base (the quarter's first round, previous day's label).
    mids = {
        BTC: [("2021-12-31", 100.0), ("2022-01-01", 110.0), ("2022-01-02", 99.0)],
        ETH: [("2021-12-31", 10.0), ("2022-01-01", 10.0), ("2022-01-02", 12.0)],
    }
    spread = {BTC: 0.0002, ETH: 0.0006}
    b = benchmark(result({"trend": depot}, daily_mid=mids, spread=spread, variant=v), "trend")
    # ē_BTC = (0.4 + 0.2) / 2 = 0.3, ē_ETH = 0.1.
    assert b.exposure == {BTC: pytest.approx(0.3), ETH: pytest.approx(0.1)}
    assert b.days == ["2022-01-01", "2022-01-02"]
    # Day 1: 0.3 * 0.10 + 0.1 * 0.0 = 0.03; day 2: 0.3 * -0.10 + 0.1 * 0.20 = -0.01.
    assert b.returns == [pytest.approx(0.03), pytest.approx(-0.01)]
    assert b.total == pytest.approx(1.03 * 0.99 - 1)
    c_btc, c_eth = 0.003 + 0.0001, 0.003 + 0.0003
    assert b.cost_per_side == {BTC: pytest.approx(c_btc), ETH: pytest.approx(c_eth)}
    entry = 0.3 * c_btc + 0.1 * c_eth
    # Rebalancing trades ē_m * |r_m - R|: day 1 0.3*0.07, 0.1*0.03; day 2 0.3*0.09, 0.1*0.21.
    day1 = 0.03 - entry - (c_btc * 0.3 * 0.07 + c_eth * 0.1 * 0.03)
    day2 = -0.01 - (c_btc * 0.3 * 0.09 + c_eth * 0.1 * 0.21)
    assert b.returns_cost == [pytest.approx(day1), pytest.approx(day2)]
    assert b.total_cost == pytest.approx((1 + day1) * (1 + day2) - 1)
    assert b.cash == 0.0

    m = depot_metrics(result({"trend": depot}, daily_mid=mids, spread=spread, variant=v), "trend")
    assert m.benchmark == pytest.approx(b.total)
    assert m.excess == pytest.approx(0.0 - b.total_cost)
    # AK7 asks for both: the excess over buy & hold without its rebalancing costs as well.
    assert m.excess_no_cost == pytest.approx(0.0 - b.total)
    assert m.excess_no_cost < m.excess


def test_benchmark_without_exposure_is_cash() -> None:
    depot = DepotResult("trend", days([10_000.0], [{}]))
    mids = {BTC: [("2021-12-31", 100.0), ("2022-01-01", 110.0)]}
    b = benchmark(result({"trend": depot}, daily_mid=mids), "trend")
    assert b.exposure == {} and b.returns == [0.0] and b.total_cost == 0.0


# --- uncertainty -------------------------------------------------------------------------

RETURNS = [0.01, -0.02, 0.015, 0.003, -0.007, 0.02, -0.01, 0.004, 0.0, 0.012, -0.005, 0.008]
BTC_RETURNS = [0.02, -0.03, 0.01, 0.0, -0.01, 0.03, -0.02, 0.01, 0.005, 0.02, -0.01, 0.0]


def _bootstrap_quarter() -> QuarterResult:
    values, v = [], 10_000.0
    for r in RETURNS:
        v *= 1 + r
        values.append(v)
    mids, mid = [("2021-12-31", 100.0)], 100.0
    for i, r in enumerate(BTC_RETURNS):
        mid *= 1 + r
        mids.append((f"2022-01-{i + 1:02d}", mid))
    depot = DepotResult("trend", days(values, [{BTC: 0.5}] * len(values)))
    return result({"trend": depot}, daily_mid={BTC: mids}, variant=Variant(slippage=0.0))


def _independent_bootstrap(xs: list[float], draws: int, block: int, seed: int) -> list[float]:
    """Block-wise formulation of the stationary bootstrap (same integer draws, other code)."""
    rng, n, out = random.Random(seed), len(xs), []
    for _ in range(draws):
        idx: list[int] = []
        start = rng.randrange(n)
        while True:
            length = 1
            while len(idx) + length < n and rng.randrange(block) != 0:
                length += 1
            idx.extend((start + j) % n for j in range(length))
            if len(idx) >= n:
                break
            start = rng.randrange(n)
        out.append(statistics.fmean(xs[i] for i in idx) * 365)
    return out


def test_bootstrap_reference_values(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: object) -> float:
        raise AssertionError("only integer draws are platform-identical")

    # LEARNING "random.gauss rechnet je Plattform anders": integer draws only.
    monkeypatch.setattr(random.Random, "random", forbidden)
    monkeypatch.setattr(random.Random, "gauss", forbidden)
    q = _bootstrap_quarter()
    u = uncertainty([q], "trend", draws=400, mean_block=3, seed=7)
    assert u.days == 12 and u.draws == 400

    # Point estimates by hand: mean * 365 and mean / stdev * sqrt(365).
    assert u.mean_return.estimate == pytest.approx(statistics.fmean(RETURNS) * 365)
    sharpe = statistics.fmean(RETURNS) / statistics.stdev(RETURNS) * math.sqrt(365)
    assert u.sharpe.estimate == pytest.approx(sharpe)
    # Benchmark by hand: ē = 0.5 in one market, cost 0.25 % per side (no slippage, no spread);
    # rebalancing trades 0.5 * |r - 0.5 r| = 0.25 |r|, day 1 also pays the entry 0.5 * c.
    c = 0.0025
    bench = [
        0.5 * x - c * 0.25 * abs(x) - (c * 0.5 if i == 0 else 0.0)
        for i, x in enumerate(BTC_RETURNS)
    ]
    excess = [r - x for r, x in zip(RETURNS, bench, strict=True)]
    assert u.excess.estimate == pytest.approx(statistics.fmean(excess) * 365)
    excess0 = [r - 0.5 * x for r, x in zip(RETURNS, BTC_RETURNS, strict=True)]
    assert u.excess_no_cost.estimate == pytest.approx(statistics.fmean(excess0) * 365)

    # Band from an independent implementation with the same seed.
    monkeypatch.undo()
    ref = sorted(_independent_bootstrap(RETURNS, 400, 3, 7))
    cuts = statistics.quantiles(ref, n=40, method="inclusive")
    assert u.mean_return.low == pytest.approx(cuts[0], rel=1e-12)
    assert u.mean_return.high == pytest.approx(cuts[38], rel=1e-12)
    assert u.mean_return.median == pytest.approx(statistics.median(ref), rel=1e-12)

    # Pinned with seed 7 (would change only if the draw order changed).
    assert [u.mean_return.low, u.mean_return.high] == pytest.approx(PINNED_MEAN, rel=1e-12)
    assert [u.sharpe.low, u.sharpe.high] == pytest.approx(PINNED_SHARPE, rel=1e-12)
    # Twelve days cannot rule out chance: the band contains 0.
    assert u.mean_return.compatible_with_chance and metrics.chance_text(u.mean_return) == "ja"
    sure = metrics.Band(1.0, 0.2, 1.8, 1.0, False)
    assert metrics.chance_text(sure) == "nein"
    again = uncertainty([q], "trend", draws=400, mean_block=3, seed=7)
    assert again == u


PINNED_MEAN = [-0.5193645833333496, 2.0379166666666513]
PINNED_SHARPE = [-2.1084968688029297, 11.643460464928605]


def test_stationary_indices_mean_block() -> None:
    rng = random.Random(1)
    idx = metrics.stationary_indices(20_000, 7, rng)
    jumps = sum(1 for a, b in itertools.pairwise(idx) if b != (a + 1) % 20_000)
    # Mean block ≈ 7 (a jump can land on the next day by chance, 1 / n).
    assert 20_000 / (jumps + 1) == pytest.approx(7, rel=0.05)


def test_too_few_days() -> None:
    q = result({"trend": DepotResult("trend", days([10_000.0], [{}]))})
    with pytest.raises(metrics.TooFewDays):
        uncertainty([q], "trend")
    assert summarize([q]).uncertainty == {}


# --- market-phase cells ------------------------------------------------------------------


def test_cells_use_past_only() -> None:
    # Six earlier vols 1..6; thresholds at quantiles 1/3 and 2/3.
    history = [(t, BTC, float(t)) for t in range(1, 7)]
    labels = vol_terciles(history, [(7, BTC, 2.5), (4, BTC, 2.5), (4, ETH, 2.5)], 3)
    # At ts 7: [1..6] -> 2.667 / 4.333 -> low. At ts 4: only [1, 2, 3] (ts < 4, not ts 4
    # itself) -> 1.667 / 2.333 -> high. ETH has no history of its own -> n/a.
    assert labels == ["low", "high", "n/a"]

    # Changing the future never changes an earlier label.
    future = [*history, (5, BTC, 1000.0), (8, BTC, -50.0)]
    assert vol_terciles(future, [(4, BTC, 2.5)], 3) == ["high"]
    # The same timestamp is not "before": a trade does not see its own bar.
    assert vol_terciles([(4, BTC, 0.0)] * 5, [(4, BTC, 0.0)], 3) == ["n/a"]

    # Through the round trips: the cell comes from the opening trade's context.
    trades = []
    for k in range(6):
        trades.append(
            trade(k * 100, "BUY", 1, 100, 0, vol=float(k + 1), regime="TREND", direction=0.01)
        )
        trades.append(trade(k * 100 + 50, "SELL", 1, 101 + k, 0, vol=99.0))
    q = result({"trend": DepotResult("trend", trades=trades)})
    cells = market_cells([q], "trend", min_history=3)
    by_key = {(c.vol, c.direction): c for c in cells}
    # Entries 1 and 2 have fewer than 3 earlier vols; later ones only see their past.
    assert by_key[("n/a", "up")].count == 2
    assert sum(c.count for c in cells) == 6 and all(c.few for c in cells)
    assert all(c.regime == "TREND" for c in cells)
    # Entry k sees k buys (vol 1..k) and k sells (vol 99): entry 2 -> [1, 2, 99, 99] gives
    # 2 / 99 -> mid; entry 3 -> [1, 2, 3, 99, 99, 99] gives 2.67 / 99 -> mid; likewise 4, 5.
    assert by_key[("mid", "up")].count == 4
    # Net return per round trip (101 + k - 100) / 100 without fees.
    assert by_key[("mid", "up")].mean == pytest.approx((0.03 + 0.04 + 0.05 + 0.06) / 4)
    assert by_key[("n/a", "up")].mean == pytest.approx((0.01 + 0.02) / 2)


def test_cell_band_and_flag() -> None:
    rets = [0.01, -0.02, 0.03]
    trades = []
    for k, r in enumerate(rets):
        trades.append(trade(k * 10, "BUY", 1, 100, 0, regime="RANGE"))
        trades.append(trade(k * 10 + 5, "SELL", 1, 100 * (1 + r), 0))
    q = result({"trend": DepotResult("trend", trades=trades)})
    (cell,) = market_cells([q], "trend")
    # Student's t with 2 degrees of freedom, not the normal 1.96: three trips are few.
    half = 4.302652729749464 * statistics.stdev(rets) / math.sqrt(3)
    assert (cell.regime, cell.vol, cell.direction) == ("RANGE", "n/a", "n/a")
    assert cell.mean == pytest.approx(statistics.fmean(rets))
    assert [cell.low, cell.high] == pytest.approx([cell.mean - half, cell.mean + half])
    assert cell.few and cell.pnl == pytest.approx(2.0)


def test_cell_with_one_round_trip_has_no_band() -> None:
    # Review evidence: one trip gave "-3,71 % [-3,71 % bis -3,71 %]", a band claiming certainty.
    trades = [trade(0, "BUY", 1, 100, 0, regime="RANGE"), trade(5, "SELL", 1, 96.29, 0)]
    (cell,) = market_cells([result({"trend": DepotResult("trend", trades=trades)})], "trend")
    assert cell.count == 1 and cell.mean == pytest.approx(-0.0371)
    assert (cell.low, cell.high) == (None, None)


def _t_cdf(t: float, df: int, steps: int = 20_000) -> float:
    """Student's t distribution function by Simpson's rule over the density (reference)."""
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)

    def density(x: float) -> float:
        return c * math.pow(1 + x * x / df, -(df + 1) / 2)

    h = t / steps
    inner = sum((4 if i % 2 else 2) * density(i * h) for i in range(1, steps))
    return 0.5 + (density(0) + density(t) + inner) * h / 3


@pytest.mark.parametrize("df", [*range(1, 32), 45, 100, 1000])
def test_t95_is_the_975_quantile(df: int) -> None:
    assert _t_cdf(metrics.t95(df), df) == pytest.approx(0.975, abs=1e-8)
    assert metrics.t95(df) > metrics.Z95


def test_t95_refuses_zero_degrees_of_freedom() -> None:
    with pytest.raises(ValueError):
        metrics.t95(0)


# --- break-even (AK6) --------------------------------------------------------------------


def test_break_even_fixed_decisions() -> None:
    v = Variant()  # fee 0.25 %, slippage 0.05 % per side -> model 0.6 % per round trip
    s = v.slippage
    trades = [trade(0, "BUY", 0.1, 4000, 10), trade(60, "SELL", 0.1, 4100, 10.25)]
    end = 10_000 - 4000 - 10 + 4100 - 10.25
    depot = DepotResult("trend", days([end], [{}]), trades, end_mid=end, end_liquidated=end)
    q = result({"trend": depot}, spread={BTC: 0.0004}, variant=v)
    be = break_even([q], "trend")

    gross = (end - 10_000) + 20.25 + 4000 * s / (1 + s) + 4100 * s / (1 - s)
    volume = (4000 + 4100) / 2
    assert be.gross_pnl == pytest.approx(gross)
    assert be.roundtrip_volume == pytest.approx(volume)
    assert be.model_cost == pytest.approx(0.006)
    assert be.break_even == pytest.approx(gross / volume)
    assert be.net(be.break_even or 0.0) == pytest.approx(0.0, abs=1e-9)
    # At the model cost the formula gives the run's own net result (up to slippage^2 terms).
    assert be.net(be.model_cost) == pytest.approx(end - 10_000, abs=0.01)
    assert be.spread == pytest.approx(0.0004)
    assert be.break_even_total == pytest.approx(gross / volume + 0.0004)
    assert be.ratio == pytest.approx((gross / volume + 0.0004) / 0.0064)

    # Two quarters pool their gross and volume; ungraded quarters stay out.
    other = Quarter.parse("2022Q2")
    q2 = result({"trend": depot}, quarter=other, spread={BTC: 0.0004}, variant=v)
    pooled = break_even([q, q2], "trend")
    assert pooled.quarters == 2 and pooled.break_even == pytest.approx(gross / volume)
    early = result({"trend": depot}, quarter=Quarter.parse("2021Q1"), variant=v)
    assert break_even([early], "trend").break_even is None

    # A loss before any cost: negative break-even. No trades: none.
    loss = DepotResult("trend", days([9_000.0], [{}]), trades, end_mid=9_000.0)
    assert (break_even([result({"trend": loss}, variant=v)], "trend").break_even or 0) < 0
    idle = DepotResult("trend", days([10_000.0], [{}]), end_mid=10_000.0)
    assert break_even([result({"trend": idle})], "trend").break_even is None


# --- quality, labels, summary ------------------------------------------------------------


def test_quality_summary() -> None:
    # Shaped like a backtest result: codes count minute rounds, due counts strategy bars.
    q = QuarterResult(
        Q,
        Variant(),
        quality={
            BTC: MarketQuality(BTC, 200, 190, {"gap_1h": 600, "stale": 400}, 120, rounds=2_000),
            ETH: MarketQuality(ETH, 200, 170, {"gap_1m": 300}, 0, rounds=2_000),
        },
        rounds=2_000,
    )
    q2 = QuarterResult(
        Quarter.parse("2022Q2"),
        Variant(),
        quality={BTC: MarketQuality(BTC, 100, 100, {"stale": 10}, rounds=1_000)},
        rounds=1_000,
    )
    rows = {(r.quarter, r.market): r for r in quality([q2, q])}
    btc = rows[("2022Q1", BTC)]
    assert btc.share == pytest.approx(0.95) and btc.eligible is True
    assert btc.rounds == 2_000
    # Over the market's rounds, never over due bars (that gave 300 % and 200 % here).
    assert btc.no_decision_share == {"gap_1h": 0.3, "stale": 0.2}
    assert btc.blocked_hours == 2.0
    assert rows[("2022Q1", ETH)].eligible is False
    total = rows[("all", BTC)]
    assert (total.due, total.decided, total.no_decision, total.eligible) == (
        300,
        290,
        {"gap_1h": 600, "stale": 410},
        None,
    )
    assert total.rounds == 3_000
    assert total.no_decision_share == {"gap_1h": 0.2, "stale": pytest.approx(410 / 3_000)}
    assert all(0 <= x <= 1 for r in rows.values() for x in r.no_decision_share.values())


def test_quality_of_an_older_result_uses_the_quarter_rounds() -> None:
    # Recorded before MarketQuality.rounds existed: every round reports each market.
    old = QuarterResult(Q, Variant(), quality={BTC: MarketQuality(BTC, 50, 40, {"stale": 30})})
    old.rounds = 600
    (row, total) = quality([old])
    assert row.rounds == total.rounds == 600
    assert row.no_decision_share == {"stale": 0.05}


def test_labels_by_round_trips() -> None:
    assert [verdict(n) for n in (0, 99, 100, 300, 301)] == [
        NO_VERDICT,
        NO_VERDICT,
        "indikativ",
        "indikativ",
        RELIABLE,
    ]


def test_summary_is_json_friendly_and_refuses_mixed_variants() -> None:
    q = _bootstrap_quarter()
    bh = DepotResult("buyhold", q.depots["trend"].days)
    q.depots["buyhold"] = bh
    s = summarize([q], draws=50)
    assert set(s.uncertainty) == {"buyhold", "trend"}
    assert s.round_trips == {"buyhold": 0, "trend": 0}
    assert s.labels == {"buyhold": NO_VERDICT, "trend": NO_VERDICT}
    assert json.loads(json.dumps(asdict(s), allow_nan=False))["variant"] == "base"
    other = result({}, quarter=Quarter.parse("2022Q2"), variant=Variant(name="fee x2"))
    with pytest.raises(ValueError, match="mix variants"):
        summarize([q, other])
    assert s.data_issues == [] and s.uncertainty_missing == {}


# --- wave-1 review: open lots, daily_mid convention, data gaps, drawdown basis ------------


def test_open_lots_per_agent_and_market() -> None:
    # Review finding: keyed by market alone, zeta's empty queue overwrote alpha's open lot.
    f = fifo(
        [
            trade(0, "BUY", 1, 100, 1, agent="alpha"),
            trade(5, "BUY", 2, 20, 0.2, market=ETH, agent="zeta"),
            trade(9, "SELL", 1, 100, 1, agent="zeta"),
        ]
    )
    assert list(f.open_lots) == [("alpha", BTC), ("zeta", ETH)]
    assert f.open_lots[("alpha", BTC)] == metrics.OpenLots(1, 101)
    assert f.open_lots[("zeta", ETH)].quantity == 2
    assert f.open_lots[("zeta", ETH)].cost_basis == pytest.approx(20.2)
    assert f.opening_inventory_unknown  # zeta sold BTC it never bought
    closed = fifo([trade(0, "BUY", 1, 100, 1), trade(9, "SELL", 1, 110, 1)])
    assert closed.open_lots == {}


FREE = Variant(fee_rate=0.0, slippage=0.0)


def test_daily_mid_first_entry_is_the_base_labelled_the_previous_day() -> None:
    depot = DepotResult("trend", days([10_100.0, 10_000.0], [{BTC: 1.0}] * 2), end_mid=10_000.0)
    rows = [("2021-12-31", 100.0), ("2022-01-01", 101.0), ("2022-01-02", 100.0)]
    b = benchmark(result({"trend": depot}, daily_mid={BTC: rows}, variant=FREE), "trend")
    # The base (quarter's first round, labelled 2021-12-31) is never a benchmark day itself;
    # it only gives day 1 its return.
    assert b.days == ["2022-01-01", "2022-01-02"]
    assert b.returns == [pytest.approx(0.01), pytest.approx(100 / 101 - 1)]
    assert b.issues == []

    # Without the base, day 1 has no return: left out, but flagged instead of dropped silently.
    q = result({"trend": depot}, daily_mid={BTC: rows[1:]}, variant=FREE)
    short = benchmark(q, "trend")
    assert short.days == ["2022-01-02"]
    (issue,) = short.issues
    assert (issue.quarter, issue.agent, issue.market) == ("2022Q1", "trend", BTC)
    assert (issue.missing_days, issue.depot_days) == (["2022-01-01"], 2)
    assert issue.text == (
        "2022Q1 trend: Tageskurs BTC-EUR ohne Vortag an 1 von 2 Tagen (2022-01-01); "
        "Benchmark und Unsicherheit ohne diese Tage"
    )
    m = depot_metrics(q, "trend")
    assert not m.benchmark_complete and m.benchmark_days == 1 and m.data_issues == [issue]
    full = depot_metrics(result({"trend": depot}, daily_mid={BTC: rows}, variant=FREE), "trend")
    assert full.benchmark_complete and full.benchmark_days == 2 and full.data_issues == []


def test_gap_in_daily_mid_is_never_booked_as_one_day() -> None:
    depot = DepotResult("trend", days([10_000.0] * 3, [{BTC: 1.0}] * 3))
    # 2022-01-02 is missing: the +21 % from 01-01 to 01-03 spans two days.
    rows = [("2021-12-31", 100.0), ("2022-01-01", 100.0), ("2022-01-03", 121.0)]
    b = benchmark(result({"trend": depot}, daily_mid={BTC: rows}, variant=FREE), "trend")
    assert b.days == ["2022-01-01"] and b.returns == [0.0] and b.total == 0.0
    (issue,) = b.issues
    assert issue.missing_days == ["2022-01-02", "2022-01-03"]
    assert "an 2 von 3 Tagen (2022-01-02 bis 2022-01-03)" in issue.text

    # Broken input is an error, not a gap.
    bad_rows: list[tuple[list[tuple[str, float]], str]] = [
        ([("2022-01-01", 100.0), ("2022-01-01", 101.0)], "increase"),
        ([("2022-01-02", 100.0), ("2022-01-01", 101.0)], "increase"),
        ([("2021-12-31", 100.0), ("2022-01-01", 0.0)], "mid"),
        ([("2021-12-31", 100.0), ("2022-01-01", math.nan)], "mid"),
    ]
    for bad, match in bad_rows:
        with pytest.raises(ValueError, match=match):
            benchmark(result({"trend": depot}, daily_mid={BTC: bad}), "trend")


def test_gap_in_the_depot_series_is_flagged() -> None:
    points = [
        DayPoint("2022-01-01", 10_000.0, 10_000.0, {}),
        DayPoint("2022-01-03", 9_000.0, 9_000.0, {}),
    ]
    b = benchmark(result({"trend": DepotResult("trend", points)}), "trend")
    # A cash depot needs no daily_mid; its own two-day step is still not a daily return.
    assert b.days == ["2022-01-01"]
    issue, hole = b.issues
    assert (issue.market, issue.missing_days) == (metrics.DEPOT_SERIES, ["2022-01-03"])
    assert "Depotwert ohne Vortag" in issue.text
    # The skipped day itself is named too, not only the day after it.
    assert (hole.kind, hole.missing_days) == (metrics.MISSING, ["2022-01-02"])
    # Day 1 is measured against the start capital, so it must be the quarter's first day.
    late = result({"trend": DepotResult("trend", points[:1])}, quarter=Quarter.parse("2022Q2"))
    outside, absent = benchmark(late, "trend").issues
    assert (outside.kind, outside.missing_days) == (metrics.OUTSIDE, ["2022-01-01"])
    assert absent.kind == metrics.MISSING and len(absent.missing_days) == 91


def _with_depot(q: QuarterResult, agent: str, weights: dict[str, float]) -> QuarterResult:
    values = [p.value for p in q.depots["trend"].days]
    q.depots[agent] = DepotResult(agent, days(values, [weights] * len(values)))
    return q


def test_one_depot_without_mids_keeps_the_other_bands() -> None:
    # Review finding: meanrev holds ETH, daily_mid has BTC only; the intersection over all
    # depots used to drop to 0 days and wipe the bands of every depot.
    q = _with_depot(_with_depot(_bootstrap_quarter(), "meanrev", {ETH: 0.3}), "breakout", {})
    s = summarize([q], draws=50, seed=7)
    alone = uncertainty([_bootstrap_quarter()], "trend", draws=50, seed=7)
    assert replace(s.uncertainty["trend"], group=()) == replace(alone, group=())
    # trend and the cash depot share their days, so they share the draws; meanrev has none.
    assert s.uncertainty["trend"].group == s.uncertainty["breakout"].group == ("breakout", "trend")
    assert alone.group == ("trend",)
    # A cash depot needs no daily_mid and shares trend's days (and index stream).
    assert s.uncertainty["breakout"].days == 12 and s.uncertainty["breakout"].excluded_days == 0
    assert set(s.uncertainty) == {"breakout", "trend"}
    assert s.uncertainty_missing == {
        "meanrev": "keine Bänder: weniger als zwei gewertete Tage mit Benchmark (0)"
    }
    (issue,) = s.data_issues
    assert (issue.agent, issue.market, len(issue.missing_days)) == ("meanrev", ETH, 12)
    assert issue.text.startswith("2022Q1 meanrev: keine Tageskurse für ETH-EUR an 12 von 12")
    complete = {m.agent: m.benchmark_complete for m in s.depots}
    assert complete == {"breakout": True, "meanrev": False, "trend": True}
    with pytest.raises(metrics.TooFewDays, match="got 0"):
        uncertainty([q], "meanrev")


def test_bands_per_depot_over_its_own_valid_days() -> None:
    q = _with_depot(_bootstrap_quarter(), "meanrev", {ETH: 0.3})
    eth = [("2021-12-31", 50.0)] + [(f"2022-01-{i + 1:02d}", 50.0 + i) for i in range(12)]
    q.daily_mid[ETH] = [row for row in eth if row[0] != "2022-01-05"]
    s = summarize([q], draws=50, seed=7)
    # 01-05 is missing and 01-06 has no predecessor: two days out for meanrev only.
    assert (s.uncertainty["meanrev"].days, s.uncertainty["meanrev"].excluded_days) == (10, 2)
    assert (s.uncertainty["trend"].days, s.uncertainty["trend"].excluded_days) == (12, 0)
    assert s.uncertainty["trend"] == uncertainty([_bootstrap_quarter()], "trend", draws=50, seed=7)
    # Other days, other draws: each depot is its own group.
    assert (s.uncertainty["meanrev"].group, s.uncertainty["trend"].group) == (
        ("meanrev",),
        ("trend",),
    )
    (issue,) = s.data_issues
    assert issue.missing_days == ["2022-01-05", "2022-01-06"]


def test_drawdown_basis_is_end_of_day() -> None:
    # The stop fired intraday (halted_at), but only end-of-day values reach the metric.
    depot = DepotResult(
        "trend", days([9_500.0, 9_500.0], [{}, {}]), halted_at=Q.start + 3_600, end_mid=9_500.0
    )
    m = depot_metrics(result({"trend": depot}), "trend")
    assert m.max_drawdown == pytest.approx(0.05) and m.halted_at is not None
    assert m.drawdown_basis == metrics.DRAWDOWN_BASIS == "Tagesende"
    s = summarize([result({"trend": depot})], draws=10)
    assert s.drawdown_basis == "Tagesende"
    assert json.loads(json.dumps(asdict(s), allow_nan=False))["drawdown_basis"] == "Tagesende"


# --- wave-2 review: series against the quarter, duplicates, issue text, spread flag -------


def _series(first: date, n: int, weights: dict[str, float] | None = None) -> list[DayPoint]:
    out = []
    for i in range(n):
        day = (first + timedelta(days=i)).isoformat()
        out.append(DayPoint(day, 10_000.0, 10_000.0, dict(weights or {})))
    return out


def _mids(first: date, n: int) -> list[tuple[str, float]]:
    """The base labelled the day before ``first``, then ``n`` days."""
    return [((first + timedelta(days=i - 1)).isoformat(), 100.0 + i) for i in range(n + 1)]


def test_truncated_series_is_flagged() -> None:
    # Review probe: a full 2022Q1 with day points only for 01-01..01-03 used to read as a
    # complete benchmark, while the bootstrap silently lost days 4 to 90.
    points = _series(date(2022, 1, 1), 3, {BTC: 0.5})
    depot = DepotResult("trend", points, end_mid=15_000.0, end_liquidated=15_000.0)
    q = result({"trend": depot}, quarter=Q, daily_mid={BTC: _mids(date(2022, 1, 1), 31)})
    m = depot_metrics(q, "trend")
    assert not m.benchmark_complete and m.benchmark_days == 3
    (issue,) = m.data_issues
    assert (issue.kind, issue.market, len(issue.missing_days)) == (
        metrics.MISSING,
        metrics.DEPOT_SERIES,
        87,
    )
    assert issue.text == (
        "2022Q1 trend: kein Depotwert an 87 von 90 Tagen (2022-01-04 bis 2022-03-31); "
        "Benchmark und Unsicherheit ohne diese Tage"
    )
    u = uncertainty([q], "trend", draws=20)
    assert (u.days, u.excluded_days) == (3, 87)
    # A depot without any day point misses the whole quarter.
    empty = depot_metrics(result({"trend": DepotResult("trend")}, quarter=Q), "trend")
    (none,) = empty.data_issues
    assert none.kind == metrics.MISSING and none.missing_days == metrics.quarter_days(Q)
    # The last day of the quarter present: complete.
    full = DepotResult("trend", _series(date(2022, 1, 1), 90))
    assert depot_metrics(result({"trend": full}, quarter=Q), "trend").benchmark_complete


def test_days_outside_the_quarter_are_never_graded() -> None:
    # Review probe: 2022-04-01 belongs to 2022Q2 but became a 2022Q1 benchmark day.
    points = [
        DayPoint("2022-03-31", 10_000.0, 10_000.0, {}),
        DayPoint("2022-04-01", 10_100.0, 10_100.0, {}),
    ]
    b = benchmark(result({"trend": DepotResult("trend", points)}, quarter=Q), "trend")
    assert b.days == []
    outside, gap, late = b.issues
    assert (outside.kind, outside.missing_days) == (metrics.OUTSIDE, ["2022-04-01"])
    assert outside.text == (
        "2022Q1 trend: Depotwert außerhalb des Quartals (2022-01-01 bis 2022-03-31) an 1 "
        "Tagen (2022-04-01); diese Tage werden nie gewertet"
    )
    # The real problem is named too: the series starts late, so 01-01 to 03-30 have no point
    # and 03-31 has no predecessor (wave-3 D1: the 89 days used to go unnamed).
    assert (gap.kind, gap.missing_days) == (metrics.GAP, ["2022-03-31"])
    assert (late.kind, late.missing_days) == (metrics.MISSING, metrics.quarter_days(Q)[:-1])
    assert late.text == (
        "2022Q1 trend: kein Depotwert an 89 von 90 Tagen (2022-01-01 bis 2022-03-30); "
        "Benchmark und Unsicherheit ohne diese Tage"
    )

    # A last point spilling into the sealed period never reaches the pooled days.
    q3 = Quarter.parse("2025Q3")
    series = [*_series(date(2025, 7, 1), 92), DayPoint("2025-10-01", 9_000.0, 9_000.0, {})]
    spill = result({"trend": DepotResult("trend", series)}, quarter=q3)
    pooled = metrics.pooled_days([spill], "trend")
    assert len(pooled) == 92 and pooled[-1][0] == "2025-09-30"
    (issue,) = depot_metrics(spill, "trend").data_issues
    assert (issue.kind, issue.missing_days) == (metrics.OUTSIDE, ["2025-10-01"])
    assert uncertainty([spill], "trend", draws=20).excluded_days == 0


def test_days_carried_twice_are_reported_and_left_out() -> None:
    # Review finding: rows keyed by day kept one copy and dropped the other without a word.
    q = _bootstrap_quarter()
    s = summarize([q, q], draws=20)
    dups = [i for i in s.data_issues if i.kind == metrics.DUPLICATE]
    assert len(dups) == 2 and all(len(i.missing_days) == 12 for i in dups)
    assert dups[0].text == (
        "2022Q1 trend: Tag auch in einem anderen gewerteten Ergebnis (2022Q1) an 12 Tagen "
        "(2022-01-01 bis 2022-01-12); Unsicherheit ohne diese Tage"
    )
    assert metrics.pooled_days([q, q], "trend") == []
    assert s.uncertainty == {} and "trend" in s.uncertainty_missing
    # Partly overlapping results: only the shared days go, the rest is still pooled.
    later = Quarter(Q.label + "b", Q.start + 6 * 86_400, Q.start + 18 * 86_400)
    other = result({"trend": DepotResult("trend", _series(date(2022, 1, 7), 12))}, quarter=later)
    pooled = [row[0] for row in metrics.pooled_days([q, other], "trend")]
    assert pooled == [f"2022-01-{i:02d}" for i in (1, 2, 3, 4, 5, 6, *range(13, 19))]
    issues = metrics.pooled_issues([q, other], "trend")
    assert [(i.quarter, i.missing_days[0], i.missing_days[-1]) for i in issues] == [
        ("2022Q1", "2022-01-07", "2022-01-12"),
        ("2022Q1b", "2022-01-07", "2022-01-12"),
    ]


def test_issue_text_lists_separate_blocks() -> None:
    # Review probe: four scattered days read as "2022-01-02 bis 2022-01-10", nine in a row.
    depot = DepotResult("trend", _series(date(2022, 1, 1), 10, {BTC: 1.0}))
    gone = ("2022-01-02", "2022-01-09")
    mids = [row for row in _mids(date(2022, 1, 1), 10) if row[0] not in gone]
    (issue,) = benchmark(result({"trend": depot}, daily_mid={BTC: mids}), "trend").issues
    assert issue.missing_days == ["2022-01-02", "2022-01-03", "2022-01-09", "2022-01-10"]
    blocks = "(2022-01-02 bis 2022-01-03, 2022-01-09 bis 2022-01-10)"
    assert f"an 4 von 10 Tagen {blocks}" in issue.text
    # Single days stay single; a long list is cut after MAX_BLOCKS - 1 blocks.
    every_other = [f"2022-01-{i:02d}" for i in range(1, 20, 2)]
    assert metrics._blocks(every_other[:2]) == "2022-01-01, 2022-01-03"
    assert metrics._blocks(every_other) == (
        "2022-01-01, 2022-01-03, 2022-01-05, 2022-01-07, 2022-01-09, und 5 weitere Abschnitte"
    )


def test_quarter_spread_marks_incomplete_excess() -> None:
    whole = DepotResult("trend", _series(date(2022, 1, 1), 2), end_mid=10_100.0)
    q1 = result({"trend": whole})
    second = Quarter.parse("2022Q2")
    cut = Quarter(second.label, second.start, second.start + 2 * 86_400)
    short = DepotResult("trend", _series(date(2022, 4, 1), 1), end_mid=9_900.0)
    q2 = result({"trend": short}, quarter=cut)
    s = summarize([q1, q2], draws=10)
    spread = s.spreads["trend"]
    assert spread.excess.n == 2 and spread.incomplete == ["2022Q2"]
    assert summarize([q1], draws=10).spreads["trend"].incomplete == []


# --- wave-3 review: every absent day is named, partial days count ----------------------------


def _issue_days(issues: list[metrics.DataIssue]) -> int:
    """Days an issue list takes out of the bootstrap (OUTSIDE labels are no quarter days)."""
    return len({d for i in issues if i.kind != metrics.OUTSIDE for d in i.missing_days})


def test_absent_days_at_start_and_inside_are_named_and_agree_with_excluded_days() -> None:
    # Review probes (wave 3, D1): only days after the last point used to be MISSING, so a late
    # start or a hole read as "1 von 90 Tagen" while the bootstrap lost 60 or 4 days.
    late = _series(date(2022, 3, 1), 31)
    hole = [*_series(date(2022, 1, 1), 1), *_series(date(2022, 1, 5), 86)]
    spill = [DayPoint("2022-03-31", 1.0, 1.0, {}), DayPoint("2022-04-01", 1.0, 1.0, {})]
    cases = [
        (late, "2022-01-01 bis 2022-02-28", 59, 60),
        (hole, "2022-01-02 bis 2022-01-04", 3, 4),
        (spill, "2022-01-01 bis 2022-03-30", 89, 90),
    ]
    for points, blocks, absent, excluded in cases:
        q = result({"trend": DepotResult("trend", points)}, quarter=Q)
        m = depot_metrics(q, "trend")
        assert not m.benchmark_complete
        (missing,) = [i for i in m.data_issues if i.kind == metrics.MISSING]
        assert len(missing.missing_days) == absent
        assert f"an {absent} von 90 Tagen ({blocks})" in missing.text
        s = summarize([q], draws=20)
        unc = s.uncertainty.get("trend")
        lost = unc.excluded_days if unc else 90
        assert lost == excluded == _issue_days(s.data_issues)
    # Holes at start, middle and end together, with an exposed market: still one count.
    scattered = [p for p in _series(date(2022, 1, 1), 90, {BTC: 0.5}) if p.day[5:7] != "02"]
    mids = _mids(date(2022, 1, 1), 90)
    q = result({"trend": DepotResult("trend", scattered[2:-3])}, quarter=Q, daily_mid={BTC: mids})
    s = summarize([q], draws=20)
    (missing,) = [i for i in s.data_issues if i.kind == metrics.MISSING]
    assert missing.missing_days[:2] == ["2022-01-01", "2022-01-02"]
    assert missing.missing_days[-3:] == ["2022-03-29", "2022-03-30", "2022-03-31"]
    assert "2022-02-01 bis 2022-02-28" in missing.text
    # 33 absent days plus the GAP days 01-03 (late start) and 03-01 (after February).
    assert s.uncertainty["trend"].excluded_days == _issue_days(s.data_issues) == 33 + 2


def test_quarter_days_count_a_partial_last_day() -> None:
    # Review probe (wave 3, D2): the partial last day was dropped and an 8-hour span had none,
    # so every point of a short backtest read as outside the quarter.
    start = Q.start + 86_400  # 2022-01-02 00:00 UTC
    assert metrics.quarter_days(Quarter("x", start, start + 86_400 + 3_600)) == [
        "2022-01-02",
        "2022-01-03",
    ]
    short = Quarter("x", start, start + 8 * 3_600)
    assert metrics.quarter_days(short) == ["2022-01-02"]
    assert metrics.quarter_days(Quarter("x", start, start)) == []
    # Midnight to midnight: the end day itself is not part of the span.
    assert metrics.quarter_days(Quarter("x", start, start + 86_400)) == ["2022-01-02"]
    assert len(metrics.quarter_days(Q)) == 90
    # A short span's own day is not flagged as outside and has no absent days.
    point = DayPoint("2022-01-02", 10_100.0, 10_100.0, {})
    m = depot_metrics(result({"trend": DepotResult("trend", [point])}, quarter=short), "trend")
    assert m.benchmark_complete and m.benchmark_days == 1 and m.data_issues == []
