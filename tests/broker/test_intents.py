"""Spec 012 AK8: the intent state machine never leads back to sending (ADR-0023)."""

from itertools import product

import pytest

from llm_trading_bot.broker.contract import OrderState
from llm_trading_bot.broker.intents import ALLOWED, IllegalTransition, may_plan, move, on_restart

S = OrderState

# The spec's table, written out independently of the code under test.
EXPECTED = {
    (S.PLANNED, S.SENT),
    (S.PLANNED, S.UNKNOWN),
    (S.PLANNED, S.ABORTED),
    (S.SENT, S.FILLED),
    (S.SENT, S.PARTIAL),
    (S.SENT, S.EXPIRED),
    (S.SENT, S.REJECTED),
    (S.SENT, S.UNKNOWN),
    (S.UNKNOWN, S.FILLED),
    (S.UNKNOWN, S.PARTIAL),
    (S.UNKNOWN, S.EXPIRED),
}


@pytest.mark.parametrize(("a", "b"), list(product(S, S)), ids=lambda s: s.name)
def test_every_pair_of_states(a: OrderState, b: OrderState) -> None:
    if (a, b) in EXPECTED:
        assert move(a, b) is b
    else:
        with pytest.raises(IllegalTransition):
            move(a, b)


def test_the_table_covers_every_state() -> None:
    assert set(ALLOWED) == set(S)


def test_nothing_ever_returns_to_planned_or_sent() -> None:
    for targets in ALLOWED.values():
        assert S.PLANNED not in targets
    assert all(S.SENT not in ALLOWED[s] for s in S if s is not S.PLANNED)


def test_results_are_final() -> None:
    for final in (S.FILLED, S.PARTIAL, S.EXPIRED, S.REJECTED, S.ABORTED):
        assert ALLOWED[final] == frozenset()


def test_a_planned_intent_found_after_a_restart_is_unknown_never_resent() -> None:
    assert on_restart(S.PLANNED) is S.UNKNOWN
    for other in S:
        if other is not S.PLANNED:
            assert on_restart(other) is other


@pytest.mark.parametrize(
    ("states", "allowed"),
    [
        ((), True),
        ((S.FILLED, S.REJECTED, S.EXPIRED, S.PARTIAL, S.ABORTED), True),
        ((S.FILLED, S.PLANNED), False),
        ((S.SENT,), False),
        ((S.UNKNOWN,), False),
    ],
)
def test_one_open_intent_per_depot_and_market(
    states: tuple[OrderState, ...], allowed: bool
) -> None:
    assert may_plan(states) is allowed
