"""Load the strategy golden fixtures and rebuild their snapshots with the bot's own types."""

import json
import math
from functools import cache
from pathlib import Path
from typing import Any

from golden_scenarios import NOW, Params, build_bars, quote_for
from llm_trading_bot.market.models import Candle, Quote, Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot

FIXTURE = Path(__file__).parent / "strategy" / "fixtures" / "strategy_golden_v0.3.1.json"
REL_TOL = 1e-12  # ADR-0007


@cache
def golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def scenarios() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = golden()["scenarios"]
    return result


def snapshot_for(params: Params) -> MarketSnapshot:
    rows = build_bars(params)
    bars = {tf: tuple(Candle(*r) for r in rows[tf]) for tf in rows}
    bid, ask = quote_for(params, rows)
    symbol = Symbol[params.get("symbol", "BTCEUR")]
    return MarketSnapshot(symbol, bars, Quote(bid, ask), NOW, NOW, {tf: 0 for tf in rows})


def assert_same(actual: Any, expected: Any, path: str = "") -> None:
    """Deep equality; floats within REL_TOL, everything else exact."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert set(actual) == set(expected), (path, set(actual) ^ set(expected))
        for key in expected:
            assert_same(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list | tuple):
        assert isinstance(actual, list | tuple), path
        assert len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_same(a, e, f"{path}[{i}]")
    elif isinstance(expected, float) or isinstance(actual, float):
        assert isinstance(actual, int | float) and not isinstance(actual, bool), path
        # int vs float matters: json renders 100 and 100.0 differently in the LLM prompt.
        assert isinstance(actual, float) == isinstance(expected, float), (path, actual, expected)
        assert math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=0.0) or actual == expected, (
            path,
            actual,
            expected,
        )
    else:
        assert actual == expected, (path, actual, expected)
