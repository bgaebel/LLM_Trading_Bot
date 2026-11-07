# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_v031.py (adapted)
from collections.abc import Callable

import pytest

from llm_trading_bot.market.http import HTTPFailure
from llm_trading_bot.market.models import Candle, MarketDataError, Quote, QuoteBook, Symbol
from llm_trading_bot.market.snapshot import (
    DEFAULT_MIN_BARS,
    INTERVALS,
    MarketSnapshot,
    build_snapshots,
)

NOW = 1_800_000_030  # 1_800_000_000 is aligned to every interval
QUOTES = {Symbol.BTCEUR: Quote(66000, 66005), Symbol.ETHEUR: Quote(3200, 3200.2)}


def series(tf: int, count: int, *, end_shift: int = 0, now: int = NOW) -> list[Candle]:
    """``count`` contiguous closed bars ending with the last bar closed before ``now``."""
    step = tf * 60
    last = (now // step - 1) * step - end_shift * step
    return [Candle(last - i * step, 100, 101, 99, 100, 1) for i in reversed(range(count))]


def full_bars(now: int = NOW) -> dict[int, list[Candle]]:
    return {tf: series(tf, DEFAULT_MIN_BARS[tf] + 20, now=now) for tf in INTERVALS}


class FakeClient:
    def __init__(
        self,
        bars: dict[Symbol, dict[int, list[Candle]]] | None = None,
        quotes: dict[Symbol, Quote] | None = None,
        *,
        candle_error: dict[Symbol, Exception] | None = None,
        quote_error: Exception | None = None,
    ) -> None:
        self.bars = bars or {s: full_bars() for s in Symbol}
        self.quote_values = quotes if quotes is not None else dict(QUOTES)
        self.candle_error = candle_error or {}
        self.quote_error = quote_error
        self.quote_calls = 0

    def quotes(self) -> QuoteBook:
        self.quote_calls += 1
        if self.quote_error:
            raise self.quote_error
        return QuoteBook(self.quote_values)

    def closed_candles(
        self, symbol: Symbol, interval: int, *, now_epoch: int
    ) -> tuple[Candle, ...]:
        if symbol in self.candle_error:
            raise self.candle_error[symbol]
        return tuple(self.bars[symbol][interval])


def ticking(*values: float) -> Callable[[], float]:
    """Clock that returns the given values in order, then repeats the last one."""
    remaining = list(values)

    def clock() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return clock


def collect() -> tuple[list[tuple[Symbol, str]], Callable[[Symbol, Exception], None]]:
    problems: list[tuple[Symbol, str]] = []
    return problems, lambda symbol, exc: problems.append((symbol, str(exc)))


def test_valid_data_gives_both_snapshots() -> None:
    snaps = build_snapshots(FakeClient(), clock=ticking(NOW))
    assert set(snaps) == {Symbol.BTCEUR, Symbol.ETHEUR}
    snap = snaps[Symbol.BTCEUR]
    assert snap.quote == QUOTES[Symbol.BTCEUR]
    assert snap.fetched_at == snap.quote_at == NOW
    assert set(snap.bars) == set(INTERVALS)


def test_candle_failure_in_one_market_keeps_the_other() -> None:
    problems, on_error = collect()
    client = FakeClient(candle_error={Symbol.ETHEUR: HTTPFailure(503)})
    snaps = build_snapshots(client, clock=ticking(NOW), on_error=on_error)
    assert set(snaps) == {Symbol.BTCEUR}
    assert problems == [(Symbol.ETHEUR, "HTTP 503")]


def test_invalid_data_in_one_market_keeps_the_other() -> None:
    eth = full_bars()
    eth[1] = eth[1][:30]  # below the 45-bar minimum
    problems, on_error = collect()
    client = FakeClient({Symbol.BTCEUR: full_bars(), Symbol.ETHEUR: eth})
    snaps = build_snapshots(client, clock=ticking(NOW), on_error=on_error)
    assert set(snaps) == {Symbol.BTCEUR}
    assert problems[0][0] is Symbol.ETHEUR and "too few" in problems[0][1]


def test_without_on_error_the_first_failure_is_raised() -> None:
    client = FakeClient(candle_error={Symbol.BTCEUR: MarketDataError("boom")})
    with pytest.raises(MarketDataError, match="boom"):
        build_snapshots(client, clock=ticking(NOW))


def test_quote_failure_reports_all_markets_and_returns_nothing() -> None:
    problems, on_error = collect()
    client = FakeClient(quote_error=OSError("network down"))
    assert build_snapshots(client, clock=ticking(NOW), on_error=on_error) == {}
    assert [s for s, _ in problems] == [Symbol.BTCEUR, Symbol.ETHEUR]


def test_no_quote_request_when_all_candle_fetches_fail() -> None:
    problems, on_error = collect()
    client = FakeClient(candle_error={s: OSError("down") for s in Symbol})
    assert build_snapshots(client, clock=ticking(NOW), on_error=on_error) == {}
    assert client.quote_calls == 0
    assert len(problems) == 2


def test_programming_errors_are_not_swallowed() -> None:
    client = FakeClient(candle_error={Symbol.BTCEUR: KeyError("bug")})
    with pytest.raises(KeyError):
        build_snapshots(client, clock=ticking(NOW), on_error=lambda *_: None)


def test_recent_gap_rejected() -> None:
    bars = full_bars()
    del bars[1][-10:-7]  # three missing minutes cannot be filled
    with pytest.raises(MarketDataError, match="gap"):
        build_snapshots(FakeClient({s: bars for s in Symbol}), clock=ticking(NOW))


def test_gap_older_than_window_is_tolerated() -> None:
    bars = full_bars()
    del bars[1][2:5]  # 65 bars total, gap well before the last 60
    snaps = build_snapshots(FakeClient({s: bars for s in Symbol}), clock=ticking(NOW))
    assert len(snaps) == 2


def test_small_gap_is_filled_and_counted() -> None:
    bars = full_bars()
    del bars[1][-5]
    snaps = build_snapshots(FakeClient({s: bars for s in Symbol}), clock=ticking(NOW))
    assert snaps[Symbol.BTCEUR].filled[1] == 1
    assert snaps[Symbol.BTCEUR].filled[15] == 0


def test_min_bars_is_a_parameter() -> None:
    bars = full_bars()
    bars[240] = bars[240][-10:]
    client = FakeClient({s: bars for s in Symbol})
    with pytest.raises(MarketDataError, match="too few"):
        build_snapshots(client, clock=ticking(NOW))
    snaps = build_snapshots(client, clock=ticking(NOW), min_bars={**DEFAULT_MIN_BARS, 240: 10})
    assert len(snaps) == 2


def test_stale_candles_rejected() -> None:
    bars = full_bars()
    bars[1] = series(1, 80, end_shift=3)  # newest closed minute is 3 minutes old
    with pytest.raises(MarketDataError, match="stale"):
        build_snapshots(FakeClient({s: bars for s in Symbol}), clock=ticking(NOW))


def test_future_candles_rejected() -> None:
    bars = full_bars()
    bars[15] = series(15, 130, end_shift=-2)
    with pytest.raises(MarketDataError, match="future"):
        build_snapshots(FakeClient({s: bars for s in Symbol}), clock=ticking(NOW))


def test_old_quote_rejected() -> None:
    # fetch at NOW, quote at NOW, validation 25 s later
    with pytest.raises(MarketDataError, match="quote age"):
        build_snapshots(FakeClient(), clock=ticking(NOW, NOW, NOW + 25))


def test_clock_drift_rejected() -> None:
    snap = MarketSnapshot(
        Symbol.BTCEUR,
        {tf: tuple(b) for tf, b in full_bars().items()},
        QUOTES[Symbol.BTCEUR],
        fetched_at=NOW,
        quote_at=NOW + 130,
        filled={},
    )
    with pytest.raises(MarketDataError, match="off by"):
        snap.validate(now_epoch=NOW + 130)


def test_wide_spread_quote_rejected_for_that_market_only() -> None:
    problems, on_error = collect()
    quotes = {**QUOTES, Symbol.ETHEUR: Quote(3200, 3240)}
    snaps = build_snapshots(FakeClient(quotes=quotes), clock=ticking(NOW), on_error=on_error)
    assert set(snaps) == {Symbol.BTCEUR}
    assert "spread" in problems[0][1]


def test_missing_quote_for_market_rejected() -> None:
    problems, on_error = collect()
    client = FakeClient(quotes={Symbol.BTCEUR: QUOTES[Symbol.BTCEUR]})
    snaps = build_snapshots(client, clock=ticking(NOW), on_error=on_error)
    assert set(snaps) == {Symbol.BTCEUR}
    assert problems == [(Symbol.ETHEUR, "ETH-EUR: no quote - missing")]


def test_non_ascending_bars_rejected() -> None:
    bars = full_bars()
    bars[60] = [*bars[60][:-2], bars[60][-1], bars[60][-2]]
    with pytest.raises(MarketDataError, match="ascending"):
        build_snapshots(FakeClient({s: bars for s in Symbol}), clock=ticking(NOW))


def test_min_bars_without_an_interval_is_a_bug_not_a_default() -> None:
    with pytest.raises(KeyError):
        build_snapshots(FakeClient(), clock=ticking(NOW), min_bars={1: 45, 15: 110, 60: 110})
