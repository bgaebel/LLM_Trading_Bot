"""Spec 005 AK2: the model's answer is checked exactly like the Arena (strict_response +
validate_signal); anything else is rejected and can never be traded."""

from typing import Any

import pytest

from golden_llm import llm_golden
from llm_trading_bot.llm.contract import InvalidSignal, parse_signal


def _id(case: dict[str, Any]) -> str:
    return case["text"][:60] or "<empty>"


@pytest.mark.parametrize("case", llm_golden()["answers"], ids=_id)
def test_answer_judged_like_the_arena(case: dict[str, Any]) -> None:
    if not case["accepted"]:
        with pytest.raises(InvalidSignal):
            parse_signal(case["text"])
        return
    sig = parse_signal(case["text"])
    expected = case["signal"]
    assert (sig.action, sig.target_fraction, sig.confidence, sig.reason) == (
        expected["action"],
        expected["target_fraction"],
        expected["confidence"],
        expected["reason"],
    )
    assert isinstance(sig.target_fraction, float) and isinstance(sig.confidence, float)
    assert sig.strategy == "gpt"
    sig.validate()


def test_fixture_covers_both_outcomes_and_the_consistency_rule() -> None:
    answers = llm_golden()["answers"]
    texts = {a["text"]: a["accepted"] for a in answers}
    assert any(texts.values()) and not all(texts.values())
    buy_zero = (
        '{"action": "BUY", "target_fraction": 0, "confidence": 0.6, "reason": "Trend intakt"}'
    )
    sell_quarter = (
        '{"action": "SELL", "target_fraction": 0.25, "confidence": 0.6, "reason": "Trend intakt"}'
    )
    assert texts[buy_zero] is False and texts[sell_quarter] is False
