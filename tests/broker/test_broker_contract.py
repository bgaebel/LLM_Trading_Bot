"""Spec 012 AK1: the broker contract - the paper broker fulfils it (checked by mypy)."""

from llm_trading_bot.broker import Broker, PaperBroker, make_broker
from llm_trading_bot.core import TradingMode

# mypy proves the structural fit: a missing method or a changed signature fails the type check.
_: Broker = PaperBroker()


def test_make_broker_gives_the_paper_broker_for_paper() -> None:
    broker = make_broker(TradingMode.PAPER)
    assert isinstance(broker, PaperBroker) and broker.mode is TradingMode.PAPER
