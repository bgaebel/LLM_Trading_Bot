"""Spec 016 AK2: the historical client replays the live request without looking ahead."""

import math
from collections.abc import Iterable, Mapping

import pytest

from fakes import FakeClock
from lab_captures import captures
from llm_trading_bot.lab.history import (
    CandleRow,
    ConstantSpreads,
    HistoricalClient,
    MemorySource,
    PreloadRangeError,
)
from llm_trading_bot.market.bitvavo import CANDLE_LIMIT, fill_tiny_gaps
from llm_trading_bot.market.models import (
    Candle,
    ClosedCandles,
    MarketDataError,
    Quote,
    QuoteBook,
    Symbol,
)
from llm_trading_bot.market.snapshot import INTERVALS, MarketClient, MarketSnapshot
from llm_trading_bot.strategy.indicators import indicators

BTC, ETH = Symbol.BTCEUR, Symbol.ETHEUR
T0 = 1_762_560_000  # 2025-11-08T00:00:00Z: aligned to every interval (4h too)
BARS = 900
"""Rows per series: longer than one window, so the 500-row cut is exercised."""
SPAN = BARS * 4 * 3600


def price(ts: int) -> float:
    """A close that names its own bar, so a test can tell which row a value came from."""
    return 1000.0 + ts / 3600


def row(ts: int, close: float | None = None) -> CandleRow:
    c = price(ts) if close is None else close
    return [ts * 1000, str(c), str(c + 1), str(c - 1), str(c), "2.5"]


def series(interval: int, start: int, end: int, skip: Iterable[int] = ()) -> list[CandleRow]:
    gone = set(skip)
    step = interval * 60
    return [row(ts) for ts in range(start, end, step) if ts not in gone]


def source(end: int, skip: dict[tuple[str, int], set[int]] | None = None) -> MemorySource:
    """BARS rows of every market and interval from T0 (not beyond ``end``), newest first."""
    out = MemorySource()
    for market in ("BTC-EUR", "ETH-EUR"):
        for interval in INTERVALS:
            stop = min(end, T0 + BARS * interval * 60)
            rows = series(interval, T0, stop, (skip or {}).get((market, interval), ()))
            out.add(market, interval, reversed(rows))
    return out


def client(src: MemorySource, clock: FakeClock, half: float = 0.0001) -> HistoricalClient:
    return HistoricalClient(src, ConstantSpreads(half), clock, start=T0, end=T0 + SPAN)


def reference(rows: list[CandleRow], interval: int, now: int) -> tuple[list[int], list[int]]:
    """Spelled out from the definition: the newest 500 rows that exist at ``now``, then the
    live filter. Returns (open times of the window, open times of its closed bars)."""
    step = interval * 60
    opens = [int(str(r[0])) // 1000 for r in rows]
    window = [ts for ts in opens if ts <= now][-CANDLE_LIMIT:]
    return window, [ts for ts in window if ts + step <= now]


def boundaries(interval: int) -> list[int]:
    """Clock values at, just before and just after bar edges, deep inside the data."""
    step = interval * 60
    base = T0 + 600 * step if interval < 240 else T0 + 700 * step
    out = []
    for k in (0, 1, 2, 7):
        edge = base + k * step
        out += [edge - 1, edge, edge + 1, edge + 5, edge + step - 1]
    return out


def test_never_returns_future_rows() -> None:
    end = T0 + SPAN
    src = source(end)
    clock = FakeClock(0)
    hist = client(src, clock)
    as_live: MarketClient = hist  # mypy proves the protocol is met
    assert as_live is hist
    for interval in INTERVALS:
        step = interval * 60
        rows = series(interval, T0, min(end, T0 + BARS * step))
        for now in boundaries(interval):
            got = hist.closed_candles(BTC, interval, now_epoch=now)
            assert isinstance(got, ClosedCandles)
            # Every bar had closed at the clock, also at the exact edge (open + step == now).
            assert all(c.ts + step <= now for c in got), (interval, now)
            window, closed = reference(rows, interval, now)
            forming = len(closed) < len(window)
            assert [c.ts for c in got] == closed, (interval, now)
            assert (got.received, got.forming) == (len(window), forming)
            assert (got.first_open, got.last_open) == (window[0], window[-1])
            # Deep inside the data: the window was cut at 500 rows.
            assert got.first_open is not None and got.first_open > T0
            # The bar that closed exactly at the clock is in; the forming one never.
            if now % step == 0:
                assert got[-1].ts == now - step
            assert got[-1].ts + 2 * step > now
            # A forming bar with a row counts in the 500, so live sees 499 closed bars.
            assert len(got) == (CANDLE_LIMIT - 1 if forming else CANDLE_LIMIT)
            assert all(c.close == price(c.ts) for c in got)
    # Quotes: always from a minute that had ended at the clock.
    for now in boundaries(1):
        clock.now = now
        q = hist.quotes()[BTC]
        last = (now - 60) // 60 * 60
        assert q.mid == pytest.approx(price(last), rel=1e-12), now
        assert last + 60 <= now


def test_forming_bar_counts_only_with_a_row() -> None:
    """Without a row for the forming bar the window holds 500 closed bars, as live does."""
    now = T0 + 600 * 900 + 5
    src = source(T0 + SPAN, skip={("BTC-EUR", 15): {now // 900 * 900}})
    got = client(src, FakeClock(now)).closed_candles(BTC, 15, now_epoch=now)
    assert len(got) == CANDLE_LIMIT and not got.forming
    assert got.received == CANDLE_LIMIT and got.last_open == now // 900 * 900 - 900


def test_quote_uses_last_trade_before_clock() -> None:
    minute = T0 + 800 * 60
    src = source(T0 + SPAN)
    src.add("BTC-EUR", 1, [row(minute - 60, 50_000.0), row(minute, 51_000.0)])
    src.add("ETH-EUR", 1, [row(minute - 60, 2_000.0), row(minute, 2_100.0)])
    clock = FakeClock(minute + 5)
    hist = client(src, clock, half=0.0002)
    book = hist.quotes()
    # 5 s into the minute its close is still in the future: the previous minute's close counts.
    assert book[BTC].mid == pytest.approx(50_000.0, rel=1e-12)
    assert book[BTC] == Quote(50_000.0 * (1 - 0.0002), 50_000.0 * (1 + 0.0002))
    assert book[ETH].mid == pytest.approx(2_000.0, rel=1e-12)
    clock.now = minute + 59
    assert hist.quotes()[BTC].mid == pytest.approx(50_000.0, rel=1e-12)
    # Exactly at the minute's end its close is the last trade before the clock.
    clock.now = minute + 60
    assert hist.quotes()[BTC].mid == pytest.approx(51_000.0, rel=1e-12)
    assert book[BTC].spread_bps == pytest.approx(4.0, rel=1e-9)


def test_missing_minute_still_quotes() -> None:
    minute = T0 + 800 * 60
    src = source(T0 + SPAN, skip={("BTC-EUR", 1): {minute - 60, minute - 120}})
    hist = client(src, FakeClock(minute + 5))
    book = hist.quotes()
    assert book[BTC].mid == pytest.approx(price(minute - 180), rel=1e-12)
    assert book[ETH].mid == pytest.approx(price(minute - 60), rel=1e-12)
    assert not book.errors


def test_no_row_at_all_costs_only_that_market() -> None:
    src = source(T0 + SPAN, skip={("ETH-EUR", 1): set(range(T0, T0 + BARS * 60, 60))})
    book = client(src, FakeClock(T0 + 600)).quotes()
    assert BTC in book and ETH not in book
    assert book.codes == {ETH: "quote_missing"}
    assert "ETH-EUR" in book.problem()


def test_wide_spread_is_refused_like_live() -> None:
    book = client(source(T0 + SPAN), FakeClock(T0 + 600), half=0.01).quotes()
    assert not book and book.codes == {BTC: "spread", ETH: "spread"}


def test_quote_override_wins() -> None:
    frozen = QuoteBook({BTC: Quote(1.0, 1.0001)})
    hist = HistoricalClient(
        source(T0 + SPAN),
        ConstantSpreads(),
        FakeClock(T0 + 600),
        start=T0,
        end=T0 + SPAN,
        quotes=lambda: frozen,
    )
    assert hist.quotes() is frozen


def test_rows_are_checked_by_the_live_parser() -> None:
    src = source(T0 + 4 * 3600)
    src.add("BTC-EUR", 15, [[T0 * 1000, "10", "9", "11", "10", "1"]])  # high below low
    with pytest.raises(MarketDataError, match="OHLC out of range"):
        HistoricalClient(src, ConstantSpreads(), FakeClock(T0), start=T0, end=T0 + 4 * 3600)
    with pytest.raises(MarketDataError, match="fractional"):
        MemorySource().add("BTC-EUR", 1, [[T0 * 1000 + 1, "1", "1", "1", "1", "1"]])


def test_clock_outside_preload_is_a_bug_not_a_data_fault() -> None:
    hist = client(source(T0 + SPAN), FakeClock(T0 + SPAN))
    # Not a ValueError: build_snapshots would swallow that as one market's data fault.
    assert not issubclass(PreloadRangeError, ValueError)
    with pytest.raises(PreloadRangeError):
        hist.closed_candles(BTC, 15, now_epoch=T0 + SPAN)
    with pytest.raises(PreloadRangeError):
        hist.quotes()
    with pytest.raises(PreloadRangeError):
        hist.closed_candles(BTC, 15, now_epoch=T0 - 1)
    with pytest.raises(MarketDataError, match="unsupported interval"):
        hist.closed_candles(BTC, 5, now_epoch=T0 + 600)


def test_later_row_wins_and_lead_is_counted() -> None:
    src = MemorySource()
    src.add("BTC-EUR", 1, [row(T0, 5.0), row(T0 + 60, 6.0)])
    src.add("BTC-EUR", 1, [row(T0 + 60, 7.0)])
    assert [r[4] for r in src.rows("BTC-EUR", 1, T0, T0 + 120)] == ["5.0", "7.0"]
    assert src.rows("BTC-EUR", 1, T0 + 60, T0 + 60) == []
    hist = HistoricalClient(
        src, ConstantSpreads(), FakeClock(T0), start=T0, end=T0 + 600, markets=(BTC,)
    )
    assert hist.rows_before(BTC, 1, T0 + 60) == 1
    assert hist.rows_before(BTC, 1, T0 + 61) == 2


def _snapshot(bars: dict[int, tuple[Candle, ...]], now: int) -> MarketSnapshot:
    filled = {}
    for tf in INTERVALS:
        bars[tf], filled[tf] = fill_tiny_gaps(bars[tf], tf, max_missing=2 if tf == 1 else 0)
    return MarketSnapshot(BTC, bars, Quote(1.0, 1.0), now, now, filled)


def test_window_499_and_500_within_tolerance() -> None:
    """The approximation (a forming bar with a row always counts) moves the window start by one
    bar against live. On real data that moves the indicators far below the 5 decimals live
    stores. A capture holds at most 500 rows, so the one-bar shift is measured one bar shorter."""
    capture = captures()[-1]
    now = capture.at
    src = MemorySource()
    for tf in INTERVALS:
        src.add("BTC-EUR", tf, capture.rows("BTC-EUR", tf))
    hist = HistoricalClient(
        src, ConstantSpreads(), FakeClock(now), start=0, end=now + 1, markets=(BTC,)
    )
    full = {tf: tuple(hist.closed_candles(BTC, tf, now_epoch=now)) for tf in INTERVALS}
    assert all(len(full[tf]) >= CANDLE_LIMIT - 1 for tf in INTERVALS)
    shifted = {tf: bars[1:] for tf, bars in full.items()}
    ia: Mapping[str, object] = indicators(_snapshot(dict(full), now), 0.006)
    ib: Mapping[str, object] = indicators(_snapshot(shifted, now), 0.006)
    moved = 0
    for key, value in ia.items():
        other = ib[key]
        if isinstance(value, float) and isinstance(other, float):
            assert math.isclose(value, other, rel_tol=1e-7, abs_tol=1e-9), key
            moved += value != other
        else:
            assert value == other, key
    # The test must see the effect at all (EMA and ADX start at the window's first bar).
    assert moved > 0
