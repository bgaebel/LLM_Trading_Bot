# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_reporting_recovery.py (adapted)
"""Test doubles for the market-data transport and clocks."""

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
