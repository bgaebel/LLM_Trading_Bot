"""When the next round starts (ADR-0017): every minute, a few seconds after the minute changes.

Pure functions on epoch seconds so the timing can be tested without waiting.
"""

import math

PERIOD = 60
# Bitvavo closes the 1-minute candle at :00; a few seconds later it is published everywhere.
OFFSET = 5


def next_slot(
    now: float, *, after: float | None = None, period: int = PERIOD, offset: int = OFFSET
) -> float:
    """The first slot (``k * period + offset``) at or after ``now`` and later than ``after``.

    ``after`` is the slot of the previous round, so a round that took no time never runs twice
    in one slot. A round that overran waits for the next free slot - missed rounds are never made
    up, because a burst of rounds would only see the same closed candles again.
    """
    slot = math.ceil((now - offset) / period) * period + offset
    if after is not None and slot <= after:
        slot = math.floor((after - offset) / period) * period + offset + period
    return slot


def skipped(previous: float, slot: float, *, period: int = PERIOD) -> int:
    """How many slots between the previous round's slot and ``slot`` passed without a round."""
    return max(0, round((slot - previous) / period) - 1)
