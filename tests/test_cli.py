import io
import re

import pytest

from fakes import VALID_BOOK, FakeTransport
from llm_trading_bot.cli import EXIT_FETCH_FAILED, EXIT_INVALID, EXIT_OK, main, run_market
from llm_trading_bot.market.bitvavo import TIMEFRAMES, BitvavoClient
from llm_trading_bot.market.http import HTTPFailure

NOW = 1_800_000_030
MINUTES = {name: tf for tf, name in TIMEFRAMES.items()}


def bitvavo_handler(*, eth_candles_fail: bool = False, quotes_fail: bool = False):  # type: ignore[no-untyped-def]
    """Answers like Bitvavo: newest candle first, millisecond stamps, string values."""

    def handler(url: str) -> object:
        if url.endswith("/ticker/book"):
            if quotes_fail:
                raise HTTPFailure(503)
            return VALID_BOOK
        if eth_candles_fail and "/ETH-EUR/" in url:
            raise HTTPFailure(500)
        tf = MINUTES[re.search(r"interval=(\w+)", url).group(1)]  # type: ignore[union-attr]
        step = tf * 60
        last = (NOW // step - 1) * step
        return [[(last - i * step) * 1000, "100", "101", "99", "100", "1"] for i in range(200)]

    return handler


def run(**kwargs: bool) -> tuple[int, str]:
    out = io.StringIO()
    client = BitvavoClient(FakeTransport(bitvavo_handler(**kwargs)))
    code = run_market(client, out=out, clock=lambda: NOW)
    return code, out.getvalue()


def test_market_report_ok() -> None:
    code, text = run()
    assert code == EXIT_OK
    assert text.count("snapshot: OK") == 2
    assert "BTC-EUR" in text and "ETH-EUR" in text
    assert "bid 66000.00  ask 66005.00" in text
    assert "last closed 2027-01-15 08:00:00 UTC" in text


def test_market_report_shows_reason_and_exit_1_for_invalid_market() -> None:
    code, text = run(eth_candles_fail=True)
    assert code == EXIT_INVALID
    assert text.count("snapshot: OK") == 1
    assert "snapshot: INVALID - candles: HTTPFailure: HTTP 500" in text


def test_market_report_exit_2_when_quotes_fail() -> None:
    code, text = run(quotes_fail=True)
    assert code == EXIT_FETCH_FAILED
    assert "quotes FAILED: HTTPFailure: HTTP 503" in text


def test_default_command_prints_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "llm-trading-bot: mode=paper"
