"""Spec 022 AK9: the classic USDC depots and buy & hold USDC write exactly the rows of release
9946852 (before Spec 022), also with the GPT USDC depots running beside them."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from fakes import FakeClock, UsdcScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.llm.agents import USDC_AGENTS, BotConfig, load_config
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import Costs
from llm_trading_bot.paper.usdc import BUYHOLD_USDC

STEPS = (0, 7, 31, 45, 61, 120, 900, 1800, 3600)
DEPOTS = (*USDC_AGENTS, BUYHOLD_USDC)
TABLES = {
    "portfolios": "agent,cash,btc,eth,peak,halted",
    "decisions": "id,ts,agent,symbol,bar_ts,model,action,target,confidence,reason,inputs,status",
    "trades": "id,ts,agent,symbol,side,qty,fill,notional,fee,slip_rate,decision_id",
    "equity": "id,ts,agent,value,cash,btc,eth",
}
FROZEN = {
    # Computed on 9946852 with BotConfig((), None, USDC_AGENTS), these steps and scenarios.
    ("up-calm-plain-s1", "up-calm-plain-s1"): (
        "c67858b6488d48e349e6b3be190f92a4f958243532c6844b3e70e58fea102050"
    ),
    ("down-calm-plain-s1", "up-wild-spike-s1"): (
        "0e18c66a5624ff884ff6ca85939407a490d6888e1c68286195e265de65538e32"
    ),
}


def rows(con: sqlite3.Connection, *, ids: bool) -> dict[str, list[list[str]]]:
    marks = ",".join("?" for _ in DEPOTS)
    out = {}
    for table, cols in TABLES.items():
        if not ids:
            cols = ",".join(c for c in cols.split(",") if c not in ("id", "decision_id"))
        order = {"portfolios": "agent", "equity": "agent, ts"}.get(table, "agent, ts, symbol")
        query = f"SELECT {cols} FROM {table} WHERE agent IN ({marks}) ORDER BY {order}"
        out[table] = [[repr(v) for v in r] for r in con.execute(query, DEPOTS)]
    return out


def play(path: Path, config: BotConfig, btc: str, eth: str) -> sqlite3.Connection:
    con = open_db(path, agents=config.depots)
    clock = FakeClock(NOW)
    client = UsdcScenarioClient(btc, eth)
    for step in STEPS:
        clock.now = NOW + step
        paper_cycle(con, client, config, costs=Costs(), model=FakeModel(), clock=clock)
    return con


def digest(con: sqlite3.Connection) -> str:
    marks = ",".join("?" for _ in DEPOTS)
    out = {}
    for table, cols in TABLES.items():
        order = "agent" if table == "portfolios" else "id"
        query = f"SELECT {cols} FROM {table} WHERE agent IN ({marks}) ORDER BY {order}"
        out[table] = [[repr(v) for v in r] for r in con.execute(query, DEPOTS)]
    return hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize(("btc", "eth"), list(FROZEN))
def test_usdc_depots_match_release_9946852(tmp_path: Path, btc: str, eth: str) -> None:
    con = play(tmp_path / "bot.db", BotConfig((), None, USDC_AGENTS), btc, eth)
    try:
        assert digest(con) == FROZEN[(btc, eth)]
    finally:
        con.close()


@pytest.mark.parametrize(("btc", "eth"), list(FROZEN))
def test_gpt_usdc_depots_leave_the_other_usdc_depots_unchanged(
    tmp_path: Path, btc: str, eth: str
) -> None:
    alone = play(tmp_path / "alone.db", BotConfig((), None, USDC_AGENTS), btc, eth)
    both = play(tmp_path / "both.db", load_config({"OPENAI_API_KEY": KEY}), btc, eth)
    try:
        traded = both.execute("SELECT COUNT(*) FROM trades WHERE agent LIKE 'gpt-%-usdc'")
        assert traded.fetchone()[0] > 0
        assert rows(alone, ids=False) == rows(both, ids=False)
        assert rows(alone, ids=False)["trades"]
    finally:
        alone.close()
        both.close()
