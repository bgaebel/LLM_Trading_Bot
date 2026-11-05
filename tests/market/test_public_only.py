"""ADR-0004: the market-data client must not be able to place orders or use the private API."""

import re

from fakes import VALID_BOOK, FakeTransport
from llm_trading_bot.market.bitvavo import TIMEFRAMES, BitvavoClient
from llm_trading_bot.market.models import Symbol

ALLOWED = re.compile(r"^https://api\.bitvavo\.com/v2/(ticker/book|[A-Z]+-EUR/candles\?.*)$")


def test_client_exposes_only_read_methods() -> None:
    public = {name for name in dir(BitvavoClient) if not name.startswith("_")}
    assert public <= {"quotes", "closed_candles"}


def test_all_calls_only_hit_public_endpoints() -> None:
    transport = FakeTransport(lambda url: VALID_BOOK if url.endswith("ticker/book") else [])
    client = BitvavoClient(transport)
    client.quotes()
    for symbol in Symbol:
        for interval in TIMEFRAMES:
            client.closed_candles(symbol, interval, now_epoch=1_800_000_000)
    assert len(transport.urls) == 1 + 2 * len(TIMEFRAMES)
    assert all(ALLOWED.match(url) for url in transport.urls), transport.urls
