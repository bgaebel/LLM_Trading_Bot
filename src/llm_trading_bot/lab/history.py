"""Historical market client for the lab (Spec 016 AK2, ADR-0026).

Stands in for ``BitvavoClient`` behind the ``MarketClient`` protocol, so the backtest runs the
unchanged ``paper_cycle``. It replays what the live request would have returned at the simulated
clock: the newest ``CANDLE_LIMIT`` rows with an open time up to the clock - the forming bar
included when the archive has a row for it - and then exactly the live closed-bar filter
``open + step <= now``.

Explicit approximation: live, the forming bar is in the response only if it already had a trade
when the request arrived (about 5 s into the bar); the archive cannot tell, so a forming bar
with a row always counts. The window then starts one row later than live would have. Its effect
on the indicators is tiny and tested (``test_window_499_and_500_within_tolerance``).
Where the live answer is known (the forming flag recorded per window, Spec 015), the fidelity
check passes it as ``forming_hint`` and the window is rebuilt exactly.

Never a value from the future: candles come only from rows that had closed at the clock, and the
quote mid is the close of the last minute that had ended at the clock.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Protocol

from llm_trading_bot.market.bitvavo import (
    CANDLE_LIMIT,
    TIMEFRAMES,
    _open_time,
    _parse_candle,
)
from llm_trading_bot.market.models import (
    SYMBOLS,
    Candle,
    ClosedCandles,
    MarketDataError,
    Quote,
    QuoteBook,
    Symbol,
)
from llm_trading_bot.market.snapshot import INTERVALS

FormingHint = Callable[[Symbol, int, int], bool | None]
"""``(symbol, interval, now_epoch)`` -> whether the live answer held the forming bar: True as
the default approximation, False drops the archive's row for it, None means unknown."""

CandleRow = Sequence[object]
"""One candle row as Bitvavo sends it: ``[ts_ms, open, high, low, close, volume]``, the prices
and volume as strings."""

MINUTE = 60


class CandleSource(Protocol):
    """Where the raw rows come from (the candle archive, or memory in tests and preloads)."""

    def rows(self, market: str, interval: int, start: int, end: int) -> list[CandleRow]:
        """Rows of ``market`` (e.g. ``BTC-EUR``) and ``interval`` (minutes: 1, 15, 60, 240)
        with ``start <= open < end`` (epoch seconds), ascending by open time, unchanged."""
        ...


class SpreadModel(Protocol):
    def half_spread(self, market: str, epoch: int) -> float:
        """Half the assumed bid/ask spread as a fraction of mid at ``epoch`` (AK6)."""
        ...


@dataclass(frozen=True)
class ConstantSpreads:
    """The same half spread at every time; ``markets`` overrides ``default`` per market."""

    default: float = 0.0
    markets: Mapping[str, float] = field(default_factory=dict)

    def half_spread(self, market: str, epoch: int) -> float:
        return self.markets.get(market, self.default)


class MemorySource:
    """Raw rows held in memory, e.g. frozen live responses or a preloaded archive slice.

    ``add`` takes rows in any order; for the same open time the row added last wins, so a later
    capture (whose bar may have been revised) replaces an earlier one.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], dict[int, CandleRow]] = {}

    def add(self, market: str, interval: int, rows: Iterable[CandleRow]) -> None:
        table = self._rows.setdefault((market, interval), {})
        for row in rows:
            # The live parser's own timestamp check: a malformed stamp fails here, not later.
            table[_open_time(row)] = row

    def rows(self, market: str, interval: int, start: int, end: int) -> list[CandleRow]:
        table = self._rows.get((market, interval), {})
        return [table[ts] for ts in sorted(table) if start <= ts < end]


class PreloadRangeError(LookupError):
    """A request outside the preloaded range: the client cannot know these rows.

    Deliberately not a ValueError: ``build_snapshots`` would take that for a data fault of one
    market and silently skip it, but asking beyond the preload is a bug of the caller.
    """


class _Series:
    """One market and interval, parsed once; ``ts`` and ``candles`` are aligned."""

    __slots__ = ("candles", "ts")

    def __init__(self, rows: Sequence[CandleRow], step: int) -> None:
        # The live parser checks every row (Spec 016 AK1): the snapshot relies on it.
        candles = [_parse_candle(row, step) for row in rows]
        ts = [c.ts for c in candles]
        if any(a >= b for a, b in pairwise(ts)):
            raise MarketDataError("history: rows not strictly ascending", "parse")
        self.ts = ts
        self.candles = tuple(candles)


class HistoricalClient:
    """``MarketClient`` over archived rows; the clock is the simulated round time.

    Every series of ``markets`` and ``intervals`` is loaded once for ``[start, end)``. A window
    is complete only if ``CANDLE_LIMIT`` rows lie between ``start`` and the clock; the caller
    checks that (``rows_before``). ``quotes`` may be overridden, e.g. with a frozen live book.
    ``forming_hint`` (optional, the fidelity check) says per request whether the live answer
    held the forming bar; without it, or where it returns None, the approximation above holds.
    """

    def __init__(
        self,
        source: CandleSource,
        spreads: SpreadModel,
        clock: Callable[[], float],
        *,
        start: int,
        end: int,
        markets: Sequence[Symbol] = SYMBOLS,
        intervals: Sequence[int] = INTERVALS,
        quotes: Callable[[], QuoteBook] | None = None,
        forming_hint: FormingHint | None = None,
    ) -> None:
        if start >= end:
            raise ValueError(f"empty preload range {start}..{end}")
        self._spreads = spreads
        self._clock = clock
        self._start = start
        self._end = end
        self._override = quotes
        self._forming_hint = forming_hint
        self._markets = tuple(markets)
        self._series: dict[tuple[Symbol, int], _Series] = {}
        # The quotes need the 1m series even when the candles of 1m are not asked for.
        wanted = sorted({1, *intervals})
        for symbol in self._markets:
            for interval in wanted:
                rows = source.rows(symbol.market, interval, start, end)
                self._series[(symbol, interval)] = _Series(rows, interval * 60)

    def _check(self, epoch: float) -> None:
        # Rows that opened at or after ``end`` were never loaded: answering would silently
        # miss them. Before ``start`` the window would be short without saying so.
        if not self._start <= epoch < self._end:
            raise PreloadRangeError(f"clock {epoch} outside preload {self._start}..{self._end}")

    def rows_before(self, symbol: Symbol, interval: int, epoch: int) -> int:
        """Loaded rows with an open time before ``epoch`` (lead check of the backtest, AK4)."""
        return bisect_right(self._series[(symbol, interval)].ts, epoch - 1)

    def candles_between(
        self, symbol: Symbol, interval: int, start: int, end: int
    ) -> tuple[Candle, ...]:
        """Loaded candles with ``start <= open < end`` (the fidelity check asks whether the
        forming bar had a trade before the clock)."""
        series = self._series[(symbol, interval)]
        return series.candles[bisect_left(series.ts, start) : bisect_left(series.ts, end)]

    def closed_candles(self, symbol: Symbol, interval: int, *, now_epoch: int) -> ClosedCandles:
        if interval not in TIMEFRAMES:
            raise MarketDataError(f"unsupported interval: {interval} min")
        self._check(now_epoch)
        series = self._series[(symbol, interval)]
        step = interval * 60
        # What the live request returns: the newest rows that exist at the clock, i.e. opened
        # at or before it (the forming bar included when it has a row).
        hi = bisect_right(series.ts, now_epoch)
        hint = (
            None if self._forming_hint is None else self._forming_hint(symbol, interval, now_epoch)
        )
        if hint is False:
            # Live had no trade in the forming bar yet, so its row was not in the answer and the
            # window reaches one closed bar further back (the fidelity check knows this from
            # the recorded forming flag, Spec 015).
            hi = bisect_right(series.ts, now_epoch - step, 0, hi)
        lo = max(0, hi - CANDLE_LIMIT)
        # The live filter (bitvavo.py): a bar is closed when open + step <= now.
        closed = bisect_right(series.ts, now_epoch - step, lo, hi)
        return ClosedCandles(
            series.candles[lo:closed],
            received=hi - lo,
            first_open=series.ts[lo] if hi > lo else None,
            last_open=series.ts[hi - 1] if hi > lo else None,
            forming=closed < hi,
        )

    def quotes(self) -> QuoteBook:
        if self._override is not None:
            return self._override()
        now = self._clock()
        self._check(now)
        result: dict[Symbol, Quote] = {}
        errors: dict[Symbol, str] = {}
        codes: dict[Symbol, str] = {}
        for symbol in self._markets:
            series = self._series[(symbol, 1)]
            # The last minute that had ended at the clock (ts + 60 <= now): its close is the
            # last trade before the clock. A minute without trades has no row, so the one before
            # it still quotes.
            last = bisect_right(series.ts, now - MINUTE) - 1
            if last < 0:
                errors[symbol] = "history: no 1m row before the clock"
                codes[symbol] = "quote_missing"
                continue
            mid = series.candles[last].close
            half = self._spreads.half_spread(symbol.market, int(now))
            quote = Quote(mid * (1 - half), mid * (1 + half))
            try:
                quote.validate()
            except MarketDataError as exc:
                # As live: an unusable quote costs only its own market, with the same code.
                errors[symbol] = f"history: unusable quote ({exc})"
                codes[symbol] = exc.code
                continue
            result[symbol] = quote
        for symbol in SYMBOLS:
            if symbol not in result and symbol not in errors:
                errors[symbol] = "history: market not loaded"
                codes[symbol] = "quote_missing"
        return QuoteBook(result, errors, codes=codes)
