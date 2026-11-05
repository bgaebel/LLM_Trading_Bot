# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_v03.py (adapted) - also test_research.py
from typing import Any

import pytest

from fakes import FakeTransport
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import Candle, MarketDataError, Symbol

NOW = 1_800_000_055
BUCKET = NOW // 60 * 60  # open time of the still-forming 1m bar


def row(
    ts: int, o: Any = "100", h: Any = "101", lo: Any = "99", c: Any = "100", v: Any = "5"
) -> list[Any]:
    return [ts * 1000, o, h, lo, c, v]


def fetch(rows: object, interval: int = 1, now: int = NOW) -> tuple[Candle, ...]:
    client = BitvavoClient(FakeTransport(lambda _url: rows))
    return client.closed_candles(Symbol.BTCEUR, interval, now_epoch=now)


def test_forming_bar_dropped_and_result_sorted() -> None:
    rows = [row(BUCKET - 60), row(BUCKET), row(BUCKET - 120)]
    assert [c.ts for c in fetch(rows)] == [BUCKET - 120, BUCKET - 60]


def test_future_bars_dropped() -> None:
    assert fetch([row(BUCKET + 600), row(BUCKET - 60)]) == (
        Candle(BUCKET - 60, 100, 101, 99, 100, 5),
    )


def test_bar_closing_exactly_now_is_complete() -> None:
    start = BUCKET - 60
    assert len(fetch([row(start)], now=start + 60)) == 1


def test_request_url_uses_market_interval_and_limit() -> None:
    transport = FakeTransport(lambda _url: [])
    BitvavoClient(transport).closed_candles(Symbol.ETHEUR, 240, now_epoch=NOW)
    assert transport.urls == ["https://api.bitvavo.com/v2/ETH-EUR/candles?interval=4h&limit=500"]


def test_identical_duplicates_deduplicated() -> None:
    assert len(fetch([row(BUCKET - 60)] * 2)) == 1


def test_conflicting_duplicates_rejected() -> None:
    with pytest.raises(MarketDataError, match="duplicate"):
        fetch([row(BUCKET - 60), row(BUCKET - 60, c="100.5")])


@pytest.mark.parametrize(
    "bad",
    [
        [1_800_000_000_001, "100", "101", "99", "100", "1"],  # fractional second
        [1_799_999_970_000, "100", "101", "99", "100", "1"],  # not aligned to 1m
        [1_800_000_000_000.5, "100", "101", "99", "100", "1"],  # fractional ms
        [True, "100", "101", "99", "100", "1"],
        [None, "100", "101", "99", "100", "1"],
        row(BUCKET - 60, o="0"),
        row(BUCKET - 60, c="-1"),
        row(BUCKET - 60, h="inf"),
        row(BUCKET - 60, v="-1"),
        row(BUCKET - 60, v="nan"),
        row(BUCKET - 60, lo="100.5"),  # low above open/close
        row(BUCKET - 60, h="99.5"),  # high below open/close
        row(BUCKET - 60, o="abc"),
        [BUCKET * 1000, "100", "101", "99", "100"],  # incomplete row
        "not a row",
    ],
)
def test_invalid_rows_rejected(bad: object) -> None:
    with pytest.raises(MarketDataError):
        fetch([bad])


@pytest.mark.parametrize("payload", [{}, None, "x"])
def test_non_list_response_rejected(payload: object) -> None:
    with pytest.raises(MarketDataError, match="format"):
        fetch(payload)


@pytest.mark.parametrize("interval", [5, 30, 1440])
def test_unsupported_interval_rejected_without_request(interval: int) -> None:
    transport = FakeTransport(lambda _url: [])
    with pytest.raises(MarketDataError, match="interval"):
        BitvavoClient(transport).closed_candles(Symbol.BTCEUR, interval, now_epoch=NOW)
    assert transport.urls == []


def test_numeric_and_string_values_both_accepted() -> None:
    candle = fetch([[(BUCKET - 60) * 1000, 100, "101", 99.0, "100", 0]])[0]
    assert candle == Candle(BUCKET - 60, 100, 101, 99, 100, 0)


def test_infinite_timestamp_rejected() -> None:
    with pytest.raises(MarketDataError, match="timestamp"):
        fetch([[float("inf"), "100", "101", "99", "100", "1"]])


def test_broken_forming_bar_is_ignored_not_fatal() -> None:
    # Arena behaviour: the forming bar is skipped before any value checks.
    rows = [row(BUCKET, v="-1"), row(BUCKET - 60)]
    assert [c.ts for c in fetch(rows)] == [BUCKET - 60]
