import io

import pytest

from fakes import FakeTransport, bitvavo_handler
from llm_trading_bot.cli import (
    EXIT_FETCH_FAILED,
    EXIT_INVALID,
    EXIT_OK,
    main,
    run_market,
    run_signals,
)
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.strategy.signals import STRATEGIES

NOW = 1_800_000_030


def run(**kwargs: bool) -> tuple[int, str]:
    out = io.StringIO()
    client = BitvavoClient(FakeTransport(bitvavo_handler(NOW, **kwargs)))
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


def run_sig(**kwargs: bool) -> tuple[int, str]:
    out = io.StringIO()
    client = BitvavoClient(FakeTransport(bitvavo_handler(NOW, **kwargs)))
    code = run_signals(client, out=out, clock=lambda: NOW)
    return code, out.getvalue()


def test_signals_report_lists_every_strategy_for_both_markets() -> None:
    code, text = run_sig()
    assert code == EXIT_OK
    for name in STRATEGIES:
        assert text.count(f"  {name:<9}") == 2, name
    assert "strategy v0.3.1" in text
    assert "regime" in text


def test_signals_report_names_invalid_market_and_exits_1() -> None:
    code, text = run_sig(eth_candles_fail=True)
    assert code == EXIT_INVALID
    assert "ETH-EUR: no signals - HTTPFailure: HTTP 500" in text
    assert text.count("  trend    ") == 1


def test_signals_report_exit_2_when_no_market_is_usable() -> None:
    code, text = run_sig(quotes_fail=True)
    assert code == EXIT_FETCH_FAILED
    assert text.count("no signals - HTTPFailure: HTTP 503") == 2
