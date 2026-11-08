"""Spec 016 AK4, AK6 (cost grid) and AK9: quarters through the unchanged live cycle."""

import hashlib
import io
import json
import math
import os
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import Future, as_completed
from dataclasses import replace
from pathlib import Path

import pytest

from lab_backtest_data import DAY, HOUR, T0, build_archive, crash, row, wavy
from llm_trading_bot.lab import backtest
from llm_trading_bot.lab.archive import Archive
from llm_trading_bot.lab.backtest import (
    BASE,
    EXIT_ABORTED,
    EXIT_NOT_GRADED,
    EXIT_OK,
    EXIT_REFUSED,
    blocked_by_higher_interval,
    cli_main,
    cost_grid,
    diagnostic_variant,
    gap_tolerance,
    run_quarter,
    run_quarters,
    same_as_base,
    skipped_as_base,
    stale_interval,
    variants,
)
from llm_trading_bot.lab.model import (
    LEAD_ROWS,
    SEAL_EPOCH,
    MarketQuality,
    Quarter,
    QuarterResult,
    Variant,
)
from llm_trading_bot.lab.register import Register
from llm_trading_bot.lab.spread import DEFAULT_SPREAD
from llm_trading_bot.market import snapshot
from llm_trading_bot.market.models import Candle, MarketDataError, Quote, Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot

MINUTE = 60


class SpyRegister:
    """Records what the orchestrator hands over, and in which process."""

    def __init__(self) -> None:
        self.recorded: list[tuple[int, QuarterResult]] = []
        self.aborted: list[tuple[int, Quarter, Variant, str]] = []

    def record(self, result: QuarterResult) -> object:
        self.recorded.append((os.getpid(), result))
        return None

    def record_aborted(self, quarter: Quarter, variant: Variant, error: str) -> object:
        self.aborted.append((os.getpid(), quarter, variant, error))
        return None


def span(start: int, hours: float, label: str = "2023Q1") -> Quarter:
    return Quarter(label, start, start + int(hours * HOUR))


def outcome(result: QuarterResult) -> str:
    return replace(result, seconds=0.0).to_json()


EMPTY = hashlib.sha256(b"").hexdigest()


def tree_digest(folder: Path) -> dict[str, str]:
    """Every file's content, except what a read-only SQLite reader of a WAL database leaves:
    the shared-memory index (``-shm``, no data) and an EMPTY ``-wal``. A ``-wal`` with content
    would mean someone wrote, so it stays in the digest."""
    out = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name.endswith("-shm"):
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if p.name.endswith("-wal") and digest == EMPTY:
            continue
        out[p.relative_to(folder).as_posix()] = digest
    return out


# ---------------------------------------------------------------- AK4


def test_quarter_needs_lead_rows(tmp_path: Path) -> None:
    short = build_archive(
        tmp_path / "short.db", T0, T0 + 2 * HOUR, lead_of={("ETH-EUR", 240): LEAD_ROWS - 1}
    )
    full = build_archive(tmp_path / "full.db", T0, T0 + 2 * HOUR)
    q = span(T0, 1)

    refused = run_quarter(short, q, BASE)
    assert refused.status == "no_lead"
    assert refused.error == "ETH-EUR 240m: 499 von 500 Kerzen"
    assert refused.rounds == 0 and not refused.depots and not refused.graded

    ran = run_quarter(full, q, BASE)
    assert ran.status == "ok", ran.error
    assert ran.rounds == 60
    assert ran.graded
    assert len(ran.dataset_sha256) == 64
    # The first round already decides on full windows: every due bar was decided.
    assert all(m.share == 1.0 for m in ran.quality.values())


def test_preload_counts_rows_not_intervals(tmp_path: Path) -> None:
    # 4h rows missing far before the start: 500 rows then reach further back, as live.
    gone = {T0 - 4 * HOUR * k for k in range(100, 140)}
    path = build_archive(
        tmp_path / "c.db",
        T0,
        T0 + 2 * HOUR,
        lead_of={("BTC-EUR", 240): LEAD_ROWS + 40},
        skip={("BTC-EUR", 240): gone},
    )
    q = span(T0, 1)
    with Archive.open_readonly(path) as arch:
        pre, problems = backtest._preload(arch, q)
    assert not problems
    step = 4 * HOUR
    lead = pre.source.rows("BTC-EUR", 240, 0, q.start)
    assert len(lead) == LEAD_ROWS
    assert int(str(lead[0][0])) // 1000 == T0 - (LEAD_ROWS + 40) * step
    assert len(pre.source.rows("ETH-EUR", 240, 0, q.start)) == LEAD_ROWS
    assert pre.source.rows("BTC-EUR", 1, q.end, q.end + DAY) == []  # nothing after the end
    result = run_quarter(path, q, BASE)
    assert result.status == "ok", result.error
    assert result.quality["BTC-EUR"].share == 1.0


def test_unfinished_archive_is_not_run(tmp_path: Path) -> None:
    path = build_archive(tmp_path / "c.db", T0, T0 + 2 * HOUR)
    with Archive(path) as arch:
        # The next 1m window is still open (fetched before its end): rows may still come.
        w = T0 + DAY
        arch.store_window("BTC-EUR", 1, w, [], fetched_at=w + HOUR)
    result = run_quarter(path, Quarter("2023Q1", T0, T0 + DAY + HOUR), BASE)
    assert result.status == "incomplete"
    assert "BTC-EUR 1m" in result.error
    assert result.rounds == 0


def test_eligibility_90_percent(tmp_path: Path) -> None:
    q = span(T0, 4)

    def with_share(decided: int) -> QuarterResult:
        quality = {
            "BTC-EUR": MarketQuality("BTC-EUR", due=100, decided=100),
            "ETH-EUR": MarketQuality("ETH-EUR", due=100, decided=decided),
        }
        return QuarterResult(q, BASE, quality=quality)

    assert with_share(90).graded
    assert not with_share(89).graded
    assert with_share(89).eligible == {"BTC-EUR": True, "ETH-EUR": False}
    full = {m: MarketQuality(m, due=10, decided=10) for m in ("BTC-EUR", "ETH-EUR")}
    early = QuarterResult(span(Quarter.parse("2021Q1").start, 1), BASE, quality=full)
    assert not early.graded  # BTC 15m only from 2021-02-20: graded from 2021Q2 on

    # Three missing ETH minutes are more than the live 1m filler bridges: ETH gets no snapshot
    # while the hole is among its newest 60 bars, so several 15m bars stay undecided.
    hole = {T0 + 30 * MINUTE + k * MINUTE for k in range(3)}
    path = build_archive(tmp_path / "c.db", T0, T0 + 5 * HOUR, skip={("ETH-EUR", 1): hole})
    result = run_quarter(path, q, BASE)
    assert result.status == "ok", result.error
    btc, eth = result.quality["BTC-EUR"], result.quality["ETH-EUR"]
    assert btc.due == eth.due == 3 * 16 + 4  # three 15m depots and trend (1h), 4 hours
    assert btc.share == 1.0
    assert eth.share < 0.9
    assert eth.share == eth.decided / eth.due
    assert eth.no_decision.get("gap_1m", 0) > 0
    # While the hole is the newest minutes, the 1m bars are stale: that is no block either.
    assert eth.no_decision.get("stale", 0) > 0
    assert eth.blocked_rounds == 0  # a 1m hole is no higher-interval block
    assert sum(eth.no_decision.values()) <= result.rounds
    assert result.eligible == {"BTC-EUR": True, "ETH-EUR": False}
    assert not result.graded


def test_missing_higher_bar_blocks_its_full_time(tmp_path: Path) -> None:
    # The ETH 1h bar opening at T0+1h is missing. While it would be the newest closed bar the
    # snapshot fails as "stale" (only past the live freshness slack), afterwards as "gap_60m"
    # while the hole is among the newest 60 bars - beyond this 8-hour span. Both are blocks.
    hole = T0 + HOUR
    path = build_archive(tmp_path / "c.db", T0, T0 + 9 * HOUR, skip={("ETH-EUR", 60): {hole}})
    q = span(T0, 8)
    result = run_quarter(path, q, BASE)
    assert result.status == "ok", result.error

    rounds = range(q.start + backtest.OFFSET, q.end, MINUTE)
    newest_ok = hole - HOUR  # the bar before the hole: its close is the newest the 1h has
    stale = [
        t
        for t in rounds
        if t < hole + 2 * HOUR and t - (newest_ok + HOUR) > HOUR + snapshot.FRESHNESS_SLACK
    ]
    gap = [t for t in rounds if t >= hole + 2 * HOUR]  # the bar after the hole has closed
    assert (len(stale), len(gap)) == (58, 300)  # the slack keeps two rounds tradeable

    eth = result.quality["ETH-EUR"]
    assert eth.no_decision == {"gap_60m": len(gap), "stale": len(stale)}  # live codes
    assert eth.blocked_rounds == len(stale) + len(gap)
    assert result.quality["BTC-EUR"].no_decision == {}
    assert result.quality["BTC-EUR"].blocked_rounds == 0

    # The diagnostic tolerance shortens only the gap; the stale hour stays blocked.
    diag = run_quarter(path, q, diagnostic_variant()).quality["ETH-EUR"]
    assert diag.no_decision["stale"] == len(stale)
    assert len(stale) < diag.blocked_rounds < eth.blocked_rounds


def _fresh_bars(tf: int, now: int, drop_newest: int = 0) -> tuple[Candle, ...]:
    step = tf * MINUTE
    opens = range(now - 200 * step, now - drop_newest * step, step)
    return tuple(Candle(ts, 1.0, 1.0, 1.0, 1.0, 1.0) for ts in opens)


def _failure(stale_tf: int | None = None, quote_late: int = 0) -> str:
    """The message the live cycle passes to the observation hook for a failed snapshot."""
    bars = {tf: _fresh_bars(tf, T0, 3 if tf == stale_tf else 0) for tf in (1, 15, 60, 240)}
    snap = MarketSnapshot(
        Symbol.ETHEUR, bars, Quote(1.0, 1.0001), T0, T0 - quote_late, dict.fromkeys(bars, 0)
    )
    with pytest.raises(MarketDataError) as caught:
        snap.validate(now_epoch=T0)
    assert caught.value.code == "stale"
    return f"{type(caught.value).__name__}: {caught.value}"


def test_stale_interval_read_from_the_live_message() -> None:
    # Built through the live validation, so a changed wording there fails here.
    for tf in (15, 60, 240):
        message = _failure(stale_tf=tf)
        assert stale_interval(message) == tf
        assert blocked_by_higher_interval("stale", message)
    one_minute = _failure(stale_tf=1)
    assert stale_interval(one_minute) == 1
    assert not blocked_by_higher_interval("stale", one_minute)
    quote = _failure(quote_late=snapshot.MAX_QUOTE_AGE + 1)
    assert stale_interval(quote) is None
    assert not blocked_by_higher_interval("stale", quote)
    assert blocked_by_higher_interval("gap_240m", None)
    assert not blocked_by_higher_interval("gap_1m", None)
    assert not blocked_by_higher_interval("stale", None)


def test_quarter_end_variants(tmp_path: Path) -> None:
    path = build_archive(tmp_path / "c.db", T0, T0 + 4 * HOUR)
    for variant in (BASE, cost_grid()[-1]):
        result = run_quarter(path, span(T0, 2), variant)
        assert result.status == "ok", result.error
        bh = result.depots["buyhold"]
        last = bh.days[-1]
        assert bh.end_mid == last.value
        # Sold: every holding at bid (mid minus half the spread), minus slippage and fee.
        keep = (1 - variant.slippage) * (1 - variant.fee_rate)
        sold = last.cash + sum(
            w * last.value * (1 - DEFAULT_SPREAD[m] / 2) * keep for m, w in last.weights.items()
        )
        assert math.isclose(bh.end_liquidated, sold, rel_tol=1e-12)
        assert bh.end_liquidated < bh.end_mid
        assert result.spread == DEFAULT_SPREAD
        for agent, depot in result.depots.items():
            if all(w == 0 for w in depot.days[-1].weights.values()):
                assert depot.end_liquidated == depot.end_mid, agent


def test_halt_only_for_its_quarter(tmp_path: Path) -> None:
    start = T0 - 4 * HOUR
    fall = T0 - 2 * HOUR
    path = build_archive(tmp_path / "c.db", start, T0 + 8 * HOUR, price=crash(fall))
    first, second = span(start, 8), span(T0 + 4 * HOUR, 3)
    spy = SpyRegister()
    a, b = run_quarters(path, [first, second], [BASE], workers=1, register=spy)

    trend = a.depots["trend"]
    assert trend.halted_at is not None and fall <= trend.halted_at < fall + HOUR
    assert trend.halted_at % MINUTE == 5
    sides = [t.side for t in trend.trades]
    assert sides[:2] == ["BUY", "BUY"] and "RISK_SELL" in sides
    # After the stop the depot is cash: here the whole next day of the quarter.
    after = [p for p in trend.days if p.day == "2023-01-02"]
    assert after and all(p.cash == p.value and set(p.weights.values()) == {0.0} for p in after)
    assert trend.end_mid == trend.end_liquidated == trend.days[-1].cash
    # The stop does not cost the quarter its grade: a stopped depot owes no decisions.
    assert a.graded, a.quality

    # Past-only context of the buy; the forced sale has no decision behind it.
    buy = trend.trades[0]
    assert set(buy.context) == {"regime", "vol", "direction"}
    assert buy.context["regime"] == "TREND" and buy.context["direction"] > 0
    assert trend.trades[-1].context == {}
    assert a.depots["buyhold"].trades[0].context == {}

    # The next quarter starts fresh: no stop, decisions again, 10,000 EUR.
    fresh = b.depots["trend"]
    assert fresh.halted_at is None
    assert sum(fresh.actions.values()) > 0
    assert fresh.days[0].value == pytest.approx(10_000.0, rel=0.01)
    assert [r.quarter for _, r in spy.recorded] == [first, second]


def test_day_points_and_daily_mid(tmp_path: Path) -> None:
    path = build_archive(tmp_path / "c.db", T0 - 2 * HOUR, T0 + 3 * HOUR)
    q = span(T0 - 2 * HOUR, 4)
    result = run_quarter(path, q, BASE)
    assert result.status == "ok", result.error
    # The quote mid is the close of the last finished minute: row of start - 60 at round 1.
    first_mid = float(row("BTC-EUR", 1, q.start - MINUTE, wavy)[4])
    last_mid = float(row("BTC-EUR", 1, q.end - 2 * MINUTE, wavy)[4])
    end_of_day = float(row("BTC-EUR", 1, T0 - 2 * MINUTE, wavy)[4])
    mids = result.daily_mid["BTC-EUR"]
    assert [d for d, _ in mids] == ["2022-12-31", "2023-01-01", "2023-01-02"]
    assert [m for _, m in mids] == pytest.approx([first_mid, end_of_day, last_mid], rel=1e-12)
    for depot in result.depots.values():
        assert [p.day for p in depot.days] == ["2023-01-01", "2023-01-02"]
        for p in depot.days:
            assert set(p.weights) == {"BTC-EUR", "ETH-EUR"}
            invested = sum(p.weights.values()) * p.value
            assert math.isclose(p.cash + invested, p.value, rel_tol=1e-9)
    actions = result.depots["buyhold"].actions
    assert actions == {"BUY/OK": 2}


def test_sealed_period_refused(tmp_path: Path) -> None:
    missing = tmp_path / "no-archive.db"  # refused before anything is read
    for q in (Quarter.parse("2025Q4"), Quarter("2025Q3", SEAL_EPOCH - HOUR, SEAL_EPOCH + MINUTE)):
        result = run_quarter(missing, q, BASE)
        assert result.status == "sealed"
        assert result.rounds == 0 and not result.depots and result.dataset_sha256 == ""
        assert not result.graded
    assert not Quarter("2025Q3", SEAL_EPOCH - HOUR, SEAL_EPOCH).sealed

    spy = SpyRegister()
    (recorded,) = run_quarters(missing, [Quarter.parse("2026Q1")], [BASE], workers=4, register=spy)
    assert recorded.status == "sealed"
    assert [r.status for _, r in spy.recorded] == ["sealed"]

    out = io.StringIO()
    code = cli_main("backtest", ["--from", "2025Q3", "--to", "2025Q4", "--dir", str(tmp_path)], out)
    assert code == EXIT_REFUSED
    assert "versiegelten Prüfzeitraum" in out.getvalue() and "2025Q4" in out.getvalue()
    assert not (tmp_path / "runs.db").exists()


def test_workers_do_not_write(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    lab.mkdir()
    path = build_archive(lab / "candles.db", T0, T0 + 3 * HOUR)
    before = tree_digest(lab)
    quarters = [span(T0, 0.5), span(T0 + HOUR, 0.5)]
    spy = SpyRegister()
    results = run_quarters(path, quarters, [BASE, cost_grid()[0]], workers=2, register=spy)

    assert tree_digest(lab) == before  # the archive and its folder are untouched
    assert {pid for pid, _ in spy.recorded} == {os.getpid()}
    assert not spy.aborted
    assert [(r.variant.name, r.quarter) for r in results] == [
        (v, q) for v in ("base", "cost-0.3") for q in quarters
    ]
    assert all(r.status == "ok" for r in results)
    # A worker process computes exactly what this process computes.
    inline = run_quarter(path, quarters[0], BASE)
    assert outcome(inline) == outcome(results[0])


def test_crashed_quarter_recorded_as_aborted(tmp_path: Path) -> None:
    path = build_archive(tmp_path / "c.db", T0, T0 + HOUR)
    spy = SpyRegister()
    bad = Quarter("2023Q1", T0 + 1, T0 + 61)  # not on a minute: a caller bug, not data
    (result,) = run_quarters(path, [bad], [BASE], workers=1, register=spy)
    assert result.status == "aborted" and "ValueError" in result.error
    assert [(q, e) for _, q, _, e in spy.aborted] == [(bad, result.error)]
    assert not spy.recorded


class OrderSpy:
    """One log of everything the register gets, in the order it gets it."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str, Quarter]] = []

    def record(self, result: QuarterResult) -> object:
        self.log.append(("record", result.variant.name, result.quarter))
        return None

    def record_aborted(self, quarter: Quarter, variant: Variant, error: str) -> object:
        self.log.append(("aborted", variant.name, quarter))
        return None


def test_register_order_fixed_when_workers_finish_out_of_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_archive(tmp_path / "c.db", T0, T0 + 2 * HOUR)
    good = span(T0, 0.25)
    bad = Quarter("2023Q1", T0 + 1, T0 + 61)  # crashes in its worker (not on a minute)
    sealed = Quarter.parse("2025Q4")

    def last_first(futures: Iterable[Future[QuarterResult]]) -> Iterator[Future[QuarterResult]]:
        # The worst case of a pool: the last submitted quarter finishes first.
        return reversed(list(as_completed(futures)))

    monkeypatch.setattr(backtest, "as_completed", last_first)
    spy = OrderSpy()
    shown: list[tuple[str, Quarter, str]] = []
    two = [BASE, cost_grid()[0]]
    results = run_quarters(
        path,
        [good, bad, sealed],
        two,
        workers=2,
        register=spy,
        progress=lambda r: shown.append((r.variant.name, r.quarter, r.status)),
    )
    expected = [
        (kind, v.name, q)
        for v in two
        for kind, q in (("record", good), ("aborted", bad), ("record", sealed))
    ]
    assert spy.log == expected
    assert [(v, q) for v, q, _ in shown] == [(v, q) for _, v, q in expected]
    assert [r.status for r in results] == ["ok", "aborted", "sealed"] * 2
    assert "ValueError" in results[1].error


# ---------------------------------------------------------------- AK6 cost grid


def test_cost_grid_keeps_the_live_ratio() -> None:
    grid = cost_grid()
    assert [v.name for v in grid] == ["cost-0.3", "cost-0.6", "cost-0.9", "cost-1.2"]
    for v, k in zip(grid, (0.003, 0.006, 0.009, 0.012), strict=True):
        assert v.roundtrip == pytest.approx(k, rel=1e-12)
        assert v.fee_rate == pytest.approx(5 * v.slippage, rel=1e-9)
        assert not v.diagnostic and v.gap_window is None
    assert (grid[1].fee_rate, grid[1].slippage) == (BASE.fee_rate, BASE.slippage)
    with pytest.raises(ValueError):
        variants("cheap")


def test_no_selection_reruns_base_under_another_name() -> None:
    # cost-0.6 has the live costs: it would be base again, counted as one more trial.
    assert same_as_base(cost_grid()[1]) and same_as_base(BASE)
    assert not any(same_as_base(v) for v in (cost_grid()[0], diagnostic_variant()))
    assert [v.name for v in variants("all")] == [
        "base",
        "cost-0.3",
        "cost-0.9",
        "cost-1.2",
        "diagnostic-gap-4",
    ]
    assert [v.name for v in variants("cost-grid")] == ["cost-0.3", "cost-0.9", "cost-1.2"]
    assert [v.name for v in variants("base")] == ["base"]
    for selection in backtest.VARIANT_SETS:
        chosen = variants(selection)
        assert sum(same_as_base(v) for v in chosen) == (BASE in chosen)
    assert skipped_as_base("all") == skipped_as_base("cost-grid") == ["cost-0.6"]
    assert skipped_as_base("base") == skipped_as_base("diagnostic") == []


# ---------------------------------------------------------------- AK9


def _bars(tf: int, n: int, missing: int | None = None) -> tuple[Candle, ...]:
    step = tf * MINUTE
    end = T0 - T0 % step
    return tuple(
        Candle(end - (n - i) * step, 1.0, 1.0, 1.0, 1.0, 1.0)
        for i in range(n)
        if missing is None or i != n - missing
    )


def test_gap_tolerance_only_higher_intervals() -> None:
    def snap(gap_tf: int, back: int) -> MarketSnapshot:
        bars = {tf: _bars(tf, 200, back if tf == gap_tf else None) for tf in (1, 15, 60, 240)}
        filled = dict.fromkeys(bars, 0)
        return MarketSnapshot(Symbol.BTCEUR, bars, Quote(1.0, 1.0001), T0, T0, filled)

    original = MarketSnapshot._validate_bars
    with pytest.raises(MarketDataError, match="gap"):
        snap(60, 10).validate(now_epoch=T0)
    with gap_tolerance(4):
        snap(60, 10).validate(now_epoch=T0)  # ten bars back: outside the 4-bar tolerance
        with pytest.raises(MarketDataError, match="gap"):
            snap(60, 3).validate(now_epoch=T0)
        with pytest.raises(MarketDataError, match="gap"):
            snap(1, 10).validate(now_epoch=T0)  # 1m keeps the live 60 bars
        assert snapshot.GAP_WINDOW == 60  # set only while a higher interval is checked
    assert MarketSnapshot._validate_bars is original
    assert snapshot.GAP_WINDOW == 60
    with pytest.raises(MarketDataError, match="gap"):
        snap(60, 10).validate(now_epoch=T0)


def test_diagnostic_flagged_in_register(tmp_path: Path) -> None:
    # One ETH hour missing: live blocks ETH for 60 hours, the diagnosis for a few.
    path = build_archive(tmp_path / "c.db", T0, T0 + 9 * HOUR, skip={("ETH-EUR", 60): {T0 + HOUR}})
    q = span(T0, 8)
    diag = diagnostic_variant()
    assert diag.diagnostic and diag.gap_window == backtest.DIAGNOSTIC_GAP_WINDOW
    with Register.open(tmp_path / "runs.db", tmp_path / "register.jsonl") as register:
        base, flagged = run_quarters(path, [q], [BASE, diag], workers=1, register=register)
        runs = register.list_runs()
        assert [(r.variant, r.diagnostic) for r in runs] == [
            ("base", False),
            ("diagnostic-gap-4", True),
        ]
        assert register.list_runs(diagnostic=True)[0].config_hash != runs[0].config_hash
        assert register.verify().ok
    chain = [json.loads(line) for line in (tmp_path / "register.jsonl").read_text().splitlines()]
    assert [e["event"]["diagnostic"] for e in chain] == [False, True]

    b, d = base.quality["ETH-EUR"], flagged.quality["ETH-EUR"]
    # Live and diagnostic both block while the missing hour is the newest bar (stale); only
    # the gap after it is what the tolerance shortens.
    assert b.blocked_rounds == b.no_decision["gap_60m"] + b.no_decision["stale"]
    assert d.no_decision["stale"] == b.no_decision["stale"]
    assert d.blocked_rounds < b.blocked_rounds
    assert d.decided > b.decided
    assert base.quality["BTC-EUR"] == flagged.quality["BTC-EUR"]
    assert snapshot.GAP_WINDOW == 60  # restored in this (the running) process


# ---------------------------------------------------------------- CLI


ENGLISH = ("rows", "before", "start", "window", "not complete", "quarter", "sealed period")


def test_cli_reports_each_quarter_in_german(tmp_path: Path) -> None:
    build_archive(tmp_path / "candles.db", T0, T0 + HOUR, lead=5)
    out = io.StringIO()
    args = ["--from", "2021Q2", "--to", "2021Q3", "--dir", str(tmp_path), "--workers", "1"]
    # Nothing graded: the run has nothing for the report, so it is no success.
    assert cli_main("backtest", args, out) == EXIT_NOT_GRADED == 1
    text = out.getvalue()
    lead = "nicht gewertet - zu wenig Kerzen vor Quartalsbeginn (BTC-EUR 1m: 0 von 500 Kerzen"
    assert f"2021Q2 base: {lead} und 7 weitere)\n" in text
    assert f"2021Q3 base: {lead} und 7 weitere)\n" in text
    assert "Fertig: 0 von 2 Quartalsläufen gewertet, 0 abgebrochen" in text
    assert str(tmp_path / "runs.db") in text
    # Says what is counted: no result yet to count, but both attempts are in the register.
    assert "ausgewertete Versuche: 0, alle Läufe: 2" in text
    assert text.rstrip().endswith(
        "Kein Quartal gewertet: Dieser Lauf liefert keine Zahlen für den Bericht "
        "(Gründe in den Zeilen oben)."
    )
    assert not [w for w in ENGLISH if w in text.replace(str(tmp_path), "")]
    for line in text.splitlines():
        assert len(line.replace(str(tmp_path), "<dir>")) <= 140, line
    with Register.open(tmp_path / "runs.db", tmp_path / "register.jsonl") as register:
        assert [r.status for r in register.list_runs()] == ["no_lead", "no_lead"]

    out = io.StringIO()
    assert cli_main("backtest", ["--dir", str(tmp_path / "empty")], out) == EXIT_REFUSED
    assert "Kein Kerzenarchiv" in out.getvalue()

    out = io.StringIO()
    assert cli_main("backtest", ["--help"], out) == EXIT_OK
    assert "Lückentoleranz" in out.getvalue() and "Prozessorkerne" in out.getvalue()


def test_cli_skips_the_cost_point_equal_to_base(tmp_path: Path) -> None:
    build_archive(tmp_path / "candles.db", T0, T0 + HOUR, lead=5)
    out = io.StringIO()
    args = ["--from", "2021Q2", "--to", "2021Q2", "--dir", str(tmp_path), "--workers", "1"]
    cli_main("backtest", [*args, "--variant", "cost-grid"], out)
    text = out.getvalue()
    assert "Varianten cost-0.3, cost-0.9, cost-1.2," in text
    assert (
        "cost-0.6 entfällt: gleiche Kosten wie base (live); base ist der 0,6-%-Punkt des "
        "Kostenrasters. Diesen Punkt liefert ein Lauf mit --variant base." in text
    )
    out = io.StringIO()
    cli_main("backtest", [*args, "--variant", "all"], out)
    assert "Diesen Punkt" not in out.getvalue()  # base runs in "all"
    with Register.open(tmp_path / "runs.db", tmp_path / "register.jsonl") as register:
        names = [r.variant for r in register.list_runs()]
    assert "cost-0.6" not in names
    assert names.count("base") == 1


def _graded(label: str) -> QuarterResult:
    full = {m: MarketQuality(m, due=10, decided=10) for m in ("BTC-EUR", "ETH-EUR")}
    return QuarterResult(Quarter.parse(label), BASE, quality=full)


@pytest.mark.parametrize(
    ("outcomes", "code"),
    [
        (["graded"], EXIT_OK),
        (["graded", "no_lead"], EXIT_OK),
        (["no_lead"], EXIT_NOT_GRADED),
        (["graded", "aborted"], EXIT_ABORTED),
    ],
)
def test_cli_exit_code_needs_a_graded_quarter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcomes: list[str], code: int
) -> None:
    build_archive(tmp_path / "candles.db", T0, T0 + HOUR, lead=5)

    def fake(*args: object, **kwargs: object) -> list[QuarterResult]:
        made = {
            "graded": _graded("2021Q2"),
            "no_lead": QuarterResult(Quarter.parse("2021Q3"), BASE, status="no_lead"),
            "aborted": QuarterResult(Quarter.parse("2021Q4"), BASE, status="aborted"),
        }
        return [made[o] for o in outcomes]

    monkeypatch.setattr(backtest, "run_quarters", fake)
    out = io.StringIO()
    args = ["--from", "2021Q2", "--to", "2021Q4", "--dir", str(tmp_path), "--workers", "1"]
    assert cli_main("backtest", args, out) == code
    assert ("Kein Quartal gewertet" in out.getvalue()) == ("graded" not in outcomes)


def test_cli_takes_the_code_state_from_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_archive(tmp_path / "candles.db", T0, T0 + HOUR, lead=5)
    calls: list[list[str]] = []

    def clean_checkout(args: Sequence[str]) -> str:
        calls.append(list(args))
        return "ab" * 20 + "\n" if args[0] == "rev-parse" else ""

    monkeypatch.setattr(backtest, "git_runner", clean_checkout)
    out = io.StringIO()
    args = ["--from", "2021Q2", "--to", "2021Q2", "--dir", str(tmp_path), "--workers", "1"]
    cli_main("backtest", args, out)
    assert calls == [["rev-parse", "HEAD"], ["status", "--porcelain"]]
    with Register.open(tmp_path / "runs.db", tmp_path / "register.jsonl") as register:
        (run,) = register.list_runs()
    assert (run.commit, run.clean) == ("ab" * 20, True)
    assert "nicht sauber" not in out.getvalue()


def test_describe_lines() -> None:
    q = span(T0, 1)
    quality = {m: MarketQuality(m, due=100, decided=95) for m in ("BTC-EUR", "ETH-EUR")}
    ok = QuarterResult(q, BASE, quality=quality, seconds=90)
    assert backtest.describe(ok) == (
        "2023Q1 base: gewertet; Entscheidungen BTC-EUR 95,0 %, ETH-EUR 95,0 %; Laufzeit 1,5 min"
    )
    low = QuarterResult(q, BASE, quality={"BTC-EUR": MarketQuality("BTC-EUR", 100, 80)})
    assert "unter 90 %" in backtest.describe(low)
