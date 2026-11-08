"""Spec 016 AK4, AK6 (cost grid) and AK9: quarters through the unchanged live cycle."""

import hashlib
import io
import json
import math
import os
from dataclasses import replace
from pathlib import Path

import pytest

from lab_backtest_data import DAY, HOUR, T0, build_archive, crash, row, wavy
from llm_trading_bot.lab import backtest
from llm_trading_bot.lab.archive import Archive
from llm_trading_bot.lab.backtest import (
    BASE,
    EXIT_OK,
    EXIT_REFUSED,
    cli_main,
    cost_grid,
    diagnostic_variant,
    gap_tolerance,
    run_quarter,
    run_quarters,
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
    assert "ETH-EUR 240m: 499 rows" in refused.error
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
    assert eth.blocked_rounds == 0  # a 1m hole is no higher-interval block
    assert sum(eth.no_decision.values()) <= result.rounds
    assert result.eligible == {"BTC-EUR": True, "ETH-EUR": False}
    assert not result.graded


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


# ---------------------------------------------------------------- AK6 cost grid


def test_cost_grid_keeps_the_live_ratio() -> None:
    grid = cost_grid()
    assert [v.name for v in grid] == ["cost-0.3", "cost-0.6", "cost-0.9", "cost-1.2"]
    for v, k in zip(grid, (0.003, 0.006, 0.009, 0.012), strict=True):
        assert v.roundtrip == pytest.approx(k, rel=1e-12)
        assert v.fee_rate == pytest.approx(5 * v.slippage, rel=1e-9)
        assert not v.diagnostic and v.gap_window is None
    assert (grid[1].fee_rate, grid[1].slippage) == (BASE.fee_rate, BASE.slippage)
    assert [v.name for v in variants("all")] == [
        "base",
        *(v.name for v in grid),
        "diagnostic-gap-4",
    ]
    with pytest.raises(ValueError):
        variants("cheap")


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
    assert b.blocked_rounds > 0 and b.no_decision.get("gap_60m") == b.blocked_rounds
    assert d.blocked_rounds < b.blocked_rounds
    assert d.decided > b.decided
    assert base.quality["BTC-EUR"] == flagged.quality["BTC-EUR"]
    assert snapshot.GAP_WINDOW == 60  # restored in this (the running) process


# ---------------------------------------------------------------- CLI


def test_cli_reports_each_quarter_in_german(tmp_path: Path) -> None:
    build_archive(tmp_path / "candles.db", T0, T0 + HOUR, lead=5)
    out = io.StringIO()
    args = ["--from", "2021Q2", "--to", "2021Q3", "--dir", str(tmp_path), "--workers", "1"]
    assert cli_main("backtest", args, out) == EXIT_OK
    text = out.getvalue()
    assert "2021Q2 base: nicht gewertet - Datenvorlauf fehlt" in text
    assert "2021Q3 base: nicht gewertet - Datenvorlauf fehlt" in text
    assert str(tmp_path / "runs.db") in text
    with Register.open(tmp_path / "runs.db", tmp_path / "register.jsonl") as register:
        assert [r.status for r in register.list_runs()] == ["no_lead", "no_lead"]

    out = io.StringIO()
    assert cli_main("backtest", ["--dir", str(tmp_path / "empty")], out) == EXIT_REFUSED
    assert "Kein Kerzenarchiv" in out.getvalue()


def test_describe_lines() -> None:
    q = span(T0, 1)
    quality = {m: MarketQuality(m, due=100, decided=95) for m in ("BTC-EUR", "ETH-EUR")}
    ok = QuarterResult(q, BASE, quality=quality, seconds=90)
    assert backtest.describe(ok) == (
        "2023Q1 base: gewertet; Entscheidungen BTC-EUR 95,0 %, ETH-EUR 95,0 %; Laufzeit 1,5 min"
    )
    low = QuarterResult(q, BASE, quality={"BTC-EUR": MarketQuality("BTC-EUR", 100, 80)})
    assert "unter 90 %" in backtest.describe(low)
