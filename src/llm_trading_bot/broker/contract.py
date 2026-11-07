"""The broker contract (Spec 012, ADR-0022): no I/O, no implementation of any exchange.

``Broker`` is the seam on decision level. Its methods have the signatures of ``paper.execution``,
so ``PaperBroker`` is pure delegation. Note (ADR-0022): sizing, weight limits, the minimum order,
the hold/buy rules and the drawdown stop live *inside* those paper functions today; a live broker
needs them, so M2 first extracts them, proven by the golden fixtures, and only then plugs in.
"""

import sqlite3
from collections.abc import Mapping
from typing import Any, Protocol

from llm_trading_bot.core import TradingMode
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.execution import Costs, Quotes, RiskStop
from llm_trading_bot.strategy.signals import Signal


class Broker(Protocol):
    """Where a decision becomes a (virtual or, from M2 on, real) order.

    Caller rules are today's: ``record_decision`` opens its own write transaction, ``mark`` runs
    inside the caller's. A live broker therefore only *plans* inside a transaction - it stores
    PLANNED intents (``intents.py``) - and sends exclusively in ``dispatch``, outside of every
    transaction, exactly once per intent (ADR-0023). ``unresolved`` lists intents whose outcome
    is not booked yet; while there is one, no new order may be planned for that market.
    """

    @property
    def mode(self) -> TradingMode: ...

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
    ) -> bool: ...

    def mark(
        self, con: sqlite3.Connection, agent: str, qs: Quotes, now: str, costs: Costs
    ) -> RiskStop | None: ...

    def initialize_buyhold(
        self, con: sqlite3.Connection, qs: Quotes, costs: Costs, *, when: str
    ) -> int: ...

    def unresolved(self, con: sqlite3.Connection) -> tuple[str, ...]: ...

    def dispatch(self, con: sqlite3.Connection) -> None: ...
