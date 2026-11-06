"""Spec 005 AK3: the model is asked on the Arena's cadence (interval, large move, daily cap)."""

import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

import golden_llm_cases as cases
from golden_llm import llm_golden
from llm_trading_bot.llm.due import Cadence, calls_today, llm_due


def _db(rows: list[dict[str, Any]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " agent TEXT NOT NULL, symbol TEXT NOT NULL, ref_mid REAL NOT NULL)"
    )
    for row in rows:
        ts = datetime.fromtimestamp(row["at"], UTC).isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO llm_calls(ts,agent,symbol,ref_mid) VALUES (?,?,?,?)",
            (ts, row["agent"], row["symbol"], row["ref_mid"]),
        )
    return con


def test_case_set_matches_the_fixture() -> None:
    assert len(cases.due_cases()) == len(llm_golden()["due"])


def test_every_cadence_case_matches_the_arena() -> None:
    expected = llm_golden()["due"]
    mismatches = []
    for i, (case, want) in enumerate(zip(cases.due_cases(), expected, strict=True)):
        con = _db(case["rows"])
        got = llm_due(con, "gpt", "BTCEUR", case["mid"], case["now"], Cadence.from_env(case["env"]))
        con.close()
        if got is not want:
            mismatches.append((i, case["env"], case["rows"][1:2], case["mid"], got, want))
    assert not mismatches, mismatches[:5]


def test_defaults_and_lower_bounds() -> None:
    assert Cadence.from_env({}) == Cadence(1800, 600, 0.018, 60)
    low = {
        "LLM_MIN_INTERVAL_SECONDS": "1",
        "LLM_EVENT_COOLDOWN_SECONDS": "1",
        "LLM_EVENT_MOVE_THRESHOLD": "0",
        "MAX_DAILY_LLM_CALLS": "-5",
    }
    assert Cadence.from_env(low) == Cadence(600, 300, 0.005, 0)


def test_non_integer_interval_is_refused() -> None:
    with pytest.raises(ValueError):
        Cadence.from_env({"LLM_MIN_INTERVAL_SECONDS": "1800.5"})


def test_calls_today_counts_every_attempt_of_the_agent_since_utc_midnight() -> None:
    midnight = 1_793_059_200  # 2026-10-27 00:00:00 UTC
    con = _db(
        [
            {"agent": "gpt", "symbol": "BTCEUR", "ref_mid": 1.0, "at": midnight - 1},
            {"agent": "gpt", "symbol": "BTCEUR", "ref_mid": 1.0, "at": midnight},
            {"agent": "gpt", "symbol": "ETHEUR", "ref_mid": 1.0, "at": midnight + 3600},
            {"agent": "trend", "symbol": "BTCEUR", "ref_mid": 1.0, "at": midnight + 60},
        ]
    )
    assert datetime.fromtimestamp(midnight, UTC).strftime("%H:%M:%S") == "00:00:00"
    assert calls_today(con, "gpt", midnight + 7200) == 2
    con.close()
