# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_reporting_recovery.py (adapted)
"""Test doubles for the market-data transport and clocks."""

import re
from collections.abc import Callable

from llm_trading_bot.market.http import HTTPFailure


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
    now: int, *, eth_candles_fail: bool = False, quotes_fail: bool = False
) -> Callable[[str], object]:
    """Answers like Bitvavo: newest candle first, millisecond stamps, string values."""
    minutes = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}

    def handler(url: str) -> object:
        if url.endswith("/ticker/book"):
            if quotes_fail:
                raise HTTPFailure(503)
            return VALID_BOOK
        if eth_candles_fail and "/ETH-EUR/" in url:
            raise HTTPFailure(500)
        match = re.search(r"interval=(\w+)", url)
        assert match, url
        step = minutes[match.group(1)] * 60
        last = (now // step - 1) * step
        return [[(last - i * step) * 1000, "100", "101", "99", "100", "1"] for i in range(200)]

    return handler
