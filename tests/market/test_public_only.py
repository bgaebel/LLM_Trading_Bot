"""ADR-0004: the market-data client must not be able to place orders or use the private API."""

import re

from fakes import VALID_BOOK, FakeTransport
from llm_trading_bot.market.bitvavo import BitvavoClient

ALLOWED = re.compile(r"^https://api\.bitvavo\.com/v2/(ticker/book|[A-Z]+-EUR/candles\?.*)$")


def test_client_exposes_only_read_methods() -> None:
    public = {name for name in dir(BitvavoClient) if not name.startswith("_")}
    assert public <= {"quotes", "closed_candles"}


def test_quotes_only_hits_public_endpoint() -> None:
    transport = FakeTransport(lambda _url: VALID_BOOK)
    BitvavoClient(transport).quotes()
    assert transport.urls
    assert all(ALLOWED.match(url) for url in transport.urls), transport.urls
