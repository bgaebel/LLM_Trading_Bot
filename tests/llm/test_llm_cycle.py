"""Spec 005 AK5-AK7: GPT depots in the paper cycle - asked when due, fail closed, isolated."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel, entry, valid_reply
from llm_trading_bot.llm.agents import BotConfig, load_config
from llm_trading_bot.llm.cycle import llm_cycle
from llm_trading_bot.llm.openai_client import ModelReply
from llm_trading_bot.market.models import Quote, Symbol
from llm_trading_bot.market.snapshot import build_snapshots
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db, write_tx
from llm_trading_bot.paper.execution import BUYHOLD, Costs, latest_quotes, record_quotes
from llm_trading_bot.paper.report import CycleReport

ENV = {"OPENAI_API_KEY": KEY}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("gpt-luna", "gpt-sol", BUYHOLD))
    yield connection
    connection.close()


def config(**env: str) -> BotConfig:
    return load_config({**ENV, **env})


def cycle(
    con: sqlite3.Connection,
    model: FakeModel,
    *,
    client: ScenarioClient | None = None,
    at: float = NOW,
    **env: str,
) -> CycleReport:
    return paper_cycle(
        con,
        client or ScenarioClient(),
        config(**env),
        costs=Costs(),
        model=model,
        clock=lambda: at,
    )


def by_agent(report: CycleReport) -> dict[str, Any]:
    return {o.agent: o for o in report.llm}


def calls(con: sqlite3.Connection) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = con.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
    return rows


def test_both_depots_get_the_same_request_luna_first(con: sqlite3.Connection) -> None:
    model = FakeModel()
    report = cycle(con, model)
    assert [o.status for o in report.llm] == ["ASKED", "ASKED"]
    assert [c["model"] for c in model.calls] == ["gpt-6-luna", "gpt-6.1-sol"]
    assert model.calls[0]["message"] == model.calls[1]["message"]  # same snapshot, same data
    assert model.calls[0]["markets"] == ["BTC-EUR", "ETH-EUR"]
    assert {(c["effort"], c["max_output_tokens"]) for c in model.calls} == {("low", 8000)}
    decisions = [(m.agent, m.symbol, m.action, m.status, m.traded) for m in report.markets]
    assert decisions == [
        ("gpt-luna", Symbol.BTCEUR, "BUY", "OK", True),
        ("gpt-luna", Symbol.ETHEUR, "BUY", "OK", True),
        ("gpt-sol", Symbol.BTCEUR, "BUY", "OK", True),
        ("gpt-sol", Symbol.ETHEUR, "BUY", "OK", True),
    ]
    assert report.complete
    luna = by_agent(report)["gpt-luna"]
    assert luna.regimes == {Symbol.BTCEUR: "UPTREND", Symbol.ETHEUR: "UPTREND"}
    assert [e.agent for e in report.equity] == ["gpt-luna", "gpt-sol", BUYHOLD]


def test_second_cycle_soon_after_asks_nobody(con: sqlite3.Connection) -> None:
    model = FakeModel()
    cycle(con, model)
    report = cycle(con, model, at=NOW + 5)
    assert [o.status for o in report.llm] == ["NOT_DUE", "NOT_DUE"]
    assert len(model.calls) == 2 and len(calls(con)) == 2


def test_timeout_of_one_depot_does_not_block_the_other(con: sqlite3.Connection) -> None:
    def timeout(_markets: list[str]) -> ModelReply:
        raise openai.APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com"))

    report = cycle(con, FakeModel({"gpt-6-luna": timeout}))
    luna, sol = report.llm
    assert (luna.status, luna.detail, sol.status) == ("ERROR", "APITimeoutError", "ASKED")
    assert {m.agent for m in report.markets} == {"gpt-sol"}
    assert not report.complete
    luna_row = calls(con)[0]
    # Unknown cost: the reservation stays booked.
    assert (luna_row["status"], luna_row["error"], luna_row["cost_eur"]) == (
        "ERROR",
        "APITimeoutError",
        None,
    )
    assert luna_row["reserved_eur"] is not None


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        (
            lambda m: ModelReply("completed", "kein JSON", None, None, "r", None, None),
            "InvalidSignal",
        ),
        (
            lambda m: ModelReply("completed", None, "I can't help", None, "r", None, None),
            "refusal",
        ),
        (
            lambda m: ModelReply("incomplete", None, None, "max_output_tokens", "r", None, None),
            "status incomplete (max_output_tokens)",
        ),
        (
            lambda m: valid_reply(m, ETH_EUR=entry("SELL", 0.25)),
            "inconsistent action and target",
        ),
    ],
    ids=["not-json", "refusal", "incomplete", "half-invalid"],
)
def test_unusable_answers_trade_nothing(con: sqlite3.Connection, reply: Any, detail: str) -> None:
    report = cycle(con, FakeModel({"gpt-6.1-sol": reply}))
    sol = by_agent(report)["gpt-sol"]
    assert sol.status == "ERROR" and detail in sol.detail
    assert not [m for m in report.markets if m.agent == "gpt-sol"]
    assert con.execute("SELECT COUNT(*) FROM trades WHERE agent='gpt-sol'").fetchone()[0] == 0
    assert calls(con)[1]["status"] == "ERROR"


def test_depot_over_budget_is_not_called(con: sqlite3.Connection) -> None:
    model = FakeModel()
    report = cycle(con, model, LLM_DAILY_BUDGET_EUR_GPT_SOL="0")
    assert by_agent(report)["gpt-sol"].status == "BUDGET_BLOCKED"
    assert [c["model"] for c in model.calls] == ["gpt-6-luna"]


def test_model_without_prices_is_never_called(con: sqlite3.Connection) -> None:
    model = FakeModel()
    report = cycle(con, model, OPENAI_MODEL_GPT_SOL="gpt-6-astra")
    sol = by_agent(report)["gpt-sol"]
    assert (sol.status, "no prices" in sol.detail) == ("BUDGET_BLOCKED", True)
    assert [c["model"] for c in model.calls] == ["gpt-6-luna"]


def test_hourly_limit_stops_requests(con: sqlite3.Connection) -> None:
    model = FakeModel()
    report = cycle(con, model, LLM_MAX_CALLS_PER_HOUR="0")
    assert [o.status for o in report.llm] == ["HOURLY_LIMIT", "HOURLY_LIMIT"]
    assert model.calls == [] and calls(con) == []


def test_missing_eth_snapshot_asks_about_btc_only(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.fail_candles = {Symbol.ETHEUR}
    model = FakeModel()
    report = cycle(con, model, client=client)
    assert {tuple(c["markets"]) for c in model.calls} == {("BTC-EUR",)}
    assert {m.symbol for m in report.markets} == {Symbol.BTCEUR}
    assert calls(con)[0]["markets"] == "BTC-EUR" and calls(con)[0]["ref_mid_eth"] is None


def test_halted_depot_is_not_asked(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET halted=1 WHERE agent='gpt-luna'")
    model = FakeModel()
    report = cycle(con, model)
    assert by_agent(report)["gpt-luna"].status == "HALTED"
    assert [c["model"] for c in model.calls] == ["gpt-6.1-sol"]


def _direct(con: sqlite3.Connection, refreshed: Any, at: float) -> Any:
    client = ScenarioClient()
    snaps = build_snapshots(client, clock=lambda: NOW)
    record_quotes(con, client.quotes(), now="2027-01-15T08:00:30+00:00")
    qs = latest_quotes(con, now_epoch=NOW)
    assert qs is not None
    settings = config().llm
    assert settings is not None
    return llm_cycle(
        con,
        settings.agents[0],
        settings,
        FakeModel(),
        snaps,
        qs,
        Costs(),
        refresh_quotes=lambda: refreshed(qs),
        clock=lambda: at,
    )


@pytest.mark.parametrize(
    "refreshed",
    [
        lambda qs: None,
        lambda qs: {
            **qs,
            Symbol.ETHEUR: Quote(qs[Symbol.ETHEUR].bid * 1.03, qs[Symbol.ETHEUR].ask * 1.03),
        },
    ],
    ids=["no-fresh-quotes", "eth-moved-3pct"],
)
def test_answer_on_stale_prices_is_stored_but_not_traded(
    con: sqlite3.Connection, refreshed: Any
) -> None:
    outcome, results = _direct(con, refreshed, NOW)
    assert outcome.status == "ASKED"
    status = {m.symbol: m.status for m in results}
    assert status[Symbol.ETHEUR] == "STALE_QUOTE"
    assert con.execute("SELECT COUNT(*) FROM trades WHERE symbol='ETHEUR'").fetchone()[0] == 0


def test_snapshot_older_than_120_seconds_is_not_used(con: sqlite3.Connection) -> None:
    outcome, results = _direct(con, lambda qs: qs, NOW + 121)
    assert (outcome.status, results) == ("NO_MARKETS", [])
    assert calls(con) == []


def test_decision_links_to_its_attempt_and_regime(con: sqlite3.Connection) -> None:
    cycle(con, FakeModel())
    attempt_id = calls(con)[0]["id"]
    row = con.execute(
        "SELECT bar_ts, model, inputs FROM decisions WHERE agent='gpt-luna' AND symbol='BTCEUR'"
    ).fetchone()
    assert row["bar_ts"] == int(NOW) * 10000 + attempt_id
    meta = json.loads(row["inputs"])["_llm"]
    assert (meta["attempt"], meta["market_regime"], meta["prompt_version"]) == (
        attempt_id,
        "UPTREND",
        "llm-v1",
    )
