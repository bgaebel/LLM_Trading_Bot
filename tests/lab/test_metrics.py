"""Spec 016 AK7 and AK6 (break-even): lab metrics against hand-computed reference values."""

import itertools
import json
import math
import random
import statistics
from dataclasses import asdict
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


def result(
    depots: dict[str, DepotResult],
    *,
    quarter: Quarter = Q,
    daily_mid: dict[str, list[tuple[str, float]]] | None = None,
    spread: dict[str, float] | None = None,
    variant: Variant | None = None,
) -> QuarterResult:
    return QuarterResult(
        quarter,
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
    assert a.open_lots[BTC].quantity == 0.5
    assert a.open_lots[BTC].cost_basis == pytest.approx(50.5)
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
    assert c.open_lots[BTC].quantity == pytest.approx(0.5)
    assert c.open_lots[BTC].cost_basis == pytest.approx(60.6)

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
    assert d.open_lots[BTC].quantity == 1

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
    half = 1.959963984540054 * statistics.stdev(rets) / math.sqrt(3)
    assert (cell.regime, cell.vol, cell.direction) == ("RANGE", "n/a", "n/a")
    assert cell.mean == pytest.approx(statistics.fmean(rets))
    assert [cell.low, cell.high] == pytest.approx([cell.mean - half, cell.mean + half])
    assert cell.few and cell.pnl == pytest.approx(2.0)


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
    q = QuarterResult(
        Q,
        Variant(),
        quality={
            BTC: MarketQuality(BTC, 200, 190, {"gap_1h": 6, "stale": 4}, 120),
            ETH: MarketQuality(ETH, 200, 170, {"gap_1m": 30}, 0),
        },
    )
    q2 = QuarterResult(
        Quarter.parse("2022Q2"), Variant(), quality={BTC: MarketQuality(BTC, 100, 100)}
    )
    rows = {(r.quarter, r.market): r for r in quality([q2, q])}
    btc = rows[("2022Q1", BTC)]
    assert btc.share == pytest.approx(0.95) and btc.eligible is True
    assert btc.no_decision_share == {"gap_1h": 0.03, "stale": 0.02}
    assert btc.blocked_hours == 2.0
    assert rows[("2022Q1", ETH)].eligible is False
    total = rows[("all", BTC)]
    assert (total.due, total.decided, total.no_decision, total.eligible) == (
        300,
        290,
        {"gap_1h": 6, "stale": 4},
        None,
    )


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
