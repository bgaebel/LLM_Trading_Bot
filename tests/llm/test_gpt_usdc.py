"""Spec 022 AK4/AK5 (ADR-0032): the GPT USDC depots ask the same prompt with the route's costs,
trade and value their BTC over the USDC route, keep ETH on ETH-EUR and fail closed without the
route's quotes."""

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fakes import UsdcScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.llm.agents import EURO_FIELD, GPT_USDC, USDC_FIELD, BotConfig, load_config
from llm_trading_bot.llm.contract import PROMPT_VERSION, SYSTEM, prompt_sha256
from llm_trading_bot.llm.cycle import USDC_SIGNAL_VERSION
from llm_trading_bot.market.models import QuoteBook, Symbol
from llm_trading_bot.market.snapshot import build_snapshots
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import balance, open_db, write_tx
from llm_trading_bot.paper.execution import Costs
from llm_trading_bot.paper.observations import ObservationBuffer
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload

ENV = {"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna,gpt-sol", USDC_FIELD: ""}
"""Only the GPT USDC depots (and their benchmark): nothing else moves the rows below."""
GPT = ("gpt-luna-usdc", "gpt-sol-usdc")


def gpt_config() -> BotConfig:
    config = load_config(ENV)
    assert config.agents == GPT and config.depots == (*GPT, usdc.BUYHOLD_USDC)
    return config


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=gpt_config().depots)
    yield connection
    connection.close()


def run(
    con: sqlite3.Connection,
    client: UsdcScenarioClient,
    model: FakeModel,
    observations: ObservationBuffer | None = None,
) -> CycleReport:
    return paper_cycle(
        con,
        client,
        gpt_config(),
        costs=Costs(),
        model=model,
        clock=lambda: NOW,
        observations=observations,
    )


def trades(con: sqlite3.Connection, agent: str) -> list[dict[str, Any]]:
    rows = con.execute("SELECT * FROM trades WHERE agent=? ORDER BY id", (agent,)).fetchall()
    return [dict(r) for r in rows]


def decided(con: sqlite3.Connection, agent: str) -> dict[str, str]:
    rows = con.execute("SELECT symbol, status FROM decisions WHERE agent=?", (agent,)).fetchall()
    return {r["symbol"]: r["status"] for r in rows}


# ------------------------------------------------------------------ AK4: the route's costs


def test_gpt_usdc_payload_carries_the_route_costs(con: sqlite3.Connection) -> None:
    client = UsdcScenarioClient()
    model = FakeModel()
    report = run(con, client, model)
    assert [(o.agent, o.status) for o in report.llm] == [(a, "ASKED") for a in GPT]
    assert [c["model"] for c in model.calls] == ["gpt-6-luna", "gpt-6.1-sol"]
    assert model.calls[0]["message"] == model.calls[1]["message"]  # one snapshot for both
    payload = json.loads(model.calls[0]["message"])["markets"]
    # Computed here independently: the route's costs for BTC, v0.3.1's for ETH, weight 0.
    snaps = build_snapshots(client, clock=lambda: NOW)
    book = client.quotes()
    btc, eth = snaps[Symbol.BTCEUR], snaps[Symbol.ETHEUR]
    expected = {
        "BTC-EUR": feature_payload(
            btc, indicators(btc, usdc.roundtrip_cost(book)), 0.0, 0.0015, 0.0006
        ),
        "ETH-EUR": feature_payload(
            eth,
            indicators(eth, cost_roundtrip(eth.quote.spread_bps, 0.0025, 0.0005)),
            0.0,
            0.0025,
            0.0005,
        ),
    }
    assert payload == json.loads(json.dumps(expected, ensure_ascii=False))
    assert (
        payload["BTC-EUR"]["taker_fee_per_side_pct"],
        payload["BTC-EUR"]["slippage_per_side_pct"],
    ) == (
        0.15,
        0.06,
    )
    assert payload["BTC-EUR"]["indicators"]["roundtrip_cost_pct"] == round(
        usdc.roundtrip_cost(book) * 100, 5
    )
    assert (
        payload["ETH-EUR"]["taker_fee_per_side_pct"],
        payload["ETH-EUR"]["slippage_per_side_pct"],
    ) == (
        0.25,
        0.05,
    )
    # The cadence sees the reference signals of exactly this payload (weight 0).
    stored = json.loads(con.execute("SELECT trigger_signals FROM llm_calls LIMIT 1").fetchone()[0])
    assert stored == {
        market: payload[market]["reference_signals"] for market in ("BTC-EUR", "ETH-EUR")
    }


def test_gpt_usdc_trades_btc_over_the_route_and_eth_on_eth_eur(con: sqlite3.Connection) -> None:
    client = UsdcScenarioClient()
    report = run(con, client, FakeModel())
    book = client.quotes()
    for agent in GPT:
        assert decided(con, agent) == {"BTCEUR": "OK", "ETHEUR": "OK"}
        btc, eth = trades(con, agent)
        assert (btc["symbol"], btc["side"], eth["symbol"], eth["side"]) == (
            "BTCUSDC",
            "BUY",
            "ETHEUR",
            "BUY",
        )
        # The route's fill, bit for bit (paper.usdc), as for the classic USDC depots.
        fill = usdc.buy(btc["notional"], book)
        assert (btc["qty"], btc["fill"], btc["fee"]) == (fill.qty, fill.fill, fill.fee)
        assert btc["slip_rate"] == usdc.ROUTE_SLIPPAGE
        assert eth["fee"] == eth["notional"] * 0.0025 and eth["slip_rate"] == 0.0005
        # Valued in euro over the route: BTC-USDC mid times USDC-EUR mid.
        p = balance(con, agent)
        (equity,) = [e for e in report.equity if e.agent == agent]
        mid_eth = book[Symbol.ETHEUR].mid
        assert equity.value == p["cash"] + p["btc"] * usdc.btc_mid(book) + p["eth"] * mid_eth
    assert report.complete


def test_prompt_stays_llm_v1_and_version_marks_usdc(con: sqlite3.Connection) -> None:
    model = FakeModel()
    buffer = ObservationBuffer()
    run(con, UsdcScenarioClient(), model, buffer)
    assert {c["instructions"] for c in model.calls} == {SYSTEM}
    rows = con.execute("SELECT agent, prompt_version, prompt_sha256 FROM llm_calls").fetchall()
    assert [tuple(r) for r in rows] == [(a, "llm-v1", prompt_sha256()) for a in GPT]
    assert PROMPT_VERSION == "llm-v1" and USDC_SIGNAL_VERSION == "llm-v1-usdc"
    for (inputs,) in con.execute(
        "SELECT inputs FROM decisions WHERE agent LIKE 'gpt-%'"
    ).fetchall():
        assert json.loads(inputs)["_llm"]["prompt_version"] == "llm-v1"
    versions = {(d[1], d[2], d[5]) for d in buffer.decisions if str(d[1]).startswith("gpt-")}
    assert versions == {(a, s, "llm-v1-usdc") for a in GPT for s in ("BTCEUR", "ETHEUR")}


def test_gpt_usdc_shares_model_and_budget_with_its_euro_twin() -> None:
    env = {
        **ENV,
        "LLM_DAILY_BUDGET_EUR_GPT_LUNA": "0.70",
        "OPENAI_MODEL_GPT_SOL": "gpt-6-astra",
        "LLM_PRICE_IN_EUR_GPT_SOL": "10",
        "LLM_PRICE_OUT_EUR_GPT_SOL": "50",
        "LLM_EFFORT": "medium",
    }
    for case in (ENV, env):
        ours, euro = load_config(case).llm, load_config({**case, EURO_FIELD: "1"}).llm
        assert ours is not None and euro is not None
        assert [a.name for a in euro.agents] == list(GPT_USDC.values())
        assert [replace(a, name=GPT_USDC[a.name]) for a in ours.agents] == list(euro.agents)
        assert (ours.total_daily_eur, ours.effort, ours.cadence, ours.max_output_tokens) == (
            euro.total_daily_eur,
            euro.effort,
            euro.cadence,
            euro.max_output_tokens,
        )
    defaults = load_config(ENV).llm
    assert defaults is not None
    assert [(a.model, str(a.daily_eur)) for a in defaults.agents] == [
        ("gpt-6-luna", "0.50"),
        ("gpt-6.1-sol", "2.50"),
    ]


# ------------------------------------------------------------------ AK5: fail closed


@pytest.mark.parametrize("gone", usdc.ROUTE_MARKETS)
def test_missing_route_quote_keeps_btc_out_of_the_request(
    con: sqlite3.Connection, gone: Symbol
) -> None:
    client = UsdcScenarioClient()
    client.drop_usdc = {gone}
    model = FakeModel()
    report = run(con, client, model)
    assert {tuple(c["markets"]) for c in model.calls} == {("ETH-EUR",)}
    assert [o.status for o in report.llm] == ["ASKED", "ASKED"]
    for agent in GPT:
        assert decided(con, agent) == {"ETHEUR": "OK"}
        assert [t["symbol"] for t in trades(con, agent)] == ["ETHEUR"]


def test_held_btc_without_route_quotes_asks_nothing(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET btc=0.01 WHERE agent LIKE 'gpt-%'")
    client = UsdcScenarioClient()
    client.drop_usdc = {Symbol.USDCEUR}
    model = FakeModel()
    report = run(con, client, model)
    assert [(o.agent, o.status) for o in report.llm] == [(a, "NO_QUOTES") for a in GPT]
    # Not asked, nothing reserved: no budget is spent on a request that could not act.
    assert model.calls == [] and con.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
    assert {u.agent for u in report.unvalued} >= set(GPT)  # and no equity point either


class LosesFromCall(UsdcScenarioClient):
    """Drops a route market from the ``n``-th ticker call on (3 = the fetch after the answers)."""

    def __init__(self, market: Symbol, n: int) -> None:
        super().__init__()
        self.market, self.n = market, n

    def quotes(self) -> QuoteBook:
        self.drop_usdc = {self.market} if self.quote_calls + 1 >= self.n else set()
        return super().quotes()


@pytest.mark.parametrize("market", usdc.ROUTE_MARKETS)
def test_route_quote_lost_after_the_answer_costs_only_btc(
    con: sqlite3.Connection, market: Symbol
) -> None:
    client = LosesFromCall(market, 3)
    report = run(con, client, FakeModel())
    assert client.quote_calls == 3
    assert [o.status for o in report.llm] == ["ASKED", "ASKED"]
    for agent in GPT:
        assert decided(con, agent) == {"BTCEUR": "STALE_QUOTE", "ETHEUR": "OK"}
        assert [t["symbol"] for t in trades(con, agent)] == ["ETHEUR"]
