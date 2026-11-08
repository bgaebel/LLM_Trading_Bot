"""Spec 021 AK1-AK4 (ADR-0031): the euro buy & hold depot runs only with ``BOT_BUYHOLD=1``. Off,
a round never touches it, every other depot writes the same rows as with it, its history stays
as it is, and switching it back on (or a rollback to a release before 021) values it again."""

import ast
import inspect
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeClock, ScenarioClient, UsdcScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.llm.agents import (
    BUYHOLD_FIELD,
    USDC_AGENTS,
    USDC_FIELD,
    BotConfig,
    load_config,
)
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db, write_tx
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.paper.usdc import BUYHOLD_USDC
from llm_trading_bot.web.credentials import FIELDS, parse

CLASSIC = ("trend", "breakout", "meanrev", "ensemble")
TABLES = ("portfolios", "decisions", "trades", "equity")
STEPS = (0, 7, 31, 45, 61, 120)
"""Seconds after NOW: several rounds while the fake market's candles are fresh."""


# ------------------------------------------------------------------ AK1: the switch


def test_buyhold_switch_is_off_by_default_and_strict() -> None:
    base = {"BOT_AGENTS": "trend", USDC_FIELD: ""}
    assert BUYHOLD_FIELD == "BOT_BUYHOLD"
    assert load_config(base).buyhold is False
    assert load_config({**base, BUYHOLD_FIELD: "0"}).buyhold is False
    assert load_config({**base, BUYHOLD_FIELD: "1"}).buyhold is True
    gpt = {"OPENAI_API_KEY": KEY, USDC_FIELD: ""}
    assert load_config(gpt).buyhold is False
    assert load_config({**gpt, BUYHOLD_FIELD: "1"}).buyhold is True
    # Fail closed: a value that is not exactly 1 or 0 is refused, never read as on or off.
    for wrong in ("", " 1", "1 ", "true", "on", "yes", "2", "01"):
        with pytest.raises(ValueError, match="BOT_BUYHOLD must be 1 or 0"):
            load_config({**base, BUYHOLD_FIELD: wrong})
        with pytest.raises(ValueError, match="BOT_BUYHOLD must be 1 or 0"):
            load_config({**gpt, BUYHOLD_FIELD: wrong})
    # Built by hand (tests, the strategy lab of Spec 016), it is off unless asked for.
    assert BotConfig(("trend",), None).buyhold is False
    # The web UI keeps only its own fields in secrets.env: the switch belongs in the environment.
    assert BUYHOLD_FIELD not in FIELDS and parse("BOT_BUYHOLD=1\n") == {}


def test_strategy_lab_asks_for_buyhold_where_it_expects_it() -> None:
    """Checker defect 2 (Spec 021, Risiken): the lab of Spec 016 opens a buy & hold depot but
    builds its config by hand, where buy & hold is now off; merged without asking for it, its
    buy & hold would neither buy nor be valued. A tripwire for that merge: skipped until the lab
    is on the same branch, then every ``BotConfig`` the backtest builds must name ``buyhold``."""
    model = pytest.importorskip("llm_trading_bot.lab.model")
    backtest = pytest.importorskip("llm_trading_bot.lab.backtest")
    if BUYHOLD not in model.DEPOTS:
        return
    tree = ast.parse(inspect.getsource(backtest))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "BotConfig"
    ]
    assert calls, "the lab builds no BotConfig - check how it runs buy & hold"
    for call in calls:
        assert any(k.arg == "buyhold" for k in call.keywords), (
            f"lab/backtest.py:{call.lineno}: BotConfig without buyhold= runs no buy & hold"
        )


def test_benchmarks_follow_both_switches() -> None:
    assert BotConfig(("trend",), None).depots == ("trend",)
    assert BotConfig(("trend",), None, buyhold=True).depots == ("trend", BUYHOLD)
    usdc = BotConfig(("trend",), None, ("trend-usdc",))
    assert usdc.depots == ("trend", "trend-usdc", BUYHOLD_USDC)
    both = BotConfig(("trend",), None, ("trend-usdc",), True)
    assert both.depots == ("trend", "trend-usdc", BUYHOLD, BUYHOLD_USDC)
    # The server's case: USDC depots on by default (Spec 020), buy & hold off by default.
    assert load_config({"BOT_AGENTS": "trend"}).benchmarks == (BUYHOLD_USDC,)


# ------------------------------------------------------------------ AK2: off means untouched


def rows(con: sqlite3.Connection, agents: tuple[str, ...], *, ids: bool) -> dict[str, list[Any]]:
    """Every row of ``agents``; without ids for depots run beside others, which shift them."""
    marks = ",".join("?" for _ in agents)
    out: dict[str, list[Any]] = {}
    for table, cols in (
        ("portfolios", "agent,cash,btc,eth,peak,halted"),
        ("decisions", "ts,agent,symbol,bar_ts,model,action,target,confidence,reason,inputs,status"),
        ("trades", "ts,agent,symbol,side,qty,fill,notional,fee,slip_rate"),
        ("equity", "ts,agent,value,cash,btc,eth"),
    ):
        if ids and table != "portfolios":
            cols = "id," + cols + (",decision_id" if table == "trades" else "")
        order = "agent" if table == "portfolios" else "agent, ts, id"
        query = f"SELECT {cols} FROM {table} WHERE agent IN ({marks}) ORDER BY {order}"
        out[table] = [[repr(v) for v in r] for r in con.execute(query, agents)]
    return out


def count(con: sqlite3.Connection, table: str, agent: str) -> int:
    n: int = con.execute(f"SELECT COUNT(*) FROM {table} WHERE agent=?", (agent,)).fetchone()[0]
    return n


def test_fresh_database_never_gets_a_buyhold_depot(tmp_path: Path) -> None:
    config = load_config({"BOT_AGENTS": "trend"})  # as on the server: USDC on, buy & hold off
    con = open_db(tmp_path / "bot.db", agents=config.depots)
    try:
        clock = FakeClock(NOW)
        reports: list[CycleReport] = []
        for step in STEPS:
            clock.now = NOW + step
            reports.append(
                paper_cycle(con, UsdcScenarioClient(), config, costs=Costs(), clock=clock)
            )
        assert all(r.complete and not r.buyhold and r.buyhold_bought == 0 for r in reports)
        assert all(BUYHOLD not in {e.agent for e in r.equity} for r in reports)
        assert reports[0].buyhold_usdc_bought == 2  # the reference still buys once
        assert {e.agent for e in reports[-1].equity} == {"trend", *USDC_AGENTS, BUYHOLD_USDC}
        for table in TABLES:
            assert count(con, table, BUYHOLD) == 0, table
    finally:
        con.close()


def with_buyhold_history(path: Path) -> None:
    """A database written by a release that ran buy & hold (before Spec 021)."""
    on = BotConfig(CLASSIC, None, buyhold=True)
    con = open_db(path, agents=on.depots)
    try:
        clock = FakeClock(NOW)
        for step in (0, 61):
            clock.now = NOW + step
            assert paper_cycle(con, ScenarioClient(), on, costs=Costs(), clock=clock).complete
        assert count(con, "trades", BUYHOLD) == 2 and count(con, "equity", BUYHOLD) == 2
        # A peak below its value: any valuation of the depot would raise it (mark), so an
        # unchanged row also proves that no drawdown check ran.
        with write_tx(con):
            con.execute("UPDATE portfolios SET peak=1.0 WHERE agent=?", (BUYHOLD,))
    finally:
        con.close()


def test_old_database_with_buyhold_opens_with_buyhold_off(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    with_buyhold_history(path)
    off = BotConfig(CLASSIC, None)
    con = open_db(path, agents=off.depots)  # the schema check passes unchanged
    try:
        assert {r[0] for r in con.execute("SELECT agent FROM portfolios")} >= {BUYHOLD}
    finally:
        con.close()


def test_existing_buyhold_rows_stay_untouched_while_off(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    with_buyhold_history(path)
    off = BotConfig(CLASSIC, None)
    con = open_db(path, agents=off.depots)
    try:
        before = rows(con, (BUYHOLD,), ids=True)
        clock = FakeClock(NOW)
        for step in (90, 100, 110, 120):
            clock.now = NOW + step
            report = paper_cycle(con, ScenarioClient(), off, costs=Costs(), clock=clock)
            assert report.complete and report.buyhold_bought == 0
            assert [e.agent for e in report.equity] == list(CLASSIC)
            assert BUYHOLD not in {m.agent for m in report.markets}
        assert rows(con, (BUYHOLD,), ids=True) == before
        assert count(con, "equity", "trend") == 6  # the others went on
    finally:
        con.close()


# ------------------------------------------------------------------ AK3: the others identical


def gpt(buyhold: bool) -> BotConfig:
    return load_config(
        {"OPENAI_API_KEY": KEY, USDC_FIELD: "", BUYHOLD_FIELD: "1" if buyhold else "0"}
    )


def classic(buyhold: bool) -> BotConfig:
    return BotConfig(CLASSIC, None, buyhold=buyhold)


def usdc(buyhold: bool) -> BotConfig:
    return BotConfig(CLASSIC, None, USDC_AGENTS, buyhold)


CASES: dict[str, tuple[Callable[[bool], BotConfig], type[ScenarioClient]]] = {
    "classic": (classic, ScenarioClient),
    "gpt": (gpt, ScenarioClient),
    "usdc": (usdc, UsdcScenarioClient),
}


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize(
    ("btc", "eth"),
    [("up-calm-plain-s1", "up-calm-plain-s1"), ("down-calm-plain-s1", "up-wild-spike-s1")],
)
def test_other_depots_are_identical_with_buyhold_on_or_off(
    tmp_path: Path, case: str, btc: str, eth: str
) -> None:
    make, client_type = CASES[case]
    seen = {}
    for on in (True, False):
        config = make(on)
        con = open_db(tmp_path / f"{case}-{on}.db", agents=config.depots)
        try:
            clock = FakeClock(NOW)
            client = client_type(btc, eth)
            for step in STEPS:
                clock.now = NOW + step
                paper_cycle(con, client, config, costs=Costs(), model=FakeModel(), clock=clock)
            others = tuple(a for a in config.depots if a != BUYHOLD)
            seen[on] = rows(con, others, ids=False)
            assert (count(con, "trades", BUYHOLD) > 0) is on
        finally:
            con.close()
    assert seen[True] == seen[False]
    assert seen[False]["trades"] and seen[False]["equity"]


# ------------------------------------------------------------------ AK4: back on


def test_switching_back_on_continues_valuing_without_buying_again(tmp_path: Path) -> None:
    """``BOT_BUYHOLD=1`` - and a rollback to a release before Spec 021, whose depots always
    include buy & hold and which runs the same code - values the dormant depot again."""
    path = tmp_path / "bot.db"
    with_buyhold_history(path)
    off, on = BotConfig(CLASSIC, None), BotConfig(CLASSIC, None, buyhold=True)
    con = open_db(path, agents=off.depots)
    try:
        clock = FakeClock(NOW)
        for step in (90, 100):
            clock.now = NOW + step
            paper_cycle(con, ScenarioClient(), off, costs=Costs(), clock=clock)
        assert count(con, "equity", BUYHOLD) == 2
        con.close()
        con = open_db(path, agents=on.depots)
        for step in (110, 120):
            clock.now = NOW + step
            report = paper_cycle(con, ScenarioClient(), on, costs=Costs(), clock=clock)
            assert report.complete and report.buyhold and report.buyhold_bought == 0
            assert BUYHOLD in {e.agent for e in report.equity}
        assert count(con, "trades", BUYHOLD) == 2  # no second purchase
        assert count(con, "equity", BUYHOLD) == 4
        assert count(con, "decisions", BUYHOLD) == 2
        # Valued again: the peak follows the value (it was set below it above).
        peak = con.execute("SELECT peak FROM portfolios WHERE agent=?", (BUYHOLD,)).fetchone()[0]
        assert peak > 1.0
    finally:
        con.close()
