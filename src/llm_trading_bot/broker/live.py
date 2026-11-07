"""Live trading is locked (ADR-0004, ADR-0022).

This module holds no exchange code on purpose: no class with methods, no import beyond ``typing``
(tests/broker/test_live_locked.py checks both). M2 replaces it only together with a spec and an
ADR that supersedes ADR-0004.
"""

from typing import NoReturn


class LiveTradingLocked(NotImplementedError):
    """Raised for every attempt to build a live broker before M2."""


def refuse_live() -> NoReturn:
    # ADR-0004: live stays locked even if someone gets past core.parse_mode.
    raise LiveTradingLocked("live trading is locked until M2 (ADR-0004)")
