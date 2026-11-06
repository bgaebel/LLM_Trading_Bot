"""Spec 008 AK1: a round 5 s after every minute change; overruns skip slots, never catch up."""

import pytest

from llm_trading_bot.service.schedule import OFFSET, PERIOD, next_slot, skipped

MINUTE = 1_800_000_000 - 1_800_000_000 % 60  # a whole UTC minute


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (MINUTE, MINUTE + 5),
        (MINUTE + 4.999, MINUTE + 5),
        (MINUTE + 5.0, MINUTE + 5),  # exactly on the slot: run now
        (MINUTE + 5.001, MINUTE + 65),
        (MINUTE + 59.9, MINUTE + 65),
    ],
)
def test_next_slot_at_the_boundaries(now: float, expected: float) -> None:
    assert next_slot(now) == expected


def test_slots_are_five_seconds_after_the_minute() -> None:
    assert (PERIOD, OFFSET) == (60, 5)
    assert next_slot(MINUTE + 10) % 60 == 5


def test_a_round_that_took_no_time_waits_for_the_next_minute() -> None:
    assert next_slot(MINUTE + 5, after=MINUTE + 5) == MINUTE + 65


def test_a_normal_round_runs_once_per_minute() -> None:
    slot = MINUTE + 5
    finished = slot + 2.5
    assert next_slot(finished, after=slot) == MINUTE + 65
    assert skipped(slot, MINUTE + 65) == 0


@pytest.mark.parametrize("overrun", [1, 3])
def test_an_overrun_skips_the_missed_slots_without_catching_up(overrun: int) -> None:
    slot = MINUTE + 5
    finished = slot + overrun * 60 + 0.5  # past `overrun` further slots
    following = next_slot(finished, after=slot)
    assert following == slot + (overrun + 1) * 60
    assert skipped(slot, following) == overrun


def test_a_clock_jump_back_never_repeats_a_slot() -> None:
    slot = MINUTE + 65
    assert next_slot(MINUTE + 1, after=slot) == MINUTE + 125
