"""The paper broker: pure delegation to the Arena port in ``paper.execution`` (ADR-0007, ADR-0022).

It adds no logic of its own, so the golden fixtures prove it bit-identical to the Arena
(tests/broker/test_paper_broker_golden.py). In M1 the cycles still call ``paper.execution``
directly; they move to the broker in M2.
"""

import sqlite3
from collections.abc import Mapping
from typing import Any

from llm_trading_bot.core import TradingMode
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper import execution
from llm_trading_bot.paper.execution import Costs, Quotes, RiskStop
from llm_trading_bot.strategy.signals import Signal


class PaperBroker:
    @property
    def mode(self) -> TradingMode:
        return TradingMode.PAPER

    def record_decision(
        self,
        con: sqlite3.Connection,
        agent: str,
        symbol: Symbol,
        bar_ts: int,
        model: str,
        sig: Signal,
        data: Mapping[str, Any],
        *,
        costs: Costs,
        now: str,
        qs: Quotes | None,
        status: str = "OK",
        stops: list[RiskStop] | None = None,
        partial: bool = False,
    ) -> bool:
        return execution.record_decision(
            con,
            agent,
            symbol,
            bar_ts,
            model,
            sig,
            data,
            costs=costs,
            now=now,
            qs=qs,
            status=status,
            stops=stops,
            partial=partial,
        )

    def mark(
        self, con: sqlite3.Connection, agent: str, qs: Quotes, now: str, costs: Costs
    ) -> RiskStop | None:
        return execution.mark(con, agent, qs, now, costs)

    def initialize_buyhold(
        self, con: sqlite3.Connection, qs: Quotes, costs: Costs, *, when: str
    ) -> int:
        return execution.initialize_buyhold(con, qs, costs, when=when)

    def unresolved(self, con: sqlite3.Connection) -> tuple[str, ...]:
        # A virtual fill is booked in the same transaction as its decision: nothing can be open.
        return ()

    def dispatch(self, con: sqlite3.Connection) -> None:
        # Nothing is ever sent anywhere in paper mode.
        return None
