# Origin: LLM-Trading-Arena@7563724 apps/arena/bitvavo.py (adapted)
"""Read-only client for Bitvavo's public REST API.

ADR-0004: no order endpoints, no private API, no keys. Only GET requests to public market data.
"""

import math
import threading
import time
from collections.abc import Callable
from urllib.parse import quote as url_quote
from urllib.parse import urlencode

from llm_trading_bot.market.http import HTTPFailure, Transport, UrllibTransport
from llm_trading_bot.market.models import (
    SYMBOLS,
    Candle,
    MarketDataError,
    Quote,
    QuoteBook,
    Symbol,
)

API = "https://api.bitvavo.com/v2"
# Interval in minutes -> Bitvavo interval name.
TIMEFRAMES: dict[int, str] = {1: "1m", 15: "15m", 60: "1h", 240: "4h"}
CANDLE_LIMIT = 500

# Without a server hint, back off long enough that repeated 429s cannot get the IP banned.
DEFAULT_COOLDOWN = 900.0
MIN_COOLDOWN = 1.0
MAX_COOLDOWN = 3600.0


class BitvavoClient:
    """One instance per process: the 429 cooldown it holds is shared by quote and candle calls.

    Deviation from the Arena: the cooldown lives on the instance instead of module globals,
    so it is testable and has no hidden state.
    """

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport: Transport = transport if transport is not None else UrllibTransport()
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._not_before = 0.0

    def _get(self, path: str) -> object:
        with self._lock:
            remaining = self._not_before - self._monotonic()
        if remaining > 0:
            raise HTTPFailure(429, remaining)
        try:
            return self._transport.get_json(API + path)
        except HTTPFailure as exc:
            if exc.status == 429:
                delay = min(MAX_COOLDOWN, max(MIN_COOLDOWN, exc.retry_after or DEFAULT_COOLDOWN))
                with self._lock:
                    self._not_before = max(self._not_before, self._monotonic() + delay)
            raise

    def quotes(self) -> QuoteBook:
        """Best bid/ask for all markets from a single request.

        Spec 011 AK7 (ADR-0021): unlike the Arena, a broken, conflicting or missing quote costs
        only its own market - the book holds the valid ones and names the others in ``errors``.
        Only an unreadable response as a whole fails both.
        """
        data = self._get("/ticker/book")
        if not isinstance(data, list):
            raise MarketDataError("ticker/book: unexpected response format")
        by_market = {symbol.market: symbol for symbol in SYMBOLS}
        result: dict[Symbol, Quote] = {}
        errors: dict[Symbol, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = by_market.get(item.get("market"))  # type: ignore[arg-type]
            if symbol is None or symbol in errors:
                continue
            try:
                quote = Quote(float(item["bid"]), float(item["ask"]))
                quote.validate()
            except (KeyError, TypeError, ValueError) as exc:  # MarketDataError is a ValueError
                errors[symbol] = f"ticker/book: unusable quote ({exc})"
                result.pop(symbol, None)
                continue
            if symbol in result and result[symbol] != quote:
                errors[symbol] = "ticker/book: conflicting duplicate"
                del result[symbol]
                continue
            result[symbol] = quote
        for s in SYMBOLS:
            if s not in result and s not in errors:
                errors[s] = "ticker/book: missing"
        return QuoteBook(result, errors)

    def closed_candles(
        self, symbol: Symbol, interval: int, *, now_epoch: int
    ) -> tuple[Candle, ...]:
        """Completed candles only, ascending by open time; forming and future bars are dropped."""
        if interval not in TIMEFRAMES:
            raise MarketDataError(f"unsupported interval: {interval} min")
        query = urlencode({"interval": TIMEFRAMES[interval], "limit": CANDLE_LIMIT})
        rows = self._get(f"/{url_quote(symbol.market, safe='')}/candles?{query}")
        if not isinstance(rows, list):
            raise MarketDataError("candles: unexpected response format")
        step = interval * 60
        seen: dict[int, Candle] = {}
        for row in rows:
            # As in the Arena, forming and future bars are skipped before any other check:
            # their values are not final, so they must neither be used nor reject the interval.
            if _open_time(row) + step > now_epoch:
                continue
            candle = _parse_candle(row, step)
            if candle.ts in seen and seen[candle.ts] != candle:
                raise MarketDataError(f"candles: conflicting duplicate at {candle.ts}")
            seen[candle.ts] = candle
        return tuple(seen[ts] for ts in sorted(seen))


def _open_time(row: object) -> int:
    """Open time in epoch seconds; rejects anything that is not an exact whole second."""
    if not isinstance(row, list | tuple) or len(row) < 6:
        raise MarketDataError("candles: incomplete OHLCV row")
    raw_ts = row[0]
    # A fractional or misaligned millisecond stamp must fail, not be silently rounded.
    if isinstance(raw_ts, bool) or not isinstance(raw_ts, int | float | str):
        raise MarketDataError("candles: invalid timestamp type")
    try:
        ts_ms = int(raw_ts)
        exact = float(raw_ts) == ts_ms
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError("candles: unreadable timestamp") from exc
    if not exact or ts_ms % 1000:
        raise MarketDataError("candles: fractional millisecond timestamp")
    return ts_ms // 1000


def _parse_candle(row: object, step: int) -> Candle:
    ts = _open_time(row)
    if not isinstance(row, list | tuple):  # already checked by _open_time; narrows the type
        raise MarketDataError("candles: incomplete OHLCV row")
    try:
        values = [float(x) for x in row[1:6]]
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError("candles: unreadable OHLCV row") from exc
    if ts % step:
        raise MarketDataError("candles: timestamp not aligned to interval")
    candle = Candle(ts, *values)
    if not all(
        math.isfinite(x) and x > 0 for x in (candle.open, candle.high, candle.low, candle.close)
    ):
        raise MarketDataError("candles: invalid prices")
    if not math.isfinite(candle.volume) or candle.volume < 0:
        raise MarketDataError("candles: invalid volume")
    if candle.low > min(candle.open, candle.close) or candle.high < max(
        candle.open, candle.close, candle.low
    ):
        raise MarketDataError("candles: OHLC out of range")
    return candle


def fill_tiny_gaps(
    rows: tuple[Candle, ...], interval: int, *, max_missing: int = 2
) -> tuple[tuple[Candle, ...], int]:
    """Fill at most ``max_missing`` consecutive absent bars with the previous close and zero volume.

    Bitvavo omits intervals without trades. Larger gaps stay and are rejected by the snapshot
    check; nothing is ever filled after the last real bar (that would invent future prices).
    Returns (candles, number_of_filled_bars).
    """
    if not rows:
        return rows, 0
    step = interval * 60
    output = [rows[0]]
    filled = 0
    for bar in rows[1:]:
        prior = output[-1]
        delta = bar.ts - prior.ts
        if delta > step and delta % step == 0 and delta // step - 1 <= max_missing:
            for ts in range(prior.ts + step, bar.ts, step):
                close = prior.close
                output.append(Candle(ts, close, close, close, close, 0.0))
                filled += 1
        output.append(bar)
    return tuple(output), filled
