"""Spec 011 AK1 / ADR-0021: the drawdown stop, bit for bit like the Arena's mark_and_risk."""

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

import golden_drawdown_cases as dd
from golden import assert_same
from golden_execution_runner import BotRunner
from golden_scenarios import boundary_digest
from llm_trading_bot.market.models import Quote, Symbol
from llm_trading_bot.paper.db import balance, open_db, write_tx
from llm_trading_bot.paper.execution import DRAWDOWN_KILL, Costs, RiskStop, mark, record_decision
from llm_trading_bot.strategy.signals import Signal

FIXTURE = Path(__file__).parent / "fixtures" / "execution_drawdown_golden_v0.3.1.json"


@cache
def golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def test_fixture_comes_from_the_expected_arena_commit_and_case_set() -> None:
    meta = golden()["meta"]
    assert meta["arena_commit"] == "7563724"
    assert meta["kill"] == dd.KILL == DRAWDOWN_KILL
    assert meta["fuzz_modes"] == list(dd.FUZZ_MODES)
    assert meta["fuzz_per_mode"] == dd.FUZZ_PER_MODE
    assert meta["fuzz_chunk"] == dd.FUZZ_CHUNK
    assert meta["walk_steps"] == dd.WALK_STEPS
    assert [(f["mode"], f["chunk"]) for f in golden()["fuzz"]] == [
        (m, c) for m, c, _ in dd.fuzz_chunks()
    ]
    assert len(golden()["walks"]) == dd.WALKS
    assert 0 < meta["walks_stopped"] < dd.WALKS
    for mode, counts in meta["coverage"].items():
        assert counts["stop"] > 0 and counts["no_stop"] > 0, mode


@pytest.mark.parametrize("expected", golden()["named"], ids=lambda s: s["name"])
def test_named_drawdown_cases_match_arena(expected: dict[str, Any], tmp_path: Path) -> None:
    rebuilt = {s["name"]: s for s in dd.named_scenarios()}[expected["name"]]
    assert json.loads(json.dumps(rebuilt["ops"])) == expected["ops"]
    actual = BotRunner(tmp_path).play(rebuilt["ops"])
    assert_same(json.loads(json.dumps(actual)), expected["results"], expected["name"])


@pytest.mark.parametrize("expected", golden()["fuzz"], ids=lambda f: f"{f['mode']}-{f['chunk']}")
def test_drawdown_edges_match_arena_exactly(expected: dict[str, Any], tmp_path: Path) -> None:
    chunk = next(
        c for m, i, c in dd.fuzz_chunks() if (m, i) == (expected["mode"], expected["chunk"])
    )
    runner = BotRunner(tmp_path)
    con, marks = runner.fresh(), dict[str, int]()
    try:
        results = [runner.play(ops, con, marks) for ops in chunk]
    finally:
        con.close()
    assert boundary_digest(results) == expected["digest"]


@pytest.mark.parametrize("expected", golden()["walks"], ids=lambda w: f"walk-{w['seed']}")
def test_wide_walks_match_arena_exactly(expected: dict[str, Any], tmp_path: Path) -> None:
    results = BotRunner(tmp_path).play(dd.walk(expected["seed"]))
    assert boundary_digest(results) == expected["digest"]


QS = {Symbol.BTCEUR: Quote(59_990.0, 60_010.0), Symbol.ETHEUR: Quote(2_999.0, 3_001.0)}


def _depot(tmp_path: Path, cash: float, btc: float, eth: float, peak: float) -> Any:
    con = open_db(tmp_path / "risk.db", agents=("trend",))
    with write_tx(con):
        con.execute(
            "UPDATE portfolios SET cash=?,btc=?,eth=?,peak=? WHERE agent='trend'",
            (cash, btc, eth, peak),
        )
    return con


def test_mark_reports_the_stop_with_equity_before_the_sale(tmp_path: Path) -> None:
    con = _depot(tmp_path, 2_000.0, 0.06, 0.9, 10_000.0)
    with write_tx(con):
        stop = mark(con, "trend", QS, "2026-11-07T08:00:00+00:00", Costs())
    equity = 2_000.0 + 0.06 * 60_000.0 + 0.9 * 3_000.0
    assert stop == RiskStop("trend", equity, 10_000.0, "2026-11-07T08:00:00+00:00")
    p = balance(con, "trend")
    assert (p["halted"], p["btc"], p["eth"]) == (1, 0.0, 0.0)
    with write_tx(con):
        assert mark(con, "trend", QS, "2026-11-07T08:01:00+00:00", Costs()) is None


def test_mark_without_a_stop_returns_none(tmp_path: Path) -> None:
    con = _depot(tmp_path, 9_000.0, 0.0, 0.0, 10_000.0)
    with write_tx(con):
        assert mark(con, "trend", QS, "2026-11-07T08:00:00+00:00", Costs()) is None
    assert balance(con, "trend")["halted"] == 0


def test_a_stop_triggered_by_a_trade_is_handed_to_the_caller(tmp_path: Path) -> None:
    con = _depot(tmp_path, 9_050.0, 0.0, 0.0, 10_000.0)
    stops: list[RiskStop] = []
    sig = Signal("BUY", 0.45, 0.7, "test", "trend")
    assert record_decision(
        con,
        "trend",
        Symbol.ETHEUR,
        3600,
        "TREND",
        sig,
        {},
        costs=Costs(0.05, 0.05),
        now="2026-11-07T08:00:00+00:00",
        qs=QS,
        stops=stops,
    )
    assert [s.agent for s in stops] == ["trend"]
    sides = [r[0] for r in con.execute("SELECT side FROM trades ORDER BY id")]
    assert sides == ["BUY", "RISK_SELL"]
