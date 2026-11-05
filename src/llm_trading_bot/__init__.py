"""LLM Trading Bot: trades BTC/EUR and ETH/EUR, paper mode first."""

from llm_trading_bot.cli import main
from llm_trading_bot.core import TradingMode, parse_mode

__all__ = ["TradingMode", "main", "parse_mode"]
