# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - MultiSnapshot.validate
# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - live_snapshots
"""Validated per-market view of quotes and closed candles. Invalid data never becomes a snapshot."""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

from llm_trading_bot.market.bitvavo import fill_tiny_gaps
from llm_trading_bot.market.http import HTTPFailure, TransportError
from llm_trading_bot.market.models import SYMBOLS, Candle, MarketDataError, Quote, Symbol

INTERVALS: tuple[int, ...] = (1, 15, 60, 240)
# Arena defaults; the strategy (M1 Nr. 2) decides the real minimum per interval.
DEFAULT_MIN_BARS: Mapping[int, int] = {1: 45, 15: 110, 60: 110, 240: 55}
# Indicators like EMA/RSI/ADX assume equal time steps, so recent bars must be gap-free.
GAP_WINDOW = 60
FRESHNESS_SLACK = 90
MAX_CLOCK_DRIFT = 120
MAX_QUOTE_AGE = 20

# Errors that mean "this market's data is unusable right now" rather than a programming bug.
# Narrower than the Arena's `except Exception` so bugs stay visible; every real feed failure
# must map onto one of these, otherwise one market's failure costs the other its snapshot.
DATA_ERRORS = (
    MarketDataError,
    HTTPFailure,
    TransportError,
    OSError,
    ValueError,
    OverflowError,
)


class MarketClient(Protocol):
    def quotes(self) -> dict[Symbol, Quote]: ...

    def closed_candles(
        self, symbol: Symbol, interval: int, *, now_epoch: int
    ) -> tuple[Candle, ...]: ...


@dataclass(frozen=True)
class MarketSnapshot:
    """Precondition: bars passed the per-row checks of the Bitvavo parser (prices, volume,
    OHLC range). ``validate`` checks only completeness, order, gaps and freshness; bars from any
    other source (e.g. a database later) must be checked row by row first.
    """

    symbol: Symbol
    bars: Mapping[int, tuple[Candle, ...]]
    quote: Quote
    fetched_at: int
    """Epoch seconds when the candles were requested."""
    quote_at: int
    """Epoch seconds when the quote arrived."""
    filled: Mapping[int, int]
    """Number of synthetic zero-volume bars per interval (see fill_tiny_gaps)."""

    def validate(self, *, now_epoch: int, min_bars: Mapping[int, int] = DEFAULT_MIN_BARS) -> None:
        name = self.symbol.market
        self.quote.validate()
        quote_age = now_epoch - self.quote_at
        if not 0 <= quote_age <= MAX_QUOTE_AGE:
            raise MarketDataError(f"{name}: quote age {quote_age}s outside 0..{MAX_QUOTE_AGE}s")
        if abs(self.fetched_at - now_epoch) > MAX_CLOCK_DRIFT:
            raise MarketDataError(f"{name}: snapshot time off by {self.fetched_at - now_epoch}s")
        for tf in INTERVALS:
            # A min_bars map without an interval is a configuration bug: KeyError, no default.
            self._validate_bars(tf, self.bars.get(tf, ()), min_bars[tf])

    def _validate_bars(self, tf: int, seq: tuple[Candle, ...], minimum: int) -> None:
        name = self.symbol.market
        step = tf * 60
        if len(seq) < max(1, minimum):
            raise MarketDataError(f"{name}: too few closed {tf}m bars ({len(seq)} < {minimum})")
        if any(a.ts >= b.ts for a, b in pairwise(seq)):
            raise MarketDataError(f"{name}: {tf}m timestamps not strictly ascending")
        recent = seq[-GAP_WINDOW:]
        if any(b.ts - a.ts != step for a, b in pairwise(recent)):
            raise MarketDataError(f"{name}: gap in recent {tf}m bars - no decision")
        age = self.fetched_at - (seq[-1].ts + step)
        if age < -FRESHNESS_SLACK or age > step + FRESHNESS_SLACK:
            raise MarketDataError(f"{name}: {tf}m data stale or in the future ({age}s)")


def build_snapshots(
    client: MarketClient,
    *,
    clock: Callable[[], float] = time.time,
    min_bars: Mapping[int, int] = DEFAULT_MIN_BARS,
    on_error: Callable[[Symbol, Exception], None] | None = None,
) -> dict[Symbol, MarketSnapshot]:
    """Fetch and validate one snapshot per market; a failing market never blocks the other.

    ``on_error`` receives each failed market; without it the first failure is raised, so
    manual runs and tests see data faults instead of silently getting fewer markets.
    """

    def report(symbol: Symbol, exc: Exception) -> None:
        if on_error is None:
            raise exc
        on_error(symbol, exc)

    fetched_at = int(clock())
    bars: dict[Symbol, dict[int, tuple[Candle, ...]]] = {}
    filled: dict[Symbol, dict[int, int]] = {}
    for symbol in SYMBOLS:
        try:
            per_tf: dict[int, tuple[Candle, ...]] = {}
            per_fill: dict[int, int] = {}
            for tf in INTERVALS:
                raw = client.closed_candles(symbol, tf, now_epoch=fetched_at)
                # Bitvavo omits no-trade minutes; only short 1m holes are reconstructed.
                per_tf[tf], per_fill[tf] = fill_tiny_gaps(raw, tf, max_missing=2 if tf == 1 else 0)
            bars[symbol], filled[symbol] = per_tf, per_fill
        except DATA_ERRORS as exc:
            report(symbol, exc)

    if not bars:
        return {}
    # Quotes last: they age fastest and must be fresh when a decision is taken.
    try:
        quotes = client.quotes()
    except DATA_ERRORS as exc:
        for symbol in bars:
            report(symbol, exc)
        return {}
    quote_at = int(clock())

    snapshots: dict[Symbol, MarketSnapshot] = {}
    for symbol in bars:
        try:
            quote = quotes.get(symbol)
            if quote is None:
                raise MarketDataError(f"{symbol.market}: no quote")
            snap = MarketSnapshot(symbol, bars[symbol], quote, fetched_at, quote_at, filled[symbol])
            snap.validate(now_epoch=int(clock()), min_bars=min_bars)
            snapshots[symbol] = snap
        except DATA_ERRORS as exc:
            report(symbol, exc)
    return snapshots
