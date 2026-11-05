# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_v03.py (adapted)
from typing import Any

import pytest

from fakes import VALID_BOOK, FakeTransport
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import MarketDataError, Quote, Symbol


def client_for(payload: object) -> tuple[BitvavoClient, FakeTransport]:
    transport = FakeTransport(lambda _url: payload)
    return BitvavoClient(transport), transport


def test_both_markets_from_one_ticker_book_request() -> None:
    client, transport = client_for(VALID_BOOK)
    quotes = client.quotes()
    assert quotes == {Symbol.BTCEUR: Quote(66000, 66005), Symbol.ETHEUR: Quote(3200, 3200.2)}
    assert transport.urls == ["https://api.bitvavo.com/v2/ticker/book"]


def test_missing_market_rejected() -> None:
    client, _ = client_for([{"market": "BTC-EUR", "bid": "10", "ask": "10.001"}])
    with pytest.raises(MarketDataError, match="ETH-EUR"):
        client.quotes()


def test_conflicting_duplicate_rejected() -> None:
    book = [*VALID_BOOK, {"market": "BTC-EUR", "bid": "67000", "ask": "67005"}]
    client, _ = client_for(book)
    with pytest.raises(MarketDataError, match="duplicate"):
        client.quotes()


def test_identical_duplicate_accepted() -> None:
    client, _ = client_for([*VALID_BOOK, VALID_BOOK[0]])
    assert client.quotes()[Symbol.BTCEUR] == Quote(66000, 66005)


@pytest.mark.parametrize(
    "btc",
    [
        {"market": "BTC-EUR", "bid": "0", "ask": "1"},
        {"market": "BTC-EUR", "bid": "101", "ask": "100"},
        {"market": "BTC-EUR", "bid": "nan", "ask": "100"},
        {"market": "BTC-EUR", "bid": "100", "ask": "101"},  # ~100 bp spread
        {"market": "BTC-EUR", "bid": None, "ask": "100"},
        {"market": "BTC-EUR", "ask": "100"},
    ],
)
def test_invalid_quote_rejected(btc: dict[str, Any]) -> None:
    client, _ = client_for([btc, VALID_BOOK[1]])
    with pytest.raises(MarketDataError):
        client.quotes()


@pytest.mark.parametrize("payload", [{}, "text", None, 42])
def test_non_list_response_rejected(payload: object) -> None:
    client, _ = client_for(payload)
    with pytest.raises(MarketDataError, match="format"):
        client.quotes()


def test_non_dict_items_are_ignored() -> None:
    client, _ = client_for(["junk", 7, *VALID_BOOK])
    assert set(client.quotes()) == {Symbol.BTCEUR, Symbol.ETHEUR}
