import pytest

from llm_trading_bot.core import TradingMode, parse_mode


@pytest.mark.parametrize("value", [None, "", "  ", "paper", " PAPER "])
def test_missing_or_paper_config_resolves_to_paper(value: str | None) -> None:
    assert parse_mode(value) is TradingMode.PAPER


def test_live_mode_is_refused() -> None:
    with pytest.raises(NotImplementedError):
        parse_mode("live")


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_mode("demo")
