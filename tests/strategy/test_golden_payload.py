"""ADR-0007 / Spec 003 AK4: LLM context and cost model identical to Arena v0.3.1."""

import json
from typing import Any

import pytest

from golden import assert_same, golden, scenarios, snapshot_for
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["params"]["name"])
def test_cost_roundtrip_matches_arena(case: dict[str, Any]) -> None:
    snap = snapshot_for(case["params"])
    meta = golden()["meta"]
    actual = cost_roundtrip(snap.quote.spread_bps, meta["fee_rate"], meta["slippage"])
    assert_same(actual, case["roundtrip_cost_frac"])


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["params"]["name"])
def test_feature_payload_matches_arena(case: dict[str, Any]) -> None:
    snap = snapshot_for(case["params"])
    meta = golden()["meta"]
    ind = indicators(snap, case["roundtrip_cost_frac"])
    for weight, expected in case["payload"].items():
        actual = feature_payload(snap, ind, float(weight), meta["fee_rate"], meta["slippage"])
        # Compare in the form the model receives it: serialised JSON.
        assert_same(json.loads(json.dumps(actual)), expected, f"{case['params']['name']}@{weight}")


def test_cost_roundtrip_formula() -> None:
    assert cost_roundtrip(10.0, 0.0025, 0.0005) == pytest.approx(0.007)
    assert cost_roundtrip(0.0, 0.0, 0.0) == 0.0
