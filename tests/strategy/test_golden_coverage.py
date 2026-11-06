"""Spec 003 AK3: the fixtures reach every branch, so the golden tests prove the whole contract."""

from golden import golden, scenarios
from llm_trading_bot.market.snapshot import DEFAULT_MIN_BARS

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


def test_adx_scenarios_around_regime_thresholds() -> None:
    adx = [c["indicators"]["adx_1h"] for c in scenarios()]
    for lo, hi in ((17.0, 18.0), (18.0, 18.6), (21.4, 22.0), (22.0, 22.6)):
        assert any(lo <= a < hi for a in adx), (lo, hi)


def test_payload_edge_inputs_are_covered() -> None:
    payloads = [p for c in scenarios() for p in c["payload"].values()]
    assert any(p["filled_no_trade_1m_intervals_last_60"] > 0 for p in payloads)
    assert any(p["market"] == "ETHEUR" for p in payloads)
    assert any(p["current_weight"] == 0.1235 for p in payloads)


def test_snapshot_min_bars_equal_the_arena_strategy_minimum() -> None:
    expected = {int(k): v for k, v in golden()["meta"]["arena_min_bars"].items()}
    assert dict(DEFAULT_MIN_BARS) == expected


def test_boundary_cases_cover_every_base() -> None:
    assert all(len(seeds) == 800 for seeds in golden()["boundary"].values())
