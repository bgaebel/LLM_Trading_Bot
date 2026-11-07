"""Spec 015 AK2: every market data failure carries a stable code; the message text is unchanged."""

from collections.abc import Callable
from typing import Any

import pytest

from fakes import FakeTransport
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.http import HTTPFailure, TransportError
from llm_trading_bot.market.models import (
    ERROR_CODES,
    Candle,
    MarketDataError,
    Quote,
    QuoteBook,
    Symbol,
)
from llm_trading_bot.market.snapshot import (
    DEFAULT_MIN_BARS,
    INTERVALS,
    MarketSnapshot,
    build_snapshots,
    error_code,
)

BUCKET = 1_800_000_055 // 60 * 60
NOW = 1_800_000_030  # 1_800_000_000 is aligned to every interval
QUOTES = {Symbol.BTCEUR: Quote(66000, 66005), Symbol.ETHEUR: Quote(3200, 3200.2)}


def series(tf: int, count: int, *, end_shift: int = 0) -> list[Candle]:
    step = tf * 60
    last = (NOW // step - 1) * step - end_shift * step
    return [Candle(last - i * step, 100, 101, 99, 100, 1) for i in reversed(range(count))]


def full_bars() -> dict[int, list[Candle]]:
    return {tf: series(tf, DEFAULT_MIN_BARS[tf] + 20) for tf in INTERVALS}


class FakeClient:
    def __init__(
        self,
        bars: dict[Symbol, dict[int, list[Candle]]] | None = None,
        quotes: dict[Symbol, Quote] | None = None,
    ) -> None:
        self.bars = bars or {s: full_bars() for s in Symbol}
        self.quote_values = quotes if quotes is not None else dict(QUOTES)

    def quotes(self) -> QuoteBook:
        return QuoteBook(self.quote_values)

    def closed_candles(
        self, symbol: Symbol, interval: int, *, now_epoch: int
    ) -> tuple[Candle, ...]:
        return tuple(self.bars[symbol][interval])


def ticking(*values: float) -> Callable[[], float]:
    remaining = list(values)

    def clock() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return clock


def row(ts: int, *values: Any) -> list[Any]:
    return [ts * 1000, *(values or ("100", "101", "99", "100", "5"))]


def candles(rows: object, interval: int = 1) -> MarketDataError:
    client = BitvavoClient(FakeTransport(lambda _url: rows))
    with pytest.raises(MarketDataError) as caught:
        client.closed_candles(Symbol.BTCEUR, interval, now_epoch=1_800_000_055)
    return caught.value


@pytest.mark.parametrize(
    ("rows", "code", "message"),
    [
        ({"x": 1}, "parse", "candles: unexpected response format"),
        ([[1]], "parse", "candles: incomplete OHLCV row"),
        ([[None, 1, 1, 1, 1, 1]], "parse", "candles: invalid timestamp type"),
        ([["x", 1, 1, 1, 1, 1]], "parse", "candles: unreadable timestamp"),
        ([[1.5, 1, 1, 1, 1, 1]], "parse", "candles: fractional millisecond timestamp"),
        ([row(BUCKET - 60, "x", "1", "1", "1", "1")], "parse", "candles: unreadable OHLCV row"),
        ([row(BUCKET - 61)], "parse", "candles: timestamp not aligned to interval"),
        ([row(BUCKET - 60, "0", "1", "0", "1", "1")], "parse", "candles: invalid prices"),
        ([row(BUCKET - 60, "1", "1", "1", "1", "-1")], "parse", "candles: invalid volume"),
        ([row(BUCKET - 60, "1", "2", "1.5", "1", "1")], "parse", "candles: OHLC out of range"),
        (
            [row(BUCKET - 60), row(BUCKET - 60, "100", "102", "99", "100", "5")],
            "conflict",
            f"candles: conflicting duplicate at {BUCKET - 60}",
        ),
    ],
)
def test_candle_errors_have_codes_and_unchanged_messages(
    rows: object, code: str, message: str
) -> None:
    exc = candles(rows)
    assert (exc.code, str(exc)) == (code, message)


def test_quote_errors_have_codes() -> None:
    client = BitvavoClient(FakeTransport(lambda _url: {"not": "a list"}))
    with pytest.raises(MarketDataError) as caught:
        client.quotes()
    assert (caught.value.code, str(caught.value)) == (
        "parse",
        "ticker/book: unexpected response format",
    )
    with pytest.raises(MarketDataError) as wide:
        Quote(100, 101).validate()
    assert (wide.value.code, str(wide.value)) == ("spread", "spread too wide: 99.5 bp")
    with pytest.raises(MarketDataError) as bad:
        Quote(0, 1).validate()
    assert (bad.value.code, str(bad.value)) == ("parse", "invalid bid/ask: 0/1")


def failures(client: Any, clock: Callable[[], float] | None = None) -> dict[Symbol, Exception]:
    found: dict[Symbol, Exception] = {}
    build_snapshots(client, clock=clock or ticking(NOW), on_error=found.__setitem__)
    return found


@pytest.mark.parametrize("tf", INTERVALS)
def test_gaps_and_too_few_bars_are_coded_per_interval(tf: int) -> None:
    gap = full_bars()
    del gap[tf][-6:-3]  # three bars: more than the 1m filler reconstructs
    exc = failures(FakeClient({Symbol.BTCEUR: gap, Symbol.ETHEUR: full_bars()}))[Symbol.BTCEUR]
    assert error_code(exc) == f"gap_{tf}m" and "gap in recent" in str(exc)
    few = full_bars()
    few[tf] = few[tf][-3:]
    exc = failures(FakeClient({Symbol.BTCEUR: few, Symbol.ETHEUR: full_bars()}))[Symbol.BTCEUR]
    assert error_code(exc) == f"gap_{tf}m" and "too few closed" in str(exc)


def test_stale_bars_quote_and_clock_are_coded_stale() -> None:
    old = {tf: series(tf, 300, end_shift=3) for tf in INTERVALS}
    exc = failures(FakeClient({Symbol.BTCEUR: old, Symbol.ETHEUR: full_bars()}))[Symbol.BTCEUR]
    assert error_code(exc) == "stale" and "stale or in the future" in str(exc)
    # The quote arrives 30 s after the candles were requested: older than MAX_QUOTE_AGE.
    late = failures(FakeClient(), ticking(NOW, NOW, NOW + 30))
    assert {error_code(e) for e in late.values()} == {"stale"}
    bars = {tf: tuple(rows) for tf, rows in full_bars().items()}
    # A fresh quote, but the candles were requested 200 s before "now" (clock off).
    snap = MarketSnapshot(Symbol.BTCEUR, bars, Quote(1, 1.0001), NOW, NOW + 190, {})
    with pytest.raises(MarketDataError) as drift:
        snap.validate(now_epoch=NOW + 200)
    assert drift.value.code == "stale" and "snapshot time off by -200s" in str(drift.value)


def test_missing_quote_is_quote_missing() -> None:
    exc = failures(FakeClient(quotes={Symbol.BTCEUR: Quote(66000, 66005)}))[Symbol.ETHEUR]
    assert error_code(exc) == "quote_missing"
    assert str(exc) == "ETH-EUR: no quote - missing"


def test_transport_failures_are_http_and_other_values_parse() -> None:
    assert error_code(HTTPFailure(503)) == "http"
    assert error_code(TransportError("cut")) == "http"
    assert error_code(OSError("down")) == "http"
    assert error_code(ValueError("json")) == "parse"
    assert error_code(OverflowError()) == "parse"
    assert error_code(KeyError("bug")) == "other"
    assert error_code(MarketDataError("plain")) == "other"
    assert error_code(MarketDataError("typo", "no_such_code")) == "other"


def test_every_code_is_listed_and_messages_stay_plain() -> None:
    assert set(ERROR_CODES) >= {
        *(f"gap_{tf}m" for tf in INTERVALS),
        "stale",
        "spread",
        "quote_missing",
        "conflict",
        "http",
        "parse",
    }
    exc = MarketDataError("text", "stale")
    assert str(exc) == "text" and exc.args == ("text",) and isinstance(exc, ValueError)
    assert QuoteBook().errors == {}
