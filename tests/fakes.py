# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_reporting_recovery.py (adapted)
"""Test doubles for the market-data transport and clocks."""

import re
from collections.abc import Callable
from typing import cast

from golden import scenarios
from golden_scenarios import Params, build_bars, quote_for
from llm_trading_bot.market.http import HTTPFailure
from llm_trading_bot.market.models import Candle, Quote, QuoteBook, Symbol


class FakeTransport:
    """Answers GETs from a handler and records every URL it was asked for."""

    def __init__(self, handler: Callable[[str], object]) -> None:
        self.handler = handler
        self.urls: list[str] = []

    def get_json(self, url: str) -> object:
        self.urls.append(url)
        return self.handler(url)


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def fail_with(status: int, retry_after: float | None = None) -> Callable[[str], object]:
    def handler(_url: str) -> object:
        raise HTTPFailure(status, retry_after)

    return handler


VALID_BOOK: list[dict[str, str]] = [
    {"market": "BTC-EUR", "bid": "66000", "ask": "66005"},
    {"market": "ETH-EUR", "bid": "3200", "ask": "3200.2"},
    {"market": "FOO-EUR", "bid": "10", "ask": "11"},
]


def bitvavo_handler(
    now: int,
    *,
    eth_candles_fail: bool = False,
    quotes_fail: bool = False,
    eth_quote_bad: bool = False,
) -> Callable[[str], object]:
    """Answers like Bitvavo: newest candle first, millisecond stamps, string values."""
    minutes = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}

    def handler(url: str) -> object:
        if url.endswith("/ticker/book"):
            if quotes_fail:
                raise HTTPFailure(503)
            if eth_quote_bad:
                return [VALID_BOOK[0], {"market": "ETH-EUR", "bid": "nan", "ask": "3200.2"}]
            return VALID_BOOK
        if eth_candles_fail and "/ETH-EUR/" in url:
            raise HTTPFailure(500)
        match = re.search(r"interval=(\w+)", url)
        assert match, url
        step = minutes[match.group(1)] * 60
        last = (now // step - 1) * step
        return [[(last - i * step) * 1000, "100", "101", "99", "100", "1"] for i in range(200)]

    return handler


def params(name: str, symbol: str) -> Params:
    base = next(s["params"] for s in scenarios() if s["params"]["name"] == name)
    return cast(Params, {**base, "symbol": symbol})


class ScenarioClient:
    """Market client serving golden-scenario bars (trend: BUY on 'up', SELL on 'down')."""

    def __init__(self, btc: str = "up-calm-plain-s1", eth: str = "up-calm-plain-s1") -> None:
        self.params = {Symbol.BTCEUR: params(btc, "BTCEUR"), Symbol.ETHEUR: params(eth, "ETHEUR")}
        self.fail_candles: set[Symbol] = set()
        self.fail_quotes = False
        self.bad_quotes: set[Symbol] = set()
        """Markets whose quote is unusable (Spec 011 AK7: only that market is lost)."""
        self.quote_calls = 0

    def quotes(self) -> QuoteBook:
        self.quote_calls += 1
        if self.fail_quotes:
            raise HTTPFailure(503)
        result = QuoteBook()
        for symbol, p in self.params.items():
            if symbol in self.bad_quotes:
                result.errors[symbol] = "ticker/book: unusable quote (fake)"
                continue
            bid, ask = quote_for(p, build_bars(p))
            result[symbol] = Quote(bid, ask)
        return result

    def closed_candles(
        self, symbol: Symbol, interval: int, *, now_epoch: int
    ) -> tuple[Candle, ...]:
        if symbol in self.fail_candles:
            raise HTTPFailure(500)
        return tuple(Candle(*row) for row in build_bars(self.params[symbol])[interval])


USDC_EUR = Quote(0.8975, 0.8976)
"""Spec 020: USDC-EUR near the price measured on 2026-11-08 (mid 0.89775), spread 1.1 bp."""


class UsdcScenarioClient(ScenarioClient):
    """ScenarioClient whose book also holds BTC-USDC and USDC-EUR (Spec 020).

    BTC-USDC follows the scenario's BTC-EUR price through ``USDC_EUR`` with a 2 bp spread, so
    the route and BTC-EUR describe the same market. ``usdc_quotes`` overrides, ``drop_usdc``
    leaves a quote-only market out of the book.
    """

    def __init__(self, btc: str = "up-calm-plain-s1", eth: str = "up-calm-plain-s1") -> None:
        super().__init__(btc, eth)
        bid, ask = quote_for(self.params[Symbol.BTCEUR], build_bars(self.params[Symbol.BTCEUR]))
        mid = (bid + ask) / 2 / USDC_EUR.mid
        self.usdc_quotes: dict[Symbol, Quote] = {
            Symbol.BTCUSDC: Quote(mid * (1 - 0.0001), mid * (1 + 0.0001)),
            Symbol.USDCEUR: USDC_EUR,
        }
        self.drop_usdc: set[Symbol] = set()

    def quotes(self) -> QuoteBook:
        book = super().quotes()
        for symbol, quote in self.usdc_quotes.items():
            if symbol not in self.drop_usdc:
                book[symbol] = quote
        return book
