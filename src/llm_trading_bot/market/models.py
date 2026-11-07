# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - PAIRS, Quote
# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - Candle
"""Market data value types. Pure, no I/O."""

import math
from dataclasses import dataclass
from enum import Enum

# Wider spreads mean the book is too thin or the feed is broken: no decision on such a quote.
MAX_SPREAD_BPS = 50.0


class MarketDataError(ValueError):
    """Market data is missing, malformed or implausible; callers must fail closed."""


class Symbol(Enum):
    BTCEUR = "BTC-EUR"
    ETHEUR = "ETH-EUR"

    @property
    def market(self) -> str:
        """Bitvavo market identifier."""
        return self.value


SYMBOLS: tuple[Symbol, ...] = tuple(Symbol)


class QuoteBook(dict[Symbol, "Quote"]):
    """The valid quotes of one fetch, per market; ``errors`` says why a market is missing.

    Spec 011 AK7: one broken quote costs only its own market, never the other one.
    """

    def __init__(
        self,
        quotes: "dict[Symbol, Quote] | None" = None,
        errors: dict[Symbol, str] | None = None,
    ) -> None:
        super().__init__(quotes or {})
        self.errors: dict[Symbol, str] = dict(errors or {})

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
            raise MarketDataError(f"invalid bid/ask: {self.bid}/{self.ask}")
        if self.spread_bps > max_spread_bps:
            raise MarketDataError(f"spread too wide: {self.spread_bps:.1f} bp")


@dataclass(frozen=True)
class Candle:
    """OHLCV bar; ``ts`` is the bar's open time in epoch seconds (UTC)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
