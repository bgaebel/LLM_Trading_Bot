# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - llm_cycle
"""One GPT depot in one paper cycle (ADR-0013): due? -> hourly cap -> reservation -> request ->
settlement -> strict check -> fresh-quote recheck per market -> virtual decision per market.

Fails closed at every step: nothing is traded on an invalid, refused, incomplete or stale answer.
"""

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from typing import Any

from llm_trading_bot.llm.agents import LlmAgent, LlmSettings
from llm_trading_bot.llm.budget import Attempt, BudgetBlocked, Limits, Pricing, reserve, settle
from llm_trading_bot.llm.contract import (
    PROMPT_VERSION,
    SYSTEM,
    InvalidSignal,
    ordered,
    parse_answer,
    prompt_sha256,
    response_schema,
    user_message,
)
from llm_trading_bot.llm.due import hourly_limit_reached, iso, llm_due
from llm_trading_bot.llm.openai_client import API_ERRORS, SignalModel
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot
from llm_trading_bot.paper.db import balance
from llm_trading_bot.paper.execution import Costs, Quotes, current_weight, record_decision
from llm_trading_bot.paper.report import LlmOutcome, MarketResult, decision_result
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload

# Arena llm_cycle: an answer is only acted on while its snapshot and the price are still current.
MAX_SNAPSHOT_AGE = 120
MAX_FUTURE_SKEW = 10
MAX_MID_DRIFT = 0.02


def _usable(snaps: Mapping[Symbol, MarketSnapshot], now: float) -> dict[Symbol, MarketSnapshot]:
    return {
        s: snap
        for s, snap in snaps.items()
        if -MAX_FUTURE_SKEW <= now - snap.fetched_at <= MAX_SNAPSHOT_AGE
    }


def llm_cycle(
    con: sqlite3.Connection,
    agent: LlmAgent,
    settings: LlmSettings,
    model: SignalModel,
    snaps: Mapping[Symbol, MarketSnapshot],
    qs: Quotes,
    costs: Costs,
    *,
    refresh_quotes: Callable[[], Quotes | None],
    clock: Callable[[], float] = time.time,
) -> tuple[LlmOutcome, list[MarketResult]]:
    now_epoch = clock()

    def outcome(status: str, detail: str = "", **kw: Any) -> LlmOutcome:
        return LlmOutcome(agent.name, agent.model, status, detail, **kw)

    p = balance(con, agent.name)
    if p["halted"]:
        return outcome("HALTED", "portfolio halted"), []
    usable = _usable(snaps, now_epoch)
    if not usable:
        return outcome("NO_MARKETS", "no current snapshot"), []
    payloads: dict[Symbol, dict[str, Any]] = {}
    for s, snap in usable.items():
        weight = current_weight(p, s, qs)
        roundtrip = cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage)
        payloads[s] = feature_payload(
            snap, indicators(snap, roundtrip), weight, costs.fee_rate, costs.slippage
        )
    mids = {s: snap.quote.mid for s, snap in usable.items()}
    signals = {s: payloads[s]["reference_signals"] for s in usable}
    if not llm_due(con, agent.name, mids, signals, now_epoch, settings.cadence):
        return outcome("NOT_DUE"), []
    if hourly_limit_reached(con, agent.name, now_epoch, settings.cadence):
        return outcome("HOURLY_LIMIT", f"{settings.cadence.max_calls_per_hour} attempts/h"), []

    markets = ordered(list(usable))
    message = user_message(payloads)
    schema = response_schema(markets)
    attempt = Attempt(
        agent=agent.name,
        markets=",".join(s.market for s in markets),
        ref_mid_btc=mids.get(Symbol.BTCEUR),
        ref_mid_eth=mids.get(Symbol.ETHEUR),
        model=agent.model,
        prompt_version=PROMPT_VERSION,
        prompt_sha256=prompt_sha256(),
        max_output_tokens=settings.max_output_tokens,
        effort=settings.effort,
        context=message,
        input_chars=len(SYSTEM) + len(message) + len(json.dumps(schema)),
    )
    limits = Limits(settings.total_daily_eur, agent.daily_eur, agent.pricing)
    now = iso(now_epoch)
    try:
        attempt_id = reserve(con, attempt, limits, now=now, now_epoch=now_epoch)
    except BudgetBlocked as exc:
        return outcome("BUDGET_BLOCKED", str(exc)), []
    # A reservation exists only with prices (reserve refuses otherwise).
    pricing: Pricing = agent.pricing  # type: ignore[assignment]

    started = time.monotonic()
    try:
        reply = model.ask(
            model=agent.model,
            instructions=SYSTEM,
            message=message,
            schema=schema,
            effort=settings.effort,
            max_output_tokens=settings.max_output_tokens,
        )
    except BaseException as exc:
        # Type name only: provider messages may echo parts of the key. Unknown cost keeps the
        # reservation (usage None), so the budget stays on the safe side.
        settle(
            con,
            attempt_id,
            pricing,
            status="ERROR",
            usage=None,
            latency_seconds=time.monotonic() - started,
            error=type(exc).__name__,
        )
        if isinstance(exc, API_ERRORS):
            return outcome("ERROR", type(exc).__name__, attempt_id=attempt_id), []
        raise
    latency = time.monotonic() - started
    problem = reply.problem()
    answers = None
    if not problem:
        try:
            answers = parse_answer(reply.text or "", markets, strategy=agent.name)
        except InvalidSignal as exc:
            problem = f"InvalidSignal: {exc}"
    cost = settle(
        con,
        attempt_id,
        pricing,
        status="ERROR" if problem else "OK",
        usage=reply.usage,
        latency_seconds=latency,
        response_id=reply.response_id,
        error=problem or None,
    )
    if answers is None:
        return outcome(
            "ERROR", problem, attempt_id=attempt_id, usage=reply.usage, cost_eur=cost
        ), []

    fresh = refresh_quotes()
    bar_ts = int(now_epoch) * 10000 + attempt_id
    results = []
    for s, answer in answers.items():
        snap = usable[s]
        stale = (
            fresh is None
            or clock() - snap.fetched_at > MAX_SNAPSHOT_AGE
            or abs(fresh[s].mid / snap.quote.mid - 1) > MAX_MID_DRIFT
        )
        data = dict(
            payloads[s],
            _llm={
                "attempt": attempt_id,
                "model": reply.model or agent.model,
                "prompt_version": PROMPT_VERSION,
                "market_regime": answer.regime,
                "response_id": reply.response_id,
            },
        )
        new = record_decision(
            con,
            agent.name,
            s,
            bar_ts,
            reply.model or agent.model,
            answer.signal,
            data,
            costs=costs,
            now=now,
            qs=None if stale else fresh,
            status="STALE_QUOTE" if stale else "OK",
        )
        results.append(decision_result(con, agent.name, s, bar_ts, new))
    regimes = {s: a.regime for s, a in answers.items()}
    return (
        outcome("ASKED", attempt_id=attempt_id, usage=reply.usage, cost_eur=cost, regimes=regimes),
        results,
    )
