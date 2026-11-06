"""ADR-0007 / Spec 003 AK2: every strategy threshold probed right at its edge, against the Arena.

The fixture stores only a digest per (base, seed); the indicator dict is rebuilt by the shared
sampler. Any change of a threshold, weight, comparison or reason text changes the digest.
"""

from functools import cache
from typing import Any

import pytest

from golden import golden, scenarios, snapshot_for
from golden_scenarios import BOUNDARY_WEIGHTS, boundary_digest, sample_indicators
from llm_trading_bot.market.snapshot import MarketSnapshot
from llm_trading_bot.strategy.indicators import closes, ema, indicators
from llm_trading_bot.strategy.signals import run_strategy

STRATEGIES = ("trend", "breakout", "meanrev", "ensemble")


@cache
def base(name: str) -> tuple[MarketSnapshot, dict[str, Any], dict[str, float]]:
    params = next(c["params"] for c in scenarios() if c["params"]["name"] == name)
    snap = snapshot_for(params)
    case = next(c for c in scenarios() if c["params"]["name"] == name)
    ind = dict(indicators(snap, case["roundtrip_cost_frac"]))
    c15 = closes(snap.bars[15])
    anchors = {
        "ema34_15m": ema(c15, 34),
        "ema30_15m": ema(c15, 30),
        "prev_close_15m": snap.bars[15][-2].close,
    }
    return snap, ind, anchors


def test_boundary_weights_match_fixture() -> None:
    assert golden()["meta"]["boundary_weights"] == list(BOUNDARY_WEIGHTS)


@pytest.mark.parametrize("name", sorted(golden()["boundary"]))
def test_boundary_cases_match_arena(name: str) -> None:
    snap, ind, anchors = base(name)
    mismatches = []
    for seed, expected in enumerate(golden()["boundary"][name]):
        i = sample_indicators(ind, anchors, seed)
        rows = []
        for w in BOUNDARY_WEIGHTS:
            for strategy in STRATEGIES:
                s = run_strategy(strategy, snap, i, w)  # type: ignore[arg-type]
                rows.append([w, strategy, s.action, s.target_fraction, s.confidence, s.reason])
        if boundary_digest(rows) != expected:
            mismatches.append(seed)
    assert not mismatches, f"{name}: {len(mismatches)} seeds differ, first {mismatches[:5]}"
