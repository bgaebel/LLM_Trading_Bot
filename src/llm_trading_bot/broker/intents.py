"""The life of an order intent (Spec 012 AK8, ADR-0022; M2: ADR-0023) - a pure state machine.

It fixes now what M2 must never do: send an intent a second time. Bitvavo's clientOrderId is no
idempotency key, so a resend after a timeout may become a second real order. Once an intent may
have been sent (SENT, or PLANNED found after a restart), only asking the exchange (UNKNOWN ->
result) can move it on; nothing leads back to PLANNED or SENT.
"""

from collections.abc import Iterable

from llm_trading_bot.broker.contract import OrderState

S = OrderState

ALLOWED: dict[OrderState, frozenset[OrderState]] = {
    S.PLANNED: frozenset({S.SENT, S.UNKNOWN, S.ABORTED}),
    S.SENT: frozenset({S.FILLED, S.PARTIAL, S.EXPIRED, S.REJECTED, S.UNKNOWN}),
    # Only by looking the order up: an order that was found has executed or ended.
    S.UNKNOWN: frozenset({S.FILLED, S.PARTIAL, S.EXPIRED}),
    S.FILLED: frozenset(),
    S.PARTIAL: frozenset(),
    S.EXPIRED: frozenset(),
    S.REJECTED: frozenset(),
    S.ABORTED: frozenset(),
}

OPEN = frozenset({S.PLANNED, S.SENT, S.UNKNOWN})
"""States that block a new intent for the same depot and market."""


class IllegalTransition(ValueError):
    pass


def move(current: OrderState, new: OrderState) -> OrderState:
    if new not in ALLOWED[current]:
        raise IllegalTransition(f"{current.name} -> {new.name}")
    return new


def on_restart(state: OrderState) -> OrderState:
    """After a restart a PLANNED intent may have been sent just before the stop: treat it as
    UNKNOWN (clarify by lookup), never send it."""
    return S.UNKNOWN if state is S.PLANNED else state


def may_plan(states: Iterable[OrderState]) -> bool:
    """``states``: the intents of one depot and market. One open intent at a time."""
    return not any(s in OPEN for s in states)
