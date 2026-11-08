"""Frozen real Bitvavo responses for the lab golden tests (Spec 016 AK3).

Captured by ``tests/lab/fixtures/capture.py``; the bodies are replayed verbatim through a fake
transport into the real ``BitvavoClient``, and the same rows feed the ``HistoricalClient``.
"""

import json
import lzma
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from llm_trading_bot.lab.history import CandleRow, MemorySource
from llm_trading_bot.market.bitvavo import API, TIMEFRAMES

FIXTURE = Path(__file__).parent / "lab" / "fixtures" / "bitvavo_captures.json.xz"
MARKETS = ("BTC-EUR", "ETH-EUR")
BOOK = "/ticker/book"


def candles_path(market: str, interval: int) -> str:
    return f"/{market}/candles?interval={TIMEFRAMES[interval]}&limit=500"


@dataclass(frozen=True)
class Capture:
    at: int
    """The round time the responses were fetched for (k*60 + 5)."""
    bodies: dict[str, str]
    """Request path -> response body exactly as Bitvavo sent it."""

    def json(self, path: str) -> Any:
        return json.loads(self.bodies[path])

    def rows(self, market: str, interval: int) -> list[CandleRow]:
        """The candle rows of one response, newest first as sent."""
        rows: list[CandleRow] = self.json(candles_path(market, interval))
        return rows

    def handler(self) -> Callable[[str], object]:
        """Answers the live client's GETs from this capture only."""

        def answer(url: str) -> object:
            assert url.startswith(API), url
            return self.json(url[len(API) :])

        return answer

    def source(self) -> MemorySource:
        """Exactly the rows of this capture."""
        return merged([self])


@cache
def captures() -> tuple[Capture, ...]:
    doc = json.loads(lzma.decompress(FIXTURE.read_bytes()).decode("utf-8"))
    return tuple(Capture(c["at"], c["responses"]) for c in doc["captures"])


def merged(items: list[Capture] | tuple[Capture, ...]) -> MemorySource:
    """All rows of ``items`` in capture order: a later capture's row wins (revisions)."""
    source = MemorySource()
    for capture in sorted(items, key=lambda c: c.at):
        for market in MARKETS:
            for interval in TIMEFRAMES:
                source.add(market, interval, capture.rows(market, interval))
    return source
