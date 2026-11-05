"""Spec 003 AK3: the fixtures reach every branch, so the golden tests prove the whole contract."""

from golden import golden, scenarios

STRATEGIES = ("trend", "breakout", "meanrev", "ensemble")


def reached() -> set[tuple[str, str]]:
    return {
        (name, sig["action"])
        for case in scenarios()
        for per_weight in case["signals"].values()
        for name, sig in per_weight.items()
    }


def test_every_strategy_reaches_buy_sell_and_hold() -> None:
    hit = reached()
    for name in STRATEGIES:
        for action in ("BUY", "SELL", "HOLD"):
            assert (name, action) in hit, (name, action)
    assert ("buyhold", "BUY") in hit


def test_ensemble_sees_every_regime() -> None:
    assert {c["indicators"]["regime"] for c in scenarios()} == {"TREND", "RANGE", "TRANSITION"}


def test_short_series_fallbacks_are_covered() -> None:
    cases = golden()["indicator_cases"]
    assert any(c["fn"] == "rsi" and c["expected"] == 50.0 for c in cases)
    assert any(c["fn"] == "rsi" and c["expected"] == 100 for c in cases)
    assert any(c["fn"] == "atr" and c["expected"] == 0.0 for c in cases)
    assert any(c["fn"] == "adx" and c["expected"] == [0.0, 0.0, 0.0] for c in cases)
    assert any(c["fn"] == "adx" and c["expected"] != [0.0, 0.0, 0.0] for c in cases)
