# Origin: LLM-Trading-Arena@7563724 apps/arena/bitvavo.py (adapted)
"""Read-only client for Bitvavo's public REST API.

ADR-0004: no order endpoints, no private API, no keys. Only GET requests to public market data.
"""

import threading
import time
from collections.abc import Callable

from llm_trading_bot.market.http import HTTPFailure, Transport, UrllibTransport
from llm_trading_bot.market.models import SYMBOLS, MarketDataError, Quote, Symbol

API = "https://api.bitvavo.com/v2"

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

    def quotes(self) -> dict[Symbol, Quote]:
        """Best bid/ask for all markets from a single request.

        Both markets come from one response, so a broken quote fails both (as in the Arena).
        """
        data = self._get("/ticker/book")
        if not isinstance(data, list):
            raise MarketDataError("ticker/book: unexpected response format")
        by_market = {symbol.market: symbol for symbol in SYMBOLS}
        result: dict[Symbol, Quote] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = by_market.get(item.get("market"))  # type: ignore[arg-type]
            if symbol is None:
                continue
            try:
                quote = Quote(float(item["bid"]), float(item["ask"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketDataError(f"ticker/book: unreadable quote for {symbol.market}") from exc
            quote.validate()
            if symbol in result and result[symbol] != quote:
                raise MarketDataError(f"ticker/book: conflicting duplicate for {symbol.market}")
            result[symbol] = quote
        missing = [s.market for s in SYMBOLS if s not in result]
        if missing:
            raise MarketDataError(f"ticker/book: missing markets {', '.join(missing)}")
        return result
