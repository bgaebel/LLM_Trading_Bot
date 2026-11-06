"""ADR-0007 / Spec 005 AK1: prompt, schema and user message identical to Arena v0.3.1."""

import json
from typing import Any

import pytest

from golden import assert_same, golden, scenarios, snapshot_for
from golden_llm import llm_golden
from golden_scenarios import llm_json
from llm_trading_bot.llm.contract import SIGNAL_SCHEMA, SYSTEM, user_message
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import feature_payload


def test_fixture_comes_from_the_expected_arena_commit() -> None:
    assert llm_golden()["meta"]["arena_commit"] == "7563724"


def test_system_prompt_is_identical() -> None:
    assert llm_golden()["system"] == SYSTEM


def test_signal_schema_is_identical_including_int_vs_float() -> None:
    assert_same(SIGNAL_SCHEMA, llm_golden()["signal_schema"])
    # The serialised schema is what the API receives.
    assert json.dumps(SIGNAL_SCHEMA) == json.dumps(llm_golden()["signal_schema"])


@pytest.mark.parametrize("case", scenarios()[:8], ids=lambda c: c["params"]["name"])
def test_user_message_is_the_arena_llm_json(case: dict[str, Any]) -> None:
    snap = snapshot_for(case["params"])
    meta = golden()["meta"]
    ind = indicators(snap, case["roundtrip_cost_frac"])
    payload = feature_payload(snap, ind, 0.25, meta["fee_rate"], meta["slippage"])
    assert user_message(payload) == llm_json(payload)
