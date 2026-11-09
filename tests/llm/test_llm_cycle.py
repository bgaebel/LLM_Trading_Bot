"""Spec 005 AK3-AK7: GPT depots in the paper cycle - asked when due, in parallel, fail closed."""

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel, entry, valid_reply
from llm_trading_bot.llm.agents import BotConfig, load_config
from llm_trading_bot.llm.budget import Attempt, Pricing, request_worst_case
from llm_trading_bot.llm.contract import SYSTEM, response_schema
from llm_trading_bot.llm.cycle import market_triggers, run_llm_depots
from llm_trading_bot.llm.due import iso, llm_due, trigger_state
from llm_trading_bot.llm.openai_client import ModelReply
from llm_trading_bot.market.http import HTTPFailure
from llm_trading_bot.market.models import SYMBOLS, Quote, QuoteBook, Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot, build_snapshots
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db, write_tx
from llm_trading_bot.paper.execution import (
    BUYHOLD,
    Costs,
    Quotes,
    current_weight,
    latest_quotes,
    record_quotes,
)
from llm_trading_bot.paper.report import CycleReport, LlmOutcome, MarketResult
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload
from llm_trading_bot.strategy.signals import run_strategy

ENV = {"OPENAI_API_KEY": KEY, "BOT_USDC_AGENTS": "", "BOT_EURO_DEPOTS": "1", "BOT_BUYHOLD": "1"}
REFERENCE = ("trend", "breakout", "meanrev")


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def slow(
    seconds: float, clock: Clock, then: Callable[[list[str]], ModelReply] = valid_reply
) -> Any:
    """A reply that takes ``seconds`` of (fake) time."""

    def reply(markets: list[str]) -> ModelReply:
        clock.now = max(clock.now, NOW + seconds)
        return then(markets)

    return reply


class ShiftingClient(ScenarioClient):
    """Quotes move by ``factor`` for ``symbol`` from the n-th ticker call on."""

    def __init__(self, symbol: Symbol, factor: float, from_call: int) -> None:
        super().__init__()
        self.symbol, self.factor, self.from_call = symbol, factor, from_call

    def quotes(self) -> QuoteBook:
        result = super().quotes()
        if self.quote_calls >= self.from_call:
            q = result[self.symbol]
            result[self.symbol] = Quote(q.bid * self.factor, q.ask * self.factor)
        return result


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
    clock: Callable[[], float] | None = None,
    **env: str,
) -> CycleReport:
    return paper_cycle(
        con,
        client or ScenarioClient(),
        config(**env),
        costs=Costs(),
        model=model,
        clock=clock or Clock(),
    )


def by_agent(report: CycleReport) -> dict[str, LlmOutcome]:
    return {o.agent: o for o in report.llm}


def calls(con: sqlite3.Connection) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = con.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
    return rows


def decisions(report: CycleReport, agent: str) -> dict[Symbol, MarketResult]:
    return {m.symbol: m for m in report.markets if m.agent == agent}


# ------------------------------------------------------------------ the regular path


def test_both_depots_get_the_same_request(con: sqlite3.Connection) -> None:
    model = FakeModel()
    report = cycle(con, model)
    assert [(o.agent, o.status) for o in report.llm] == [
        ("gpt-luna", "ASKED"),
        ("gpt-sol", "ASKED"),
    ]
    assert sorted(c["model"] for c in model.calls) == ["gpt-6-luna", "gpt-6.1-sol"]
    assert model.calls[0]["message"] == model.calls[1]["message"]  # same snapshot, same data
    assert {tuple(c["markets"]) for c in model.calls} == {("BTC-EUR", "ETH-EUR")}
    assert {(c["effort"], c["max_output_tokens"]) for c in model.calls} == {("low", 8000)}
    for agent in ("gpt-luna", "gpt-sol"):
        made = decisions(report, agent)
        assert {(m.action, m.status, m.traded) for m in made.values()} == {("BUY", "OK", True)}
    assert report.complete
    assert by_agent(report)["gpt-luna"].regimes == {
        Symbol.BTCEUR: "UPTREND",
        Symbol.ETHEUR: "UPTREND",
    }
    assert [e.agent for e in report.equity] == ["gpt-luna", "gpt-sol", BUYHOLD]


def test_second_cycle_within_the_cooldown_asks_nobody(con: sqlite3.Connection) -> None:
    model = FakeModel()
    cycle(con, model)
    report = cycle(con, model, clock=Clock(NOW + 5))
    assert [o.status for o in report.llm] == ["NOT_DUE", "NOT_DUE"]
    assert len(model.calls) == 2 and len(calls(con)) == 2


def test_own_trades_do_not_trigger_another_request(con: sqlite3.Connection) -> None:
    """After a buy, the weight-dependent reference signals flip (breakout/meanrev SELL), but the
    market itself is unchanged: after the cooldown the depot must still not be due."""
    client = ScenarioClient()
    cycle(con, FakeModel(), client=client)
    snaps = build_snapshots(client, clock=lambda: NOW)
    mids = {s: snap.quote.mid for s, snap in snaps.items()}
    # Computed here independently of the code under test: market-only means weight 0.
    triggers = {}
    for s, snap in snaps.items():
        ind = indicators(snap, cost_roundtrip(snap.quote.spread_bps, 0.0025, 0.0005))
        triggers[s] = {n: run_strategy(n, snap, ind, 0.0).action for n in REFERENCE}
    assert market_triggers(snaps, Costs()) == triggers
    assert calls(con)[0]["trigger_signals"] == trigger_state(triggers)
    settings = config().llm
    assert settings is not None
    for agent in ("gpt-luna", "gpt-sol"):
        assert not llm_due(con, agent, mids, triggers, NOW + 600, settings.cadence)
    # A real change of a market-only signal does trigger.
    changed = {**triggers, Symbol.ETHEUR: {**triggers[Symbol.ETHEUR], "trend": "SELL"}}
    assert llm_due(con, "gpt-luna", mids, changed, NOW + 600, settings.cadence)


def test_decision_links_to_its_attempt_and_regime(con: sqlite3.Connection) -> None:
    clock = Clock()
    cycle(con, FakeModel({"gpt-6-luna": slow(30, clock)}), clock=clock)
    attempt_id = calls(con)[0]["id"]
    row = con.execute(
        "SELECT ts, bar_ts, model, inputs FROM decisions WHERE agent='gpt-luna' AND symbol='BTCEUR'"
    ).fetchone()
    # Stamped when the decision was taken, not when the cycle started.
    assert row["ts"] == iso(NOW + 30)
    assert row["bar_ts"] == int(NOW + 30) * 10000 + attempt_id
    meta = json.loads(row["inputs"])["_llm"]
    assert (meta["attempt"], meta["market_regime"], meta["prompt_version"]) == (
        attempt_id,
        "UPTREND",
        "llm-v1",
    )


# ------------------------------------------------------------------ isolation and timing (AK7)


def test_timeout_of_one_depot_does_not_block_the_other(con: sqlite3.Connection) -> None:
    clock = Clock()

    def timeout(_markets: list[str]) -> ModelReply:
        clock.now = NOW + 60
        raise openai.APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com"))

    report = cycle(con, FakeModel({"gpt-6-luna": timeout}), clock=clock)
    luna, sol = report.llm
    assert (luna.status, luna.detail, sol.status) == ("ERROR", "APITimeoutError", "ASKED")
    assert {m.agent for m in report.markets} == {"gpt-sol"}
    assert {m.status for m in report.markets} == {"OK"}
    assert not report.complete
    luna_row = calls(con)[0]
    # Unknown cost: the whole reservation stays booked.
    assert (luna_row["status"], luna_row["error"], luna_row["cost_eur"]) == (
        "ERROR",
        "APITimeoutError",
        None,
    )
    assert luna_row["reserved_eur"] is not None


def test_a_slow_depot_does_not_cost_the_other_its_snapshot(con: sqlite3.Connection) -> None:
    """Both requests run at the same time: 100 s for luna leave sol's answer usable."""
    clock = Clock()
    model = FakeModel({"gpt-6-luna": slow(100, clock)})
    report = cycle(con, model, clock=clock)
    assert [o.status for o in report.llm] == ["ASKED", "ASKED"]
    assert {m.status for m in report.markets} == {"OK"}


@pytest.mark.parametrize(("seconds", "status"), [(120, "OK"), (121, "STALE_QUOTE")])
def test_answer_older_than_the_snapshot_limit_is_not_traded(
    con: sqlite3.Connection, seconds: int, status: str
) -> None:
    clock = Clock()
    report = cycle(con, FakeModel({"gpt-6-luna": slow(seconds, clock)}), clock=clock)
    assert {m.status for m in decisions(report, "gpt-luna").values()} == {status}


# ------------------------------------------------------------------ unusable answers


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
        (lambda m: ModelReply("completed", None, None, None, "r", None, None), "no output text"),
        (
            lambda m: ModelReply("incomplete", None, None, "max_output_tokens", "r", None, None),
            "status incomplete (max_output_tokens)",
        ),
        (
            lambda m: valid_reply(m, ETH_EUR=entry("SELL", 0.25)),
            "inconsistent action and target",
        ),
    ],
    ids=["not-json", "refusal", "no-text", "incomplete", "half-invalid"],
)
def test_unusable_answers_trade_nothing(con: sqlite3.Connection, reply: Any, detail: str) -> None:
    report = cycle(con, FakeModel({"gpt-6.1-sol": reply}))
    sol = by_agent(report)["gpt-sol"]
    assert sol.status == "ERROR" and detail in sol.detail
    assert not decisions(report, "gpt-sol")
    assert con.execute("SELECT COUNT(*) FROM trades WHERE agent='gpt-sol'").fetchone()[0] == 0
    assert calls(con)[1]["status"] == "ERROR"


# ------------------------------------------------------------------ budget and caps


def test_depot_over_its_budget_is_not_called(con: sqlite3.Connection) -> None:
    model = FakeModel()
    report = cycle(con, model, LLM_DAILY_BUDGET_EUR_GPT_SOL="0")
    assert by_agent(report)["gpt-sol"].status == "BUDGET_BLOCKED"
    assert [c["model"] for c in model.calls] == ["gpt-6-luna"]


def test_total_budget_from_the_configuration_binds_across_depots(con: sqlite3.Connection) -> None:
    model = FakeModel()
    report = cycle(con, model, LLM_DAILY_BUDGET_EUR="0.10")
    sol = by_agent(report)["gpt-sol"]
    assert by_agent(report)["gpt-luna"].status == "ASKED"
    assert sol.status == "BUDGET_BLOCKED" and "total budget" in sol.detail
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


def test_reservation_covers_every_possible_request_and_retries_are_billed(
    con: sqlite3.Connection,
) -> None:
    def retried(markets: list[str]) -> ModelReply:
        reply = valid_reply(markets)
        return ModelReply(
            reply.status, reply.text, None, None, "resp_r", None, reply.usage, requests=3
        )

    cycle(con, FakeModel({"gpt-6-luna": retried}))
    row = calls(con)[0]
    luna_prices = Pricing(Decimal("0.10"), Decimal("0.50"))
    chars = len(SYSTEM) + len(row["context"]) + len(json.dumps(response_schema(SYMBOLS)))
    one = Attempt(
        "gpt-luna",
        row["markets"],
        None,
        None,
        row["model"],
        "llm-v1",
        "",
        8000,
        "low",
        row["context"],
        chars,
    )
    worst_one = request_worst_case(one, luna_prices)
    assert Decimal(row["reserved_eur"]) == 3 * worst_one  # 1 request + 2 retries
    usage_cost = (Decimal(1500) * Decimal("0.10") + Decimal(700) * Decimal("0.50")) / 10**6
    assert Decimal(row["cost_eur"]) == usage_cost + 2 * worst_one


# ------------------------------------------------------------------ fresh quotes (AK6)


def _direct(
    con: sqlite3.Connection,
    *,
    quotes_at: float,
    now: float,
    refreshed: Callable[[Quotes], Quotes | None] = lambda qs: qs,
    model: FakeModel | None = None,
) -> tuple[list[tuple[LlmOutcome, list[MarketResult]]], dict[Symbol, MarketSnapshot]]:
    client = ScenarioClient()
    snaps = build_snapshots(client, clock=lambda: NOW)
    record_quotes(con, client.quotes(), now=iso(quotes_at))
    stored = latest_quotes(con, now_epoch=quotes_at)
    assert stored is not None
    settings = config().llm
    assert settings is not None
    out = run_llm_depots(
        con,
        settings.agents,
        settings,
        model or FakeModel(),
        snaps,
        Costs(),
        refresh_quotes=lambda: refreshed(stored),
        clock=lambda: now,
    )
    return out, snaps


def test_no_request_without_fresh_quotes(con: sqlite3.Connection) -> None:
    model = FakeModel()
    out, _ = _direct(con, quotes_at=NOW - 21, now=NOW, model=model)
    assert [o.status for o, _ in out] == ["NO_QUOTES", "NO_QUOTES"]
    assert model.calls == [] and calls(con) == []


@pytest.mark.parametrize(("age", "status"), [(120, "ASKED"), (121, "NO_MARKETS")])
def test_snapshot_age_limit_before_the_request(
    con: sqlite3.Connection, age: int, status: str
) -> None:
    out, _ = _direct(con, quotes_at=NOW + age, now=NOW + age)
    assert {o.status for o, _ in out} == {status}


@pytest.mark.parametrize(("factor", "status"), [(1.0199, "OK"), (1.0201, "STALE_QUOTE")])
def test_mid_drift_limit_after_the_answer(
    con: sqlite3.Connection, factor: float, status: str
) -> None:
    def moved(qs: Quotes) -> Quotes:
        q = qs[Symbol.ETHEUR]
        return {**qs, Symbol.ETHEUR: Quote(q.bid * factor, q.ask * factor)}

    out, _ = _direct(con, quotes_at=NOW, now=NOW, refreshed=moved)
    for _outcome, results in out:
        by_symbol = {m.symbol: m.status for m in results}
        assert by_symbol == {Symbol.BTCEUR: "OK", Symbol.ETHEUR: status}  # BTC unaffected


def test_answer_without_fresh_quotes_afterwards_is_not_traded(con: sqlite3.Connection) -> None:
    out, _ = _direct(con, quotes_at=NOW, now=NOW, refreshed=lambda qs: None)
    statuses = {m.status for _o, results in out for m in results}
    assert statuses == {"STALE_QUOTE"}
    assert con.execute("SELECT COUNT(*) FROM trades WHERE agent LIKE 'gpt-%'").fetchone()[0] == 0


def test_the_cycle_really_fetches_quotes_after_the_answers(con: sqlite3.Connection) -> None:
    """Ticker calls: 1 at cycle start, 2 for the snapshots, 3 after the answers."""
    client = ShiftingClient(Symbol.ETHEUR, 1.03, from_call=3)
    report = cycle(con, FakeModel(), client=client)
    for agent in ("gpt-luna", "gpt-sol"):
        made = decisions(report, agent)
        assert (made[Symbol.BTCEUR].status, made[Symbol.ETHEUR].status) == ("OK", "STALE_QUOTE")
    assert client.quote_calls == 3


class FailingFromClient(ScenarioClient):
    """The ticker fails from the n-th call on (after the answers: call 3)."""

    def __init__(self, from_call: int) -> None:
        super().__init__()
        self.from_call = from_call

    def quotes(self) -> QuoteBook:
        if self.quote_calls + 1 >= self.from_call:
            self.quote_calls += 1
            raise HTTPFailure(503)
        return super().quotes()


def test_a_failed_fetch_after_the_answers_trades_nothing(con: sqlite3.Connection) -> None:
    # The quotes stored at the cycle start are still young enough for latest_quotes; using
    # them would trade at a price from before the answer and make the drift check pointless.
    client = FailingFromClient(from_call=3)
    report = cycle(con, FakeModel(), client=client)
    assert client.quote_calls == 3
    for agent in ("gpt-luna", "gpt-sol"):
        assert {m.status for m in decisions(report, agent).values()} == {"STALE_QUOTE"}
    assert con.execute("SELECT COUNT(*) FROM trades WHERE agent LIKE 'gpt-%'").fetchone()[0] == 0
    assert {r["status"] for r in calls(con)} == {"OK"} and all(r["cost_eur"] for r in calls(con))


# ------------------------------------------------------------------ other skips


def test_missing_eth_snapshot_asks_about_btc_only(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.fail_candles = {Symbol.ETHEUR}
    model = FakeModel()
    report = cycle(con, model, client=client)
    assert {tuple(c["markets"]) for c in model.calls} == {("BTC-EUR",)}
    assert {tuple(json.loads(c["message"])["markets"]) for c in model.calls} == {("BTC-EUR",)}
    assert {m.symbol for m in report.markets} == {Symbol.BTCEUR}
    assert calls(con)[0]["markets"] == "BTC-EUR" and calls(con)[0]["ref_mid_eth"] is None


def test_halted_depot_is_not_asked(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET halted=1 WHERE agent='gpt-luna'")
    model = FakeModel()
    report = cycle(con, model)
    assert by_agent(report)["gpt-luna"].status == "HALTED"
    assert [c["model"] for c in model.calls] == ["gpt-6.1-sol"]


def test_halted_depot_costs_nothing_and_is_no_error(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET halted=1 WHERE agent='gpt-luna'")
    report = cycle(con, FakeModel())
    luna = by_agent(report)["gpt-luna"]
    assert (luna.status, luna.cost_eur, luna.attempt_id) == ("HALTED", None, None)
    assert [c["agent"] for c in calls(con)] == ["gpt-sol"]
    assert report.complete


def test_halted_in_the_middle_of_an_answer_stores_but_does_not_trade_eth(
    con: sqlite3.Connection,
) -> None:
    # The BTC trade's mark triggers the stop (peak far above); the ETH decision of the same
    # answer is stored, but not traded - like the Arena (record_decision reads halted again).
    with write_tx(con):
        con.execute("UPDATE portfolios SET peak=20000 WHERE agent='gpt-luna'")
    report = cycle(con, FakeModel())
    luna = decisions(report, "gpt-luna")
    assert (luna[Symbol.BTCEUR].status, luna[Symbol.BTCEUR].traded) == ("OK", True)
    assert (luna[Symbol.ETHEUR].status, luna[Symbol.ETHEUR].traded) == ("OK", False)
    assert luna[Symbol.ETHEUR].new and luna[Symbol.ETHEUR].action != "HOLD"
    assert [s.agent for s in report.risk_stops] == ["gpt-luna"]
    sides = [
        (r[0], r[1])
        for r in con.execute("SELECT symbol, side FROM trades WHERE agent='gpt-luna' ORDER BY id")
    ]
    assert sides == [("BTCEUR", "BUY"), ("BTCEUR", "RISK_SELL")]
    assert report.complete


# ------------------------------------------------------------------ review follow-ups


class ThreadClock:
    """Each model thread may advance only its own time; the main thread keeps the base."""

    def __init__(self, now: float = NOW) -> None:
        self.base = now
        self.local = threading.local()

    def __call__(self) -> float:
        value: float = getattr(self.local, "now", self.base)
        return value

    def advance_this_thread(self, to: float) -> None:
        self.local.now = to


def test_requests_really_run_in_parallel(con: sqlite3.Connection) -> None:
    """luna answers only after sol was asked: sequential requests would make luna time out."""
    sol_asked = threading.Event()

    def luna(markets: list[str]) -> ModelReply:
        if not sol_asked.wait(timeout=5):
            raise openai.APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com"))
        return valid_reply(markets)

    def sol(markets: list[str]) -> ModelReply:
        sol_asked.set()
        return valid_reply(markets)

    report = cycle(con, FakeModel({"gpt-6-luna": luna, "gpt-6.1-sol": sol}))
    assert [o.status for o in report.llm] == ["ASKED", "ASKED"]


def test_a_very_slow_depot_does_not_make_the_other_stale(con: sqlite3.Connection) -> None:
    clock = ThreadClock()

    def after(seconds: float) -> Callable[[list[str]], ModelReply]:
        def reply(markets: list[str]) -> ModelReply:
            clock.advance_this_thread(NOW + seconds)
            return valid_reply(markets)

        return reply

    model = FakeModel({"gpt-6-luna": after(125), "gpt-6.1-sol": after(5)})
    report = cycle(con, model, clock=clock)
    assert {m.status for m in decisions(report, "gpt-luna").values()} == {"STALE_QUOTE"}
    assert {m.status for m in decisions(report, "gpt-sol").values()} == {"OK"}


def test_second_real_cycle_after_the_cooldown_is_not_due(con: sqlite3.Connection) -> None:
    """Same market, but both depots traded in between: their own trades must not count."""
    client = ScenarioClient()
    snaps = build_snapshots(client, clock=lambda: NOW)
    settings = config().llm
    assert settings is not None

    def run(at: float) -> list[tuple[LlmOutcome, list[MarketResult]]]:
        moved = {
            s: replace(snap, fetched_at=int(at), quote_at=int(at)) for s, snap in snaps.items()
        }
        record_quotes(con, client.quotes(), now=iso(at))
        return run_llm_depots(
            con,
            settings.agents,
            settings,
            FakeModel(),
            moved,
            Costs(),
            refresh_quotes=lambda: latest_quotes(con, now_epoch=at),
            clock=lambda: at,
        )

    first = run(NOW)
    assert all(m.traded for _o, results in first for m in results)
    second = run(NOW + 600)
    assert [o.status for o, _ in second] == ["NOT_DUE", "NOT_DUE"]


def test_message_carries_the_arena_payload_with_the_depot_weight(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    model = FakeModel()
    cycle(con, model, client=client)
    snaps = build_snapshots(ScenarioClient(), clock=lambda: NOW)
    sent = json.loads(model.calls[0]["message"])["markets"]
    for symbol, snap in snaps.items():
        ind = indicators(snap, cost_roundtrip(snap.quote.spread_bps, 0.0025, 0.0005))
        expected = feature_payload(snap, ind, 0.0, 0.0025, 0.0005)  # flat depot before the call
        assert sent[symbol.market] == json.loads(json.dumps(expected))


def test_message_carries_the_weight_of_a_depot_that_holds_coins(con: sqlite3.Connection) -> None:
    # HOLD keeps the existing weight: a lost weight in the prompt would change decisions.
    with write_tx(con):
        con.execute("UPDATE portfolios SET cash=6000, btc=0.04, eth=0.5 WHERE agent='gpt-luna'")
    model = FakeModel()
    cycle(con, model)
    qs = ScenarioClient().quotes()
    luna = next(c for c in model.calls if c["model"] == "gpt-6-luna")
    sol = next(c for c in model.calls if c["model"] == "gpt-6.1-sol")
    sent = json.loads(luna["message"])["markets"]
    held = {"cash": 6000.0, "btc": 0.04, "eth": 0.5}
    for symbol, snap in build_snapshots(ScenarioClient(), clock=lambda: NOW).items():
        weight = current_weight(held, symbol, qs)
        assert 0.05 < weight < 0.95
        ind = indicators(snap, cost_roundtrip(snap.quote.spread_bps, 0.0025, 0.0005))
        expected = feature_payload(snap, ind, weight, 0.0025, 0.0005)
        assert sent[symbol.market] == json.loads(json.dumps(expected))
    assert luna["message"] != sol["message"]  # sol is still flat


def test_failing_quote_refresh_still_settles_every_paid_answer(con: sqlite3.Connection) -> None:
    def broken(_qs: Quotes) -> Quotes | None:
        raise sqlite3.OperationalError("database is locked")

    out, _ = _direct(con, quotes_at=NOW, now=NOW, refreshed=broken)
    assert [o.status for o, _ in out] == ["ASKED", "ASKED"]
    assert {m.status for _o, results in out for m in results} == {"STALE_QUOTE"}
    assert {r["status"] for r in calls(con)} == {"OK"} and all(r["cost_eur"] for r in calls(con))


@pytest.mark.parametrize(
    ("status", "error", "free"),
    [
        (401, openai.AuthenticationError, True),
        (400, openai.BadRequestError, True),
        (404, openai.NotFoundError, True),
        (500, openai.InternalServerError, False),
    ],
)
def test_immediate_rejections_cost_nothing_server_errors_keep_the_reservation(
    con: sqlite3.Connection, status: int, error: type[openai.APIStatusError], free: bool
) -> None:
    def rejected(_markets: list[str]) -> ModelReply:
        response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.openai.com"))
        raise error("rejected", response=response, body=None)

    cycle(con, FakeModel({"gpt-6-luna": rejected}))
    row = calls(con)[0]
    assert row["status"] == "ERROR"
    if free:
        assert row["cost_eur"] is not None and Decimal(row["cost_eur"]) == 0
    else:
        assert row["cost_eur"] is None  # possibly billed: the reservation stays


# ------------------------------------------------------------------ Spec 011 AK7: per market


def test_quote_lost_after_answer_costs_only_that_market(con: sqlite3.Connection) -> None:
    def only_btc(qs: Quotes) -> Quotes:
        return {Symbol.BTCEUR: qs[Symbol.BTCEUR]}

    out, _ = _direct(con, quotes_at=NOW, now=NOW, refreshed=only_btc)
    for outcome, results in out:
        assert outcome.status == "ASKED"  # billed as usual
        by_symbol = {m.symbol: m.status for m in results}
        assert by_symbol == {Symbol.BTCEUR: "OK", Symbol.ETHEUR: "STALE_QUOTE"}


def test_quote_lost_after_answer_of_a_held_market_blocks_both(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET eth=0.5")

    def only_btc(qs: Quotes) -> Quotes:
        return {Symbol.BTCEUR: qs[Symbol.BTCEUR]}

    out, _ = _direct(con, quotes_at=NOW, now=NOW, refreshed=only_btc)
    for _outcome, results in out:
        assert {m.status for m in results} == {"STALE_QUOTE"}
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_request_covers_only_markets_with_a_fresh_quote(con: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.bad_quotes = {Symbol.ETHEUR}
    model = FakeModel()
    report = cycle(con, model, client=client)
    assert {o.status for o in report.llm} == {"ASKED"}
    assert {tuple(c["markets"]) for c in model.calls} == {("BTC-EUR",)}


def test_no_request_while_a_held_market_has_no_fresh_quote(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET eth=0.5 WHERE agent='gpt-luna'")
    client = ScenarioClient()
    client.bad_quotes = {Symbol.ETHEUR}
    model = FakeModel()
    report = cycle(con, model, client=client)
    assert by_agent(report)["gpt-luna"].status == "NO_QUOTES"
    assert [c["model"] for c in model.calls] == ["gpt-6.1-sol"]
    assert [c["agent"] for c in calls(con)] == ["gpt-sol"]  # no reservation for luna


def test_paused_depots_are_not_asked_and_reserve_nothing(con: sqlite3.Connection) -> None:
    """Spec 011 AK6: no paid request while trading is paused."""
    client = ScenarioClient()
    snaps = build_snapshots(client, clock=lambda: NOW)
    record_quotes(con, client.quotes(), now=iso(NOW))
    settings = config().llm
    assert settings is not None
    model = FakeModel()
    out = run_llm_depots(
        con,
        settings.agents,
        settings,
        model,
        snaps,
        Costs(),
        refresh_quotes=lambda: None,
        clock=lambda: NOW,
        paused=True,
    )
    assert [(o.status, r) for o, r in out] == [("PAUSED", []), ("PAUSED", [])]
    assert model.calls == [] and calls(con) == []
