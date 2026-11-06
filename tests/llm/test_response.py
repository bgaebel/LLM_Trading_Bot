"""Spec 005 AK2: each market's answer is checked by the Arena rules plus the regime; any invalid
part rejects the whole answer, so nothing is ever traded on a guess."""

import json
from typing import Any

import pytest

from llm_trading_bot.llm.contract import PROMPT_VERSION, InvalidSignal, parse_answer
from llm_trading_bot.market.models import Symbol

BOTH = [Symbol.BTCEUR, Symbol.ETHEUR]
MISSING = object()


def entry(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action": "BUY",
        "target_fraction": 0.25,
        "confidence": 0.6,
        "market_regime": "UPTREND",
        "reason": "Trend und Momentum stimmen überein",
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not MISSING}


def answer(btc: Any = None, eth: Any = None, **extra: Any) -> str:
    data: dict[str, Any] = {
        "BTC-EUR": entry() if btc is None else btc,
        "ETH-EUR": entry(action="HOLD", target_fraction=0, market_regime="SIDEWAYS")
        if eth is None
        else eth,
    }
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


def test_valid_answer_gives_one_signal_and_regime_per_market() -> None:
    result = parse_answer(answer(), BOTH, strategy="gpt-sol")
    btc, eth = result[Symbol.BTCEUR], result[Symbol.ETHEUR]
    assert (btc.signal.action, btc.signal.target_fraction, btc.regime) == ("BUY", 0.25, "UPTREND")
    assert (eth.signal.action, eth.signal.target_fraction, eth.regime) == ("HOLD", 0.0, "SIDEWAYS")
    assert btc.signal.strategy == "gpt-sol" and btc.signal.version == PROMPT_VERSION
    assert isinstance(eth.signal.target_fraction, float) and isinstance(
        btc.signal.confidence, float
    )
    for part in result.values():
        part.signal.validate()


@pytest.mark.parametrize(
    ("action", "target", "ok"),
    [
        ("BUY", 0.25, True),
        ("BUY", 0.45, True),
        ("BUY", 0, False),
        ("SELL", 0, True),
        ("SELL", 0.0, True),
        ("SELL", 0.25, False),
        ("SELL", 0.45, False),
        ("HOLD", 0, True),
        ("HOLD", 0.25, True),
        ("HOLD", 0.45, True),
    ],
)
def test_action_and_target_must_be_consistent(action: str, target: float, ok: bool) -> None:
    text = answer(btc=entry(action=action, target_fraction=target))
    if ok:
        assert parse_answer(text, BOTH, strategy="gpt-sol")[Symbol.BTCEUR].signal.action == action
    else:
        with pytest.raises(InvalidSignal, match="inconsistent"):
            parse_answer(text, BOTH, strategy="gpt-sol")


@pytest.mark.parametrize(
    "bad",
    [
        {"action": "buy"},
        {"action": "SHORT"},
        {"action": None},
        {"target_fraction": 0.3},
        {"target_fraction": 0.250001},
        {"target_fraction": 1},
        {"target_fraction": "0.25"},
        {"target_fraction": True},
        {"confidence": -0.0001},
        {"confidence": 1.0001},
        {"confidence": True},
        {"confidence": "0.5"},
        {"market_regime": "uptrend"},
        {"market_regime": "VOLATILE"},
        {"market_regime": None},
        {"reason": ""},
        {"reason": "   "},
        {"reason": 5},
        {"reason": MISSING},
        {"market_regime": MISSING},
        {"extra": "feld"},
    ],
    ids=lambda d: ",".join(f"{k}={v!r}" for k, v in d.items())[:40],
)
def test_one_invalid_field_rejects_the_whole_answer(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidSignal):
        parse_answer(answer(eth=entry(**bad)), BOTH, strategy="gpt-sol")


@pytest.mark.parametrize("confidence", [0, 1, 0.0, 1.0])
def test_confidence_bounds_are_inclusive(confidence: float) -> None:
    result = parse_answer(answer(btc=entry(confidence=confidence)), BOTH, strategy="gpt-sol")
    assert result[Symbol.BTCEUR].signal.confidence == float(confidence)


def test_reason_is_trimmed_and_capped_at_550() -> None:
    long = parse_answer(answer(btc=entry(reason="  " + "ä" * 600)), BOTH, strategy="gpt-sol")
    assert long[Symbol.BTCEUR].signal.reason == "ä" * 550
    exact = parse_answer(answer(btc=entry(reason="x" * 549)), BOTH, strategy="gpt-sol")
    assert exact[Symbol.BTCEUR].signal.reason == "x" * 549


@pytest.mark.parametrize(
    "text",
    [
        "",
        "kein JSON",
        '["BUY"]',
        '"BUY"',
        '{"BTC-EUR": {}}',
        answer(**{"XRP-EUR": entry()}),
        answer() + " trailing",
        '{"BTC-EUR": ' + json.dumps(entry()) + ', "ETH-EUR": "HOLD"}',
        '{"BTC-EUR": ' + json.dumps(entry()) + ', "ETH-EUR": {"confidence": NaN}}',
    ],
    ids=[
        "empty",
        "text",
        "list",
        "string",
        "empty-entry",
        "extra-market",
        "trailing",
        "str-entry",
        "nan",
    ],
)
def test_structure_errors_reject_the_answer(text: str) -> None:
    with pytest.raises(InvalidSignal):
        parse_answer(text, BOTH, strategy="gpt-sol")


def test_nan_and_infinity_confidence_are_rejected() -> None:
    for token in ("NaN", "Infinity", "-Infinity"):
        text = answer().replace('"confidence": 0.6', f'"confidence": {token}', 1)
        with pytest.raises(InvalidSignal, match="confidence"):
            parse_answer(text, BOTH, strategy="gpt-sol")


def test_answer_must_cover_exactly_the_requested_markets() -> None:
    only_eth = json.dumps({"ETH-EUR": entry()})
    assert list(parse_answer(only_eth, [Symbol.ETHEUR], strategy="gpt-sol")) == [Symbol.ETHEUR]
    with pytest.raises(InvalidSignal, match="exactly"):
        parse_answer(only_eth, BOTH, strategy="gpt-sol")
    with pytest.raises(InvalidSignal, match="exactly"):
        parse_answer(answer(), [Symbol.ETHEUR], strategy="gpt-sol")
