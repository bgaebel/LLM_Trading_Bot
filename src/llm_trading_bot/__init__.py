"""LLM Trading Bot: trades BTC/EUR and ETH/EUR, paper mode first."""

import os

from llm_trading_bot.core import TradingMode, parse_mode

__all__ = ["TradingMode", "main", "parse_mode"]


def main() -> None:
    mode = parse_mode(os.environ.get("TRADING_MODE"))
    print(f"llm-trading-bot: mode={mode.value}")
