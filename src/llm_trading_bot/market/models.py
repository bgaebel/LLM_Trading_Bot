# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - PAIRS, Quote
# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - Candle
"""Market data value types. Pure, no I/O."""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Self

# Wider spreads mean the book is too thin or the feed is broken: no decision on such a quote.
MAX_SPREAD_BPS = 50.0

ERROR_CODES: tuple[str, ...] = (
    "gap_1m",
    "gap_15m",
    "gap_60m",
    "gap_240m",
    "stale",
    "spread",
    "quote_missing",
    "conflict",
    "http",
    "parse",
    "other",
)
"""Stable reasons why market data was refused (Spec 015 AK2): the observations count rounds
without a decision by these codes, so they must not change with the wording of a message."""


class MarketDataError(ValueError):
    """Market data is missing, malformed or implausible; callers must fail closed.

    ``code`` is one of ERROR_CODES. The message text is exactly what it was before codes existed,
    because logs, reports and the status file show it.
    """

    def __init__(self, message: str, code: str = "other") -> None:
        super().__init__(message)
        self.code = code if code in ERROR_CODES else "other"


class Symbol(Enum):
    BTCEUR = "BTC-EUR"
    ETHEUR = "ETH-EUR"

    @property
    def market(self) -> str:
        """Bitvavo market identifier."""
        return self.value


SYMBOLS: tuple[Symbol, ...] = tuple(Symbol)

Depth = tuple[float | None, float | None]
"""Best bid size and best ask size as the exchange sent them; None where unreadable."""

RawRow = tuple[float, float, float | None, float | None]
"""bid, ask, bid size, ask size of one book row as sent - not validated, observed only."""


class QuoteBook(dict[Symbol, "Quote"]):
    """The valid quotes of one fetch, per market; ``errors`` says why a market is missing.

    Spec 011 AK7: one broken quote costs only its own market, never the other one.
    """

    def __init__(
        self,
        quotes: "dict[Symbol, Quote] | None" = None,
        errors: dict[Symbol, str] | None = None,
        depth: dict[Symbol, Depth] | None = None,
        codes: dict[Symbol, str] | None = None,
        rejected: dict[Symbol, tuple[RawRow, ...]] | None = None,
    ) -> None:
        super().__init__(quotes or {})
        self.errors: dict[Symbol, str] = dict(errors or {})
        # Spec 015 AK1: sizes live beside the quotes, never inside them - Quote, its equality
        # and everything decided on it stay exactly as before; the sizes are only observed.
        self.depth: dict[Symbol, Depth] = dict(depth or {})
        # Spec 015 AK2/AK4: why a market is missing as a stable code (ERROR_CODES), and the rows
        # of that market as sent (only those with finite bid and ask). Observed only: nothing
        # is ever traded, valued or validated on them.
        self.codes: dict[Symbol, str] = dict(codes or {})
        self.rejected: dict[Symbol, tuple[RawRow, ...]] = dict(rejected or {})

    def problem(self) -> str:
        """The missing markets and why, for reports; "" when every market is there."""
        return "; ".join(
            f"{s.market}: {self.errors.get(s, 'missing')}" for s in SYMBOLS if s not in self
        )


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return 10_000 * (self.ask - self.bid) / self.mid

    def validate(self, *, max_spread_bps: float = MAX_SPREAD_BPS) -> None:
        if not (math.isfinite(self.bid) and math.isfinite(self.ask) and 0 < self.bid <= self.ask):
            raise MarketDataError(f"invalid bid/ask: {self.bid}/{self.ask}", "parse")
        if self.spread_bps > max_spread_bps:
            raise MarketDataError(f"spread too wide: {self.spread_bps:.1f} bp", "spread")


@dataclass(frozen=True)
class Candle:
    """OHLCV bar; ``ts`` is the bar's open time in epoch seconds (UTC)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class ClosedCandles(tuple[Candle, ...]):
    """The closed candles of one request, plus what the exchange actually sent (Spec 015 AK4).

    Equal to and used like the plain tuple of closed candles. The extra attributes (rows received
    before forming and future bars were dropped, their oldest and newest open time, whether the
    forming bar was among them) are only observed, so they cannot change a snapshot or decision.
    """

    received: int
    first_open: int | None
    last_open: int | None
    forming: bool

    def __new__(
        cls,
        candles: Iterable[Candle],
        *,
        received: int,
        first_open: int | None,
        last_open: int | None,
        forming: bool,
    ) -> Self:
        obj = super().__new__(cls, candles)
        obj.received = received
        obj.first_open = first_open
        obj.last_open = last_open
        obj.forming = forming
        return obj
