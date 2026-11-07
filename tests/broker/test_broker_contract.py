"""Spec 012 AK1/AK7: the broker contract and the live types (M2 draft, ADR-0023)."""

import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from llm_trading_bot.broker import Broker, PaperBroker, make_broker
from llm_trading_bot.broker.contract import (
    AccountBalances,
    ExecutedFill,
    MarketRules,
    OrderIntent,
    OrderOutcome,
    OrderSide,
    OrderState,
    classify,
    new_client_order_id,
)
from llm_trading_bot.core import TradingMode
from llm_trading_bot.market.models import Symbol

# mypy proves the structural fit: a missing method or a changed signature fails the type check.
_: Broker = PaperBroker()

MARKETS = json.loads(
    (Path(__file__).parent / "fixtures" / "markets_2026-11.json").read_text(encoding="utf-8")
)["responses"]
D = Decimal


def test_make_broker_gives_the_paper_broker_for_paper() -> None:
    broker = make_broker(TradingMode.PAPER)
    assert isinstance(broker, PaperBroker) and broker.mode is TradingMode.PAPER


# ------------------------------------------------------------------ OrderIntent


def intent(**changes: Any) -> OrderIntent:
    base: dict[str, Any] = {
        "agent": "gpt-luna",
        "symbol": Symbol.BTCEUR,
        "side": OrderSide.BUY,
        "client_order_id": new_client_order_id(),
        "operator_id": 1201,
        "amount_quote": D("25.00"),
    }
    return OrderIntent(**{**base, **changes})


def test_valid_intents() -> None:
    assert intent().client_order_id.version == 4
    sell = intent(side=OrderSide.SELL, amount_quote=None, amount_base=D("0.0004"))
    risk = intent(side=OrderSide.RISK_SELL, amount_quote=None, amount_base=D("0.0004"))
    assert sell.amount_base == risk.amount_base == D("0.0004")


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_quote": None},  # no amount
        {"amount_base": D("0.1")},  # both amounts
        {"amount_quote": D("0")},
        {"amount_quote": D("-5")},
        {"amount_quote": D("NaN")},
        {"amount_quote": D("Infinity")},
        {"amount_quote": 25.0},  # float
        {"amount_quote": 25},  # int
        {"amount_quote": True},
        {"amount_quote": None, "amount_base": D("0.1")},  # BUY with a base quantity
        {"side": OrderSide.SELL},  # SELL with an EUR budget
        {"side": OrderSide.RISK_SELL},
        {"operator_id": 0},
        {"operator_id": -3},
        {"operator_id": True},
        {"operator_id": "1201"},
        {"operator_id": 1201.0},
        {"client_order_id": uuid.uuid1()},
        {"client_order_id": uuid.uuid5(uuid.NAMESPACE_DNS, "x")},
        {"client_order_id": "2b0e4c9e-5d8a-4f6e-9b7a-0c1d2e3f4a5b"},
        {"agent": ""},
    ],
)
def test_invalid_intents_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        intent(**changes)


def test_client_order_ids_are_fresh() -> None:
    ids = {new_client_order_id() for _ in range(1000)}
    assert len(ids) == 1000 and all(i.version == 4 for i in ids)


# ------------------------------------------------------------------ fills and outcomes


def fill(
    fill_id: str = "f1", amount: str = "0.001", settled: bool = True, **kw: Any
) -> ExecutedFill:
    fee = kw.pop("fee", D("0.15") if settled else None)
    currency = kw.pop("fee_currency", "EUR" if settled else None)
    return ExecutedFill(fill_id, D(amount), D("60000"), settled, fee, currency, **kw)


def test_fills_need_their_fee_once_settled() -> None:
    assert fill(settled=False).fee is None
    assert fill(fee=D("-0.01")).fee == D("-0.01")  # rebate


FLOAT: Any = 60000.0


@pytest.mark.parametrize(
    "build",
    [
        lambda: fill(fee=None),
        lambda: fill(fee_currency=""),
        lambda: fill(fill_id=""),
        lambda: fill(amount="0"),
        lambda: ExecutedFill("f", D("1"), FLOAT, False),
        lambda: fill(fee=0.15),
    ],
)
def test_invalid_fills_are_refused(build: Callable[[], ExecutedFill]) -> None:
    with pytest.raises((TypeError, ValueError)):
        build()


@pytest.mark.parametrize(
    ("status", "fills", "state"),
    [
        ("filled", (fill(),), OrderState.FILLED),
        ("expired", (fill(),), OrderState.PARTIAL),  # IOC executed in part
        ("canceled", (fill(),), OrderState.PARTIAL),  # price protection cancelled the rest
        ("expired", (), OrderState.EXPIRED),
        ("canceled", (), OrderState.EXPIRED),
    ],
)
def test_state_follows_what_was_executed(
    status: str, fills: tuple[ExecutedFill, ...], state: OrderState
) -> None:
    assert classify(status, fills) is state


@pytest.mark.parametrize("status", ["new", "partiallyFilled", "awaitingTrigger", "rejected", ""])
def test_active_or_unknown_statuses_are_not_classified(status: str) -> None:
    with pytest.raises(ValueError):
        classify(status, (fill(),))


def test_filled_without_fills_is_not_classified() -> None:
    with pytest.raises(ValueError):
        classify("filled", ())


def outcome(state: OrderState, fills: tuple[ExecutedFill, ...] = (), **kw: Any) -> OrderOutcome:
    return OrderOutcome(new_client_order_id(), state, fills, **kw)


def test_outcome_keeps_the_exchange_status_and_reason_unchanged() -> None:
    o = outcome(
        OrderState.PARTIAL,
        (fill(),),
        exchange_order_id="95e9-a",
        exchange_status="canceled",
        restatement_reason="cancelOnExecutionPriceProtection",
    )
    assert (o.exchange_status, o.restatement_reason, o.exchange_order_id) == (
        "canceled",
        "cancelOnExecutionPriceProtection",
        "95e9-a",
    )


def test_outcomes_match_their_fills() -> None:
    o = outcome(
        OrderState.PARTIAL, (fill("a", "0.001"), fill("b", "0.002")), exchange_status="expired"
    )
    assert o.filled_base == D("0.003") and o.notional == D("180.000")
    assert outcome(OrderState.REJECTED, reject_code=216).filled_base == 0


@pytest.mark.parametrize(
    ("state", "fills"),
    [
        (OrderState.FILLED, ()),
        (OrderState.PARTIAL, ()),
        (OrderState.EXPIRED, (fill(),)),
        (OrderState.REJECTED, (fill(),)),
        (OrderState.UNKNOWN, (fill(),)),
        (OrderState.PLANNED, ()),  # not a result
        (OrderState.SENT, ()),
        (OrderState.ABORTED, ()),
        (OrderState.FILLED, (fill("x"), fill("x"))),  # booked twice
    ],
)
def test_impossible_outcomes_are_refused(
    state: OrderState, fills: tuple[ExecutedFill, ...]
) -> None:
    with pytest.raises(ValueError):
        outcome(state, fills)


# ------------------------------------------------------------------ market rules (/markets)


@pytest.fixture(params=["BTC-EUR", "ETH-EUR"])
def rules(request: pytest.FixtureRequest) -> MarketRules:
    return MarketRules.from_markets(MARKETS[request.param])


def test_rules_come_from_the_public_markets_answer() -> None:
    btc = MarketRules.from_markets(MARKETS["BTC-EUR"])
    eth = MarketRules.from_markets(MARKETS["ETH-EUR"])
    assert (btc.symbol, btc.status, btc.min_quote, btc.tick_size) == (
        Symbol.BTCEUR,
        "trading",
        D("5.00"),
        D("1.00"),
    )
    assert (btc.min_base, btc.quantity_decimals, btc.notional_decimals) == (D("0.00007294"), 8, 2)
    assert (eth.tick_size, eth.max_open_orders) == (D("0.010"), 100)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tickSize", 1.0),
        ("minOrderInQuoteAsset", 5),
        ("minOrderInBaseAsset", "-1"),
        ("tickSize", "0"),
        ("quantityDecimals", "8"),
        ("quantityDecimals", 8.0),
        ("quantityDecimals", True),
        ("notionalDecimals", -1),
    ],
)
def test_unexpected_markets_values_are_refused(key: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        MarketRules.from_markets({**MARKETS["BTC-EUR"], key: value})


def test_amounts_are_only_rounded_down(rules: MarketRules) -> None:
    assert rules.floor_base(D("0.123456789999")) == D("0.12345678")
    assert rules.floor_quote(D("25.999")) == D("25.99")
    assert rules.floor_base(D("0.00000001")) == D("0.00000001")


@pytest.mark.parametrize(
    ("bound", "buy", "sell"),
    [
        ("60123.70", "60123.00", "60124.00"),
        ("60123.00", "60123.00", "60123.00"),  # on the grid already
        ("60123.01", "60123.00", "60124.00"),
    ],
)
def test_btc_limit_price_never_crosses_the_bound(bound: str, buy: str, sell: str) -> None:
    btc = MarketRules.from_markets(MARKETS["BTC-EUR"])
    assert btc.limit_price(OrderSide.BUY, D(bound)) == D(buy)
    assert btc.limit_price(OrderSide.SELL, D(bound)) == D(sell)
    assert btc.limit_price(OrderSide.RISK_SELL, D(bound)) == D(sell)


def test_eth_limit_price_on_its_tick() -> None:
    eth = MarketRules.from_markets(MARKETS["ETH-EUR"])
    assert eth.limit_price(OrderSide.BUY, D("3012.3456")) == D("3012.34")
    assert eth.limit_price(OrderSide.SELL, D("3012.3456")) == D("3012.35")


def test_problems_name_minimum_and_market_status() -> None:
    btc = MarketRules.from_markets(MARKETS["BTC-EUR"])
    assert btc.problems(base=D("0.00007294"), quote=D("5.00")) == []
    assert len(btc.problems(base=D("0.00007293"), quote=D("4.99"))) == 2
    halted = MarketRules.from_markets({**MARKETS["BTC-EUR"], "status": "halted"})
    assert halted.problems() == ["BTC-EUR status halted"]


def test_balances_are_decimal_and_not_negative() -> None:
    assert AccountBalances({"EUR": D("10")}, {"BTC": D("0")}).available["EUR"] == 10
    bads: list[dict[str, Any]] = [{"EUR": 10.0}, {"EUR": D("-1")}]
    for bad in bads:
        with pytest.raises((TypeError, ValueError)):
            AccountBalances(bad, {})
