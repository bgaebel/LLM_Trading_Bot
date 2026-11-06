"""Spec 005 AK1 / ADR-0011: prompt v1 is fixed and versioned; the message has the Arena context."""

import json
from typing import Any

import pytest

from golden import golden, scenarios, snapshot_for
from golden_scenarios import llm_json
from llm_trading_bot.llm.contract import (
    FIELDS,
    PROMPT_VERSION,
    SYSTEM,
    prompt_sha256,
    response_schema,
    user_message,
)
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import feature_payload

# Changing SYSTEM or the schema changes this hash. That is a new prompt version (CLAUDE.md):
# bump PROMPT_VERSION, update the hash here, and record why in the spec - never edit silently.
PINNED = {"llm-v1": "273e006301bbea60bfd29bec8229b7d8e8268500e80bc5b34c6794d1e9315818"}


def test_prompt_is_pinned_to_its_version() -> None:
    assert prompt_sha256() == PINNED[PROMPT_VERSION]


def test_prompt_keeps_the_arena_safety_rules() -> None:
    for rule in (
        "NOT a broker, order executor",
        "CLOSED candle",
        "do not claim to know news",
        "BUY must use 0.25 or 0.45, SELL must use 0.0",
        "avoiding overtrading is valuable",
        "a deterministic sandbox risk engine controls every virtual order",
    ):
        assert rule in SYSTEM


def _walk_objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    found = [schema] if schema.get("type") == "object" else []
    for child in schema.get("properties", {}).values():
        found += _walk_objects(child)
    return found


@pytest.mark.parametrize(
    "markets",
    [[Symbol.BTCEUR, Symbol.ETHEUR], [Symbol.ETHEUR, Symbol.BTCEUR], [Symbol.ETHEUR]],
    ids=["both", "both-reversed", "eth-only"],
)
def test_schema_lists_exactly_the_requested_markets_and_is_strict(markets: list[Symbol]) -> None:
    schema = response_schema(markets)
    names = [s.market for s in (Symbol.BTCEUR, Symbol.ETHEUR) if s in markets]
    assert list(schema["properties"]) == names and schema["required"] == names
    for obj in _walk_objects(schema):
        # Strict structured outputs: every property required, nothing else allowed.
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    entry = schema["properties"][names[0]]
    assert tuple(entry["required"]) == FIELDS
    assert json.dumps(entry["properties"]["target_fraction"]) == (
        '{"type": "number", "enum": [0, 0.25, 0.45]}'
    )


def test_schema_needs_a_market() -> None:
    with pytest.raises(ValueError):
        response_schema([])


def _payload(case: dict[str, Any]) -> dict[str, Any]:
    snap = snapshot_for(case["params"])
    meta = golden()["meta"]
    ind = indicators(snap, case["roundtrip_cost_frac"])
    return feature_payload(snap, ind, 0.25, meta["fee_rate"], meta["slippage"])


def test_message_wraps_the_arena_context_per_market() -> None:
    btc, eth = _payload(scenarios()[0]), _payload(scenarios()[48])
    text = user_message({Symbol.ETHEUR: eth, Symbol.BTCEUR: btc})
    # Market order is fixed (BTC first), and each market's part is the Arena llm JSON verbatim.
    assert text == '{"markets":{"BTC-EUR":' + llm_json(btc) + ',"ETH-EUR":' + llm_json(eth) + "}}"
    assert json.loads(text)["markets"]["BTC-EUR"] == json.loads(llm_json(btc))


def test_message_with_one_market() -> None:
    eth = _payload(scenarios()[48])
    assert list(json.loads(user_message({Symbol.ETHEUR: eth}))["markets"]) == ["ETH-EUR"]
