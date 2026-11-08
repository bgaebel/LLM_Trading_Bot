"""Against the real public Bitvavo API. Excluded by default; run with `uv run pytest -m net`."""

import time

import pytest

from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import QUOTED, SYMBOLS, Symbol
from llm_trading_bot.market.snapshot import DEFAULT_MIN_BARS, INTERVALS, build_snapshots

pytestmark = pytest.mark.net


@pytest.fixture(scope="module")
def client() -> BitvavoClient:
    return BitvavoClient()


def test_live_quotes_for_both_markets(client: BitvavoClient) -> None:
    quotes = client.quotes()
    assert set(quotes) == set(QUOTED)  # Spec 020: BTC-USDC and USDC-EUR from the same book


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_live_candles_are_closed_and_sufficient(client: BitvavoClient, symbol: Symbol) -> None:
    now = int(time.time())
    for tf in INTERVALS:
        candles = client.closed_candles(symbol, tf, now_epoch=now)
        assert len(candles) >= DEFAULT_MIN_BARS[tf], (symbol, tf, len(candles))
        assert candles[-1].ts + tf * 60 <= now


def test_live_snapshots_validate(client: BitvavoClient) -> None:
    problems: dict[Symbol, str] = {}
    snaps = build_snapshots(client, on_error=lambda s, e: problems.__setitem__(s, str(e)))
    assert set(snaps) == {Symbol.BTCEUR, Symbol.ETHEUR}, problems
