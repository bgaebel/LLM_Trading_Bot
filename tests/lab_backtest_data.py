"""Synthetic candle archives for the backtest tests (Spec 016 AK4, AK9, AK11).

Rows go through the archive's own writer (``Archive.store_window``), so the backtest reads
exactly the format a real ``trading-bot lab fetch`` leaves behind. Prices follow smooth
deterministic paths: no randomness, the same rows on every platform.
"""

import math
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from llm_trading_bot.lab.archive import Archive, StoredRow, step_of, windows
from llm_trading_bot.lab.model import LEAD_ROWS, MARKETS
from llm_trading_bot.market.snapshot import INTERVALS

PriceFn = Callable[[str, int], float]
"""(market, epoch) -> price."""
BASE = {"BTC-EUR": 30_000.0, "ETH-EUR": 2_000.0}
T0 = 1_672_617_600
"""2023-01-02T00:00:00Z: inside the graded years and aligned to every interval (4h too)."""
HOUR = 3600
DAY = 86_400
FAR = 2_000_000_000
"""``fetched_at`` far after every row: every window counts as done."""


def wavy(market: str, ts: int) -> float:
    """Slow swings with faster ripples: enough movement for the strategies to trade now and
    then, smooth enough that nothing looks like a data fault."""
    phase = 0.7 if market == "ETH-EUR" else 0.0
    x = (
        1
        + 0.06 * math.sin(2 * math.pi * ts / (4 * DAY) + phase)
        + 0.012 * math.sin(2 * math.pi * ts / (7 * HOUR) + 2 * phase)
        + 0.003 * math.sin(2 * math.pi * ts / (37 * 60))
    )
    return BASE[market] * x


def crash(at: int, depth: float = 0.30, minutes: int = 10, rate: float = 0.04) -> PriceFn:
    """A steady uptrend (``rate`` per day) that falls by ``depth`` within ``minutes`` at ``at``
    and stays down: long trend depots hit the drawdown stop."""

    def price(market: str, ts: int) -> float:
        p = BASE[market] * math.exp(rate * (ts - at) / DAY)
        p *= 1 + 0.002 * math.sin(2 * math.pi * ts / (5 * HOUR))
        if ts >= at:
            p *= 1 - depth * min(1.0, (ts - at) / (minutes * 60))
        return p

    return price


def row(market: str, interval: int, ts: int, price: PriceFn) -> StoredRow:
    step = step_of(interval)
    o = round(price(market, ts), 2)
    c = round(price(market, ts + step), 2)
    h = round(max(o, c) * 1.0004 + 0.01, 2)
    lo = round(min(o, c) * 0.9996 - 0.01, 2)
    volume = 1.0 + 0.6 * (1 + math.sin(ts / 7777.0)) + 0.4 * (1 + math.sin(ts / 911.0))
    return (ts, f"{o:.2f}", f"{h:.2f}", f"{lo:.2f}", f"{c:.2f}", f"{volume:.5f}")


def build_archive(
    path: Path,
    start: int,
    end: int,
    *,
    price: PriceFn = wavy,
    lead: int = LEAD_ROWS,
    lead_of: Mapping[tuple[str, int], int] | None = None,
    skip: Mapping[tuple[str, int], Iterable[int]] | None = None,
) -> Path:
    """Rows of both markets and all intervals from ``lead`` bars before ``start`` (aligned) to
    ``end``; ``lead_of`` overrides the lead of one series, ``skip`` removes open times."""
    with Archive(path) as arch:
        for market in MARKETS:
            for interval in INTERVALS:
                step = step_of(interval)
                n = (lead_of or {}).get((market, interval), lead)
                first = start // step * step - n * step
                gone = set((skip or {}).get((market, interval), ()))
                rows = [
                    row(market, interval, ts, price)
                    for ts in range(first, end, step)
                    if ts not in gone
                ]
                for w_start, w_end in windows(interval, first, end):
                    part = [r for r in rows if w_start <= r[0] < w_end]
                    arch.store_window(market, interval, w_start, part, fetched_at=FAR)
    return path
