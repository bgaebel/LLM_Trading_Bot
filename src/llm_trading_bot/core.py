"""Core logic: keep it free of I/O so it stays unit-testable."""

from enum import Enum


class TradingMode(Enum):
    PAPER = "paper"
    LIVE = "live"


def parse_mode(value: str | None) -> TradingMode:
    """Resolve the configured trading mode; missing config means paper."""
    normalized = (value or "").strip().lower()
    if normalized in ("", TradingMode.PAPER.value):
        return TradingMode.PAPER
    if normalized == TradingMode.LIVE.value:
        # ADR-0004: live stays locked until its own spec and a superseding ADR exist,
        # so a stray config value can never place real orders.
        raise NotImplementedError("live trading is not enabled in this version")
    raise ValueError(f"unknown trading mode: {value!r}")
