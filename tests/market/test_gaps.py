from llm_trading_bot.market.bitvavo import fill_tiny_gaps
from llm_trading_bot.market.models import Candle


def bar(ts: int, close: float = 100.0) -> Candle:
    return Candle(ts, close, close + 1, close - 1, close, 10.0)


def test_single_missing_minute_filled_with_previous_close_and_zero_volume() -> None:
    bars, filled = fill_tiny_gaps((bar(0, 100), bar(120, 103)), 1)
    assert filled == 1
    assert [b.ts for b in bars] == [0, 60, 120]
    assert bars[1] == Candle(60, 100, 100, 100, 100, 0.0)


def test_two_missing_minutes_filled() -> None:
    bars, filled = fill_tiny_gaps((bar(0), bar(180)), 1)
    assert filled == 2
    assert [b.ts for b in bars] == [0, 60, 120, 180]


def test_three_missing_minutes_stay_unfilled() -> None:
    rows = (bar(60), bar(300))
    assert fill_tiny_gaps(rows, 1) == (rows, 0)


def test_other_intervals_use_max_missing_zero() -> None:
    rows = (bar(0), bar(1800))  # one missing 15m bar
    assert fill_tiny_gaps(rows, 15, max_missing=0) == (rows, 0)


def test_misaligned_delta_not_filled() -> None:
    rows = (bar(0), bar(150))
    assert fill_tiny_gaps(rows, 1) == (rows, 0)


def test_nothing_added_after_last_bar_or_on_empty_input() -> None:
    assert fill_tiny_gaps((), 1) == ((), 0)
    rows = (bar(0), bar(60))
    bars, filled = fill_tiny_gaps(rows, 1)
    assert filled == 0
    assert bars[-1] == rows[-1]
