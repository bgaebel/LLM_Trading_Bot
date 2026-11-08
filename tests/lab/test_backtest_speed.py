"""Spec 016 AK11: how fast the unchanged live cycle runs over an SQLite archive.

Benchmark only (marker ``slow``, run with ``-m slow -s``): one simulated week of rounds against a
synthetic ``candles.db``. No hard limit yet - the targets for a quarter and the whole run are set
after the first measurement on real quarters (2021 and 2025) and noted in the spec's history.
"""

import time
from pathlib import Path

import pytest

from lab_backtest_data import DAY, T0, build_archive
from llm_trading_bot.lab.backtest import BASE, run_quarter
from llm_trading_bot.lab.model import Quarter

WEEK = 7 * DAY


@pytest.mark.slow
def test_one_simulated_week(tmp_path: Path) -> None:
    built = time.perf_counter()
    path = build_archive(tmp_path / "candles.db", T0, T0 + WEEK)
    build_seconds = time.perf_counter() - built
    result = run_quarter(path, Quarter("2023Q1", T0, T0 + WEEK), BASE)
    assert result.status == "ok", result.error
    assert result.rounds == WEEK // 60
    rate = result.rounds / result.seconds
    quarter_minutes = 91 * DAY / 60 / rate / 60
    print(
        f"\nbacktest benchmark: {result.rounds} rounds in {result.seconds:.1f} s = "
        f"{rate:.0f} rounds/s ({1000 / rate:.2f} ms/round); a 91-day quarter ~ "
        f"{quarter_minutes:.1f} min per process; archive built in {build_seconds:.1f} s"
    )
