import math

import pytest

from llm_trading_bot.market.models import SYMBOLS, MarketDataError, Quote, Symbol


def test_symbols_map_to_bitvavo_markets() -> None:
    assert [s.market for s in SYMBOLS] == ["BTC-EUR", "ETH-EUR"]
    assert Symbol("ETH-EUR") is Symbol.ETHEUR


def test_quote_mid_and_spread() -> None:
    q = Quote(100.0, 100.1)
    assert q.mid == pytest.approx(100.05)
    assert q.spread_bps == pytest.approx(9.995, rel=1e-3)


def test_valid_quote_passes() -> None:
    Quote(66000.0, 66005.0).validate()


@pytest.mark.parametrize(
    ("bid", "ask"),
    [(0.0, 1.0), (-1.0, 1.0), (101.0, 100.0), (math.nan, 1.0), (1.0, math.inf)],
)
def test_invalid_bid_ask_rejected(bid: float, ask: float) -> None:
    with pytest.raises(MarketDataError):
        Quote(bid, ask).validate()


def test_spread_above_limit_rejected() -> None:
    # 100 vs 100.6 is roughly 60 bp, above the 50 bp default.
    with pytest.raises(MarketDataError, match="spread"):
        Quote(100.0, 100.6).validate()


def test_spread_limit_is_configurable() -> None:
    Quote(100.0, 100.6).validate(max_spread_bps=70.0)


def test_market_data_error_is_a_value_error() -> None:
    assert issubclass(MarketDataError, ValueError)
