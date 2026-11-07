"""The broker contract (Spec 012, ADR-0022): no I/O, no implementation of any exchange.

``Broker`` is the seam on decision level. Its methods have the signatures of ``paper.execution``,
so ``PaperBroker`` is pure delegation. Note (ADR-0022): sizing, weight limits, the minimum order,
the hold/buy rules and the drawdown stop live *inside* those paper functions today; a live broker
needs them, so M2 first extracts them, proven by the golden fixtures, and only then plugs in.

The exchange side (``OrderIntent`` ... ``OrderExchange``) is decided in ADR-0022 (M2 proposal:
ADR-0023), built on the Bitvavo trading API as documented on 2026-11-07
(docs/recherche/bitvavo-trading-api-2026-11.md).
Money is ``Decimal`` only. Nothing here can place an order: ``OrderExchange`` has no
implementation, and tests/broker/test_no_order_path.py fails if one appears (ADR-0004).
"""

import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from enum import Enum
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
    transaction, exactly once per intent (ADR-0022). ``unresolved`` lists intents whose outcome
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


# ------------------------------------------------------------------ the exchange side (M2 draft)


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"
    RISK_SELL = "RISK_SELL"
    """The drawdown stop's forced sale: its own intent, never retried (ADR-0021, ADR-0023)."""


class OrderState(Enum):
    PLANNED = "PLANNED"  # stored and committed, not sent
    SENT = "SENT"  # one POST went out, outcome not known yet
    FILLED = "FILLED"  # executed completely
    PARTIAL = "PARTIAL"  # executed in part, the rest expired or was cancelled: never topped up
    EXPIRED = "EXPIRED"  # an order existed, nothing executed (expired/canceled)
    REJECTED = "REJECTED"  # refused without an order (HTTP 400): books nothing, never retried
    UNKNOWN = "UNKNOWN"  # timeout, 5xx, 109, 429 or a restart after PLANNED: market blocked
    ABORTED = "ABORTED"  # given up before sending, provably never sent


def _money(name: str, value: object, *, positive: bool = True) -> Decimal:
    # type() and not isinstance(): bool is an int, and a float would carry binary rounding into
    # an amount that is sent to the exchange.
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal, not {type(value).__name__}")
    if not value.is_finite() or (positive and value <= 0):
        raise ValueError(f"{name} must be finite{' and > 0' if positive else ''}: {value}")
    return value


def new_client_order_id() -> uuid.UUID:
    """A fresh random UUID4 per intent. Bitvavo's clientOrderId is unique among open orders only
    and reusable after completion - it identifies, it does not make a resend safe (ADR-0022)."""
    return uuid.uuid4()


@dataclass(frozen=True)
class OrderIntent:
    """What a decision wants, before it becomes a Bitvavo order. No order type yet: whether M2
    sends a market order with ``amountQuote`` or an IOC limit with ``amount`` and ``price`` is
    open in ADR-0023; the live broker converts with ``MarketRules``."""

    agent: str
    symbol: Symbol
    side: OrderSide
    client_order_id: uuid.UUID
    operator_id: int
    amount_quote: Decimal | None = None
    """BUY: EUR budget."""
    amount_base: Decimal | None = None
    """SELL / RISK_SELL: quantity in the base asset."""

    def __post_init__(self) -> None:
        if not self.agent:
            raise ValueError("agent missing")
        if type(self.operator_id) is not int or self.operator_id <= 0:
            raise ValueError(f"operator_id must be an int > 0: {self.operator_id!r}")
        if not isinstance(self.client_order_id, uuid.UUID) or self.client_order_id.version != 4:
            raise ValueError(f"client_order_id must be a UUID4: {self.client_order_id!r}")
        if (self.amount_quote is None) == (self.amount_base is None):
            raise ValueError("exactly one of amount_quote and amount_base")
        if self.side is OrderSide.BUY:
            if self.amount_quote is None:
                raise ValueError("BUY carries an EUR budget (amount_quote)")
            _money("amount_quote", self.amount_quote)
        else:
            if self.amount_base is None:
                raise ValueError(f"{self.side.name} carries a base quantity (amount_base)")
            _money("amount_base", self.amount_base)


@dataclass(frozen=True)
class ExecutedFill:
    fill_id: str
    amount_base: Decimal
    price: Decimal
    settled: bool
    fee: Decimal | None = None
    """Known only once settled; negative for a rebate."""
    fee_currency: str | None = None

    def __post_init__(self) -> None:
        if not self.fill_id:
            raise ValueError("fill_id missing")
        _money("amount_base", self.amount_base)
        _money("price", self.price)
        if self.settled and (self.fee is None or not self.fee_currency):
            raise ValueError("a settled fill carries fee and fee_currency")
        if self.fee is not None:
            _money("fee", self.fee, positive=False)


# Bitvavo's terminal order statuses since v2.10.0 (active: new, awaitingTrigger, partiallyFilled).
FINAL_STATUSES = ("filled", "expired", "canceled")


def classify(exchange_status: str, fills: tuple[ExecutedFill, ...]) -> OrderState:
    """Internal state of a terminal Bitvavo order: by what was executed, not by the status name
    (an IOC that executed in part ends 'expired' or 'canceled' WITH fills)."""
    if exchange_status not in FINAL_STATUSES:
        raise ValueError(f"order not terminal: {exchange_status!r}")
    if exchange_status == "filled":
        if not fills:
            raise ValueError("'filled' without fills")
        return OrderState.FILLED
    return OrderState.PARTIAL if fills else OrderState.EXPIRED


RESULT_STATES = frozenset(
    {
        OrderState.FILLED,
        OrderState.PARTIAL,
        OrderState.EXPIRED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    }
)


@dataclass(frozen=True)
class OrderOutcome:
    client_order_id: uuid.UUID
    state: OrderState
    fills: tuple[ExecutedFill, ...] = ()
    exchange_order_id: str | None = None
    """Only unique per market at Bitvavo."""
    exchange_status: str = ""
    restatement_reason: str = ""
    reject_code: int | None = None

    def __post_init__(self) -> None:
        if self.state not in RESULT_STATES:
            raise ValueError(f"not a result state: {self.state.name}")
        with_fills = self.state in (OrderState.FILLED, OrderState.PARTIAL)
        if with_fills and not self.fills:
            raise ValueError(f"{self.state.name} without fills")
        if not with_fills and self.fills:
            raise ValueError(f"{self.state.name} with fills")
        if len({f.fill_id for f in self.fills}) != len(self.fills):
            raise ValueError("duplicate fill_id")

    @property
    def filled_base(self) -> Decimal:
        return sum((f.amount_base for f in self.fills), Decimal(0))

    @property
    def notional(self) -> Decimal:
        return sum((f.amount_base * f.price for f in self.fills), Decimal(0))


def _text_decimal(raw: Mapping[str, Any], key: str) -> Decimal:
    value = raw[key]
    # Bitvavo sends amounts, quantities and tickSize as JSON strings; a float here would mean a
    # changed API or a broken parser - refuse instead of guessing.
    if type(value) is not str:
        raise TypeError(f"{key} must be a string, not {type(value).__name__}")
    return _money(key, Decimal(value))


def _count(raw: Mapping[str, Any], key: str) -> int:
    value = raw[key]
    if type(value) is not int or value < 0:
        raise TypeError(f"{key} must be a non-negative int: {value!r}")
    return value


@dataclass(frozen=True)
class MarketRules:
    """Order limits of one market from the public ``GET /markets``; fetched fresh before every
    order in M2, never hard-coded (minOrderInBaseAsset may follow the price)."""

    symbol: Symbol
    status: str
    min_base: Decimal
    min_quote: Decimal
    quantity_decimals: int
    notional_decimals: int
    tick_size: Decimal
    max_open_orders: int

    @classmethod
    def from_markets(cls, raw: Mapping[str, Any]) -> "MarketRules":
        return cls(
            Symbol(raw["market"]),
            str(raw["status"]),
            _text_decimal(raw, "minOrderInBaseAsset"),
            _text_decimal(raw, "minOrderInQuoteAsset"),
            _count(raw, "quantityDecimals"),
            _count(raw, "notionalDecimals"),
            _text_decimal(raw, "tickSize"),
            _count(raw, "maxOpenOrders"),
        )

    def floor_base(self, amount: Decimal) -> Decimal:
        """Never more than wanted: an amount is only ever rounded down."""
        step = Decimal(1).scaleb(-self.quantity_decimals)
        return _money("amount", amount).quantize(step, ROUND_DOWN)

    def floor_quote(self, amount: Decimal) -> Decimal:
        step = Decimal(1).scaleb(-self.notional_decimals)
        return _money("amount", amount).quantize(step, ROUND_DOWN)

    def limit_price(self, side: OrderSide, bound: Decimal) -> Decimal:
        """A price on the tick grid that never crosses ``bound``: a buy never pays more, a sale
        never takes less (ADR-0023: slippage capped)."""
        steps = _money("bound", bound) / self.tick_size
        rounding = ROUND_FLOOR if side is OrderSide.BUY else ROUND_CEILING
        return steps.to_integral_value(rounding) * self.tick_size

    def problems(self, *, base: Decimal | None = None, quote: Decimal | None = None) -> list[str]:
        """Reasons the exchange would refuse the order; empty if none is known."""
        out = []
        if self.status != "trading":
            out.append(f"{self.symbol.market} status {self.status}")
        if base is not None and base < self.min_base:
            out.append(f"amount {base} below the minimum {self.min_base}")
        if quote is not None and quote < self.min_quote:
            out.append(f"amount {quote} EUR below the minimum {self.min_quote} EUR")
        return out


@dataclass(frozen=True)
class AccountBalances:
    available: Mapping[str, Decimal]
    in_order: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        for name, values in (("available", self.available), ("in_order", self.in_order)):
            for symbol, value in values.items():
                if _money(f"{name}[{symbol}]", value, positive=False) < 0:
                    raise ValueError(f"{name}[{symbol}] negative: {value}")


class OrderExchange(Protocol):
    """M2 only (ADR-0023). Implementing it before an ADR supersedes ADR-0004 fails
    tests/broker/test_no_order_path.py."""

    def market_rules(self, symbol: Symbol) -> MarketRules: ...

    def balances(self) -> AccountBalances: ...

    def submit(self, intent: OrderIntent, *, limit_price: Decimal | None) -> OrderOutcome:
        """Exactly one send; a timeout, 5xx, code 109 or HTTP 429 gives UNKNOWN, never a retry."""
        ...

    def lookup(self, symbol: Symbol, client_order_id: uuid.UUID) -> OrderOutcome: ...

    def cancel_all(self, symbol: Symbol, operator_id: int) -> None: ...
