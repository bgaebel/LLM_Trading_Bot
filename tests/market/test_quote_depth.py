"""Spec 015 AK1: book sizes are read beside the quotes and can never cost or change a quote."""

import dataclasses
from typing import Any

import pytest

from fakes import VALID_BOOK, FakeTransport
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import ClosedCandles, Quote, QuoteBook, Symbol

SIZED = [
    {**VALID_BOOK[0], "bidSize": "0.5", "askSize": "1.25"},
    {**VALID_BOOK[1], "bidSize": "12", "askSize": "7.5"},
]


def quotes_of(book: object) -> QuoteBook:
    return BitvavoClient(FakeTransport(lambda _url: book)).quotes()


def test_sizes_are_read_into_the_depth_mapping() -> None:
    book = quotes_of(SIZED)
    assert book == {Symbol.BTCEUR: Quote(66000, 66005), Symbol.ETHEUR: Quote(3200, 3200.2)}
    assert book.depth == {Symbol.BTCEUR: (0.5, 1.25), Symbol.ETHEUR: (12.0, 7.5)}


def test_missing_size_keeps_quote() -> None:
    book = quotes_of(VALID_BOOK)
    assert book == quotes_of(SIZED)
    assert book.errors == {}
    assert book.depth == {Symbol.BTCEUR: (None, None), Symbol.ETHEUR: (None, None)}


@pytest.mark.parametrize(
    "garbage", ["", "x", "nan", "inf", "-1", None, True, [], {"a": 1}, "1e999", 10**400]
)
def test_garbage_size_keeps_quote(garbage: Any) -> None:
    book = quotes_of([{**SIZED[0], "bidSize": garbage, "askSize": garbage}, SIZED[1]])
    assert book == quotes_of(VALID_BOOK) and book.errors == {}
    assert book.depth[Symbol.BTCEUR] == (None, None)
    assert book.depth[Symbol.ETHEUR] == (12.0, 7.5)


def test_size_not_in_duplicate_check() -> None:
    """Equal bid/ask with different sizes is the same quote - no conflict, the market stays."""
    other_sizes = {**SIZED[0], "bidSize": "9", "askSize": "garbage"}
    book = quotes_of([*SIZED, other_sizes])
    assert book == quotes_of(VALID_BOOK) and book.errors == {}
    assert book.depth[Symbol.BTCEUR] == (9.0, None)
    # A real conflict (other price) still drops the market, and its sizes with it.
    conflict = quotes_of([*SIZED, {**SIZED[0], "bid": "67000", "ask": "67005"}])
    assert set(conflict) == {Symbol.ETHEUR} and "duplicate" in conflict.errors[Symbol.BTCEUR]
    assert set(conflict.depth) == {Symbol.ETHEUR}


def test_rejected_quote_has_no_depth() -> None:
    book = quotes_of([{**SIZED[0], "bid": "nan"}, SIZED[1]])
    assert set(book) == set(book.depth) == {Symbol.ETHEUR}


def test_quote_positional_construction_unchanged() -> None:
    quote = Quote(1.0, 2.0)
    assert [f.name for f in dataclasses.fields(Quote)] == ["bid", "ask"]
    assert (quote.bid, quote.ask) == (1.0, 2.0)
    assert quote == Quote(1.0, 2.0) and hash(quote) == hash(Quote(1.0, 2.0))
    # Old callers build books without depth; it is empty, never required.
    assert QuoteBook({Symbol.BTCEUR: quote}).depth == {}
    assert QuoteBook({Symbol.BTCEUR: quote}, {Symbol.ETHEUR: "missing"}).errors


def test_closed_candles_equal_the_plain_tuple() -> None:
    """The observed extras ride on a tuple subclass that compares like the tuple it was."""
    plain: tuple[Any, ...] = (1, 2)
    rows = ClosedCandles(plain, received=3, first_open=0, last_open=9, forming=True)
    assert rows == plain and repr(rows) == repr(plain) and len(rows) == 2
    assert (rows.received, rows.first_open, rows.last_open, rows.forming) == (3, 0, 9, True)
