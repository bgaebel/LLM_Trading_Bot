"""Spec 020 AK1: BTC-USDC and USDC-EUR come from the same /ticker/book request, pass the same
checks with the same codes, are stored and observed - and never cost BTC-EUR or ETH-EUR."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from fakes import VALID_BOOK, FakeTransport, UsdcScenarioClient
from golden_scenarios import NOW
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.llm.due import iso
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import QUOTED, SYMBOLS, Quote, QuoteBook, Symbol
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs, fresh_quotes, record_quotes
from llm_trading_bot.paper.observations import ObservationBuffer
from llm_trading_bot.paper.usdc import BUYHOLD_USDC

USDC_ROWS = [
    {"market": "BTC-USDC", "bid": "73500", "ask": "73515", "bidSize": "0.01", "askSize": "0.2"},
    {"market": "USDC-EUR", "bid": "0.8975", "ask": "0.8976", "bidSize": "5000", "askSize": "9"},
]
FULL = [*VALID_BOOK, *USDC_ROWS]


def book_of(rows: object) -> tuple[QuoteBook, FakeTransport]:
    transport = FakeTransport(lambda _url: rows)
    return BitvavoClient(transport).quotes(), transport


def test_usdc_markets_come_from_the_same_single_request() -> None:
    book, transport = book_of(FULL)
    assert transport.urls == ["https://api.bitvavo.com/v2/ticker/book"]
    assert book == {
        Symbol.BTCEUR: Quote(66000, 66005),
        Symbol.ETHEUR: Quote(3200, 3200.2),
        Symbol.BTCUSDC: Quote(73500, 73515),
        Symbol.USDCEUR: Quote(0.8975, 0.8976),
    }
    assert book.errors == {} and book.codes == {} and book.problem(QUOTED) == ""
    assert book.depth[Symbol.BTCUSDC] == (0.01, 0.2)
    assert book.depth[Symbol.USDCEUR] == (5000.0, 9.0)


def test_a_book_without_usdc_markets_is_exactly_the_book_of_before() -> None:
    book, _ = book_of(VALID_BOOK)
    assert set(book) == set(SYMBOLS)
    # Only candle markets are named missing; the quote-only ones count where they are needed.
    assert book.errors == {} and book.codes == {} and book.problem() == ""
    assert book.problem(QUOTED) == "BTC-USDC: missing; USDC-EUR: missing"


@pytest.mark.parametrize(
    ("row", "code", "message"),
    [
        ({"market": "BTC-USDC", "bid": "73000", "ask": "74000"}, "spread", "spread too wide"),
        ({"market": "BTC-USDC", "bid": "nan", "ask": "73515"}, "parse", "unusable quote"),
        ({"market": "BTC-USDC", "bid": "73515", "ask": "73500"}, "parse", "invalid bid/ask"),
        ({"market": "BTC-USDC", "ask": "73515"}, "parse", "unusable quote"),
    ],
)
def test_broken_usdc_quote_gets_the_same_checks_and_codes_and_costs_only_itself(
    row: dict[str, str], code: str, message: str
) -> None:
    book, _ = book_of([*VALID_BOOK, row, USDC_ROWS[1]])
    assert set(book) == {Symbol.BTCEUR, Symbol.ETHEUR, Symbol.USDCEUR}
    assert book.codes == {Symbol.BTCUSDC: code}
    assert message in book.errors[Symbol.BTCUSDC]
    assert book.problem() == ""  # BTC-EUR and ETH-EUR are untouched
    assert book.problem(QUOTED).startswith("BTC-USDC: ticker/book: unusable quote")


def test_conflicting_usdc_duplicate_is_a_conflict_of_that_market_only() -> None:
    other = {"market": "USDC-EUR", "bid": "0.8974", "ask": "0.8976"}
    book, _ = book_of([*FULL, other])
    assert Symbol.USDCEUR not in book and book.codes == {Symbol.USDCEUR: "conflict"}
    assert book.rejected == {
        Symbol.USDCEUR: ((0.8975, 0.8976, 5000.0, 9.0), (0.8974, 0.8976, None, None))
    }
    assert {Symbol.BTCEUR, Symbol.ETHEUR, Symbol.BTCUSDC} <= set(book)


def test_quote_only_markets_are_stored_only_when_asked_for(tmp_path: Path) -> None:
    con = open_db(tmp_path / "bot.db", agents=())
    try:
        book, _ = book_of(FULL)
        assert record_quotes(con, book, now=iso(NOW)) == list(SYMBOLS)
        assert {r[0] for r in con.execute("SELECT symbol FROM market")} == {"BTCEUR", "ETHEUR"}
        assert record_quotes(con, book, now=iso(NOW), markets=QUOTED) == list(QUOTED)
        stored = {r[0] for r in con.execute("SELECT symbol FROM market")}
        assert stored == {s.name for s in QUOTED}
        # The v0.3.1 readers never see them: "both markets" stays BTC-EUR and ETH-EUR.
        assert set(fresh_quotes(con, now_epoch=NOW)) == set(SYMBOLS)
        assert set(fresh_quotes(con, now_epoch=NOW, markets=QUOTED)) == set(QUOTED)
    finally:
        con.close()


def test_observations_record_the_usdc_books_like_the_others() -> None:
    book, _ = book_of([*VALID_BOOK, USDC_ROWS[0], {"market": "USDC-EUR", "bid": "1", "ask": "2"}])
    buffer = ObservationBuffer()
    buffer.book("start", NOW, book)
    rows = [(r[1], r[2], r[3], r[4]) for r in buffer.books]
    assert rows == [
        ("BTCEUR", "start", 66000.0, 66005.0),
        ("ETHEUR", "start", 3200.0, 3200.2),
        ("BTCUSDC", "start", 73500.0, 73515.0),
        # A rejected book keeps its rows as sent, marked by its code (Spec 015 AK4).
        ("USDCEUR", "start:rejected:spread", 1.0, 2.0),
    ]


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("trend", "trend-usdc", BUYHOLD, BUYHOLD_USDC))
    yield connection
    connection.close()


def market_rows(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT symbol FROM market")}


def test_a_cycle_without_usdc_depots_stores_no_quote_only_market(con: sqlite3.Connection) -> None:
    report = paper_cycle(
        con, UsdcScenarioClient(), BotConfig(("trend",), None), costs=Costs(), clock=lambda: NOW
    )
    assert report.complete and market_rows(con) == {"BTCEUR", "ETHEUR"}


def test_a_cycle_with_usdc_depots_stores_and_reports_the_usdc_markets(
    con: sqlite3.Connection,
) -> None:
    config = BotConfig((), None, ("trend-usdc",))
    client = UsdcScenarioClient()
    report = paper_cycle(con, client, config, costs=Costs(), clock=lambda: NOW)
    assert report.complete and market_rows(con) == {s.name for s in QUOTED}
    client.drop_usdc = {Symbol.USDCEUR}
    report = paper_cycle(con, client, config, costs=Costs(), clock=lambda: NOW + 30)
    assert report.quotes_error == "MarketDataError: USDC-EUR: missing"
