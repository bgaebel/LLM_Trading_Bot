"""Spec 016: shared lab data - quarters, seal, JSON round trip."""

import calendar

import pytest

from llm_trading_bot.lab.model import (
    SEAL_EPOCH,
    DayPoint,
    DepotResult,
    MarketQuality,
    Quarter,
    QuarterResult,
    Trade,
    Variant,
)


def test_seal_is_2025_10_01_utc() -> None:
    assert calendar.timegm((2025, 10, 1, 0, 0, 0)) == SEAL_EPOCH


def test_quarter_bounds_and_seal() -> None:
    q = Quarter.parse("2021Q2")
    assert (q.start, q.end) == (
        calendar.timegm((2021, 4, 1, 0, 0, 0)),
        calendar.timegm((2021, 7, 1, 0, 0, 0)),
    )
    assert Quarter.parse("2024Q4").end == calendar.timegm((2025, 1, 1, 0, 0, 0))
    assert not Quarter.parse("2025Q3").sealed and Quarter.parse("2025Q4").sealed
    labels = [x.label for x in Quarter.span("2021Q2", "2025Q3")]
    assert labels[0] == "2021Q2" and labels[-1] == "2025Q3" and len(labels) == 18


@pytest.mark.parametrize("bad", ["2021Q5", "21Q1", "2021-Q1", ""])
def test_quarter_label_is_checked(bad: str) -> None:
    with pytest.raises(ValueError):
        Quarter.parse(bad)


def test_result_survives_json() -> None:
    q = Quarter.parse("2022Q1")
    result = QuarterResult(
        q,
        Variant(),
        depots={
            "trend": DepotResult(
                "trend",
                [DayPoint("2022-01-01", 10_010.0, 5_000.0, {"BTC-EUR": 0.5})],
                [Trade(q.start + 65, "trend", "BTC-EUR", "BUY", 0.1, 40_000.0, 4_000.0, 10.0)],
                None,
                10_010.0,
                9_990.0,
                {"BUY/OK": 1},
            )
        },
        quality={"BTC-EUR": MarketQuality("BTC-EUR", 100, 95, {"stale": 3}, 2)},
        daily_mid={"BTC-EUR": [("2022-01-01", 40_100.0)]},
        spread={"BTC-EUR": 0.0002},
        rounds=1440,
        seconds=3.5,
        dataset_sha256="ab" * 32,
    )
    again = QuarterResult.from_json(result.to_json())
    assert again == result
    assert again.eligible == {"BTC-EUR": True} and again.graded
