"""Broker seam for paper and (from M2 on) live execution (Spec 012, ADR-0022).

ADR-0004: in M1 only the paper broker exists; asking for a live one raises ``LiveTradingLocked``.
"""

from llm_trading_bot.broker.contract import Broker
from llm_trading_bot.broker.live import LiveTradingLocked, refuse_live
from llm_trading_bot.broker.paper import PaperBroker
from llm_trading_bot.core import TradingMode

__all__ = ["Broker", "LiveTradingLocked", "PaperBroker", "make_broker"]


def make_broker(mode: TradingMode) -> Broker:
    if mode is TradingMode.PAPER:
        return PaperBroker()
    # ADR-0004: whatever reached this point, live is refused here as well.
    refuse_live()
