# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - llm_cycle
"""The GPT depots of one paper cycle (ADR-0012, ADR-0013).

1. Per depot, in order: fresh quotes? due? hourly cap? -> payload -> reservation (committed).
2. All reserved requests run at the same time with the same snapshot, so a slow depot never delays
   another one (AK7).
3. Per depot, in order: settlement -> strict check -> fresh-quote recheck per market -> decision.

Fails closed at every step: nothing is traded on an invalid, refused, incomplete or stale answer.
"""

import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from llm_trading_bot.llm.agents import LlmAgent, LlmSettings
from llm_trading_bot.llm.budget import (
    Attempt,
    BudgetBlocked,
    Limits,
    Pricing,
    Usage,
    request_worst_case,
    reserve,
    settle,
)
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
from llm_trading_bot.llm.due import hourly_limit_reached, iso, llm_due, trigger_state
from llm_trading_bot.llm.openai_client import API_ERRORS, MAX_RETRIES, ModelReply, SignalModel
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot
from llm_trading_bot.paper.db import balance
from llm_trading_bot.paper.execution import (
    Costs,
    Quotes,
    RiskStop,
    current_weight,
    latest_quotes,
    record_decision,
)
from llm_trading_bot.paper.report import LlmOutcome, MarketResult, decision_result
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload
from llm_trading_bot.strategy.signals import run_strategy

# Arena llm_cycle: an answer is only acted on while its snapshot and the price are still current.
MAX_SNAPSHOT_AGE = 120
MAX_FUTURE_SKEW = 10
MAX_MID_DRIFT = 0.02
# The classic strategies whose verdict is part of the payload (strategy/payload.py).
REFERENCE_STRATEGIES = ("trend", "breakout", "meanrev")


def _usable(snaps: Mapping[Symbol, MarketSnapshot], now: float) -> dict[Symbol, MarketSnapshot]:
    return {
        s: snap
        for s, snap in snaps.items()
        if -MAX_FUTURE_SKEW <= now - snap.fetched_at <= MAX_SNAPSHOT_AGE
    }


def market_triggers(
    snaps: Mapping[Symbol, MarketSnapshot], costs: Costs
) -> dict[Symbol, dict[str, str]]:
    """The reference signals as the cadence sees them: with weight 0, so a depot's own trade
    cannot flip them and trigger another paid request. The payload keeps the real weight."""
    triggers: dict[Symbol, dict[str, str]] = {}
    for s, snap in snaps.items():
        ind = indicators(
            snap, cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage)
        )
        triggers[s] = {n: run_strategy(n, snap, ind, 0.0).action for n in REFERENCE_STRATEGIES}
    return triggers


@dataclass
class _Prepared:
    agent: LlmAgent
    attempt_id: int
    pricing: Pricing
    attempt: Attempt
    usable: dict[Symbol, MarketSnapshot]
    payloads: dict[Symbol, dict[str, Any]]
    schema: dict[str, Any]
    reply: ModelReply | None = None
    error: BaseException | None = None
    latency: float = 0.0
    answered_at: float = 0.0
    """Clock time when this depot's own reply arrived - not when the slowest depot finished."""


def _rejected_unbilled(exc: BaseException) -> bool:
    """4xx answers other than timeout/conflict/rate limit: refused before any generation."""
    status = getattr(exc, "status_code", None)
    return (
        isinstance(exc, API_ERRORS)
        and isinstance(status, int)
        and 400 <= status < 500
        and (status not in (408, 409, 429))
    )


def _outcome(agent: LlmAgent, status: str, detail: str = "", **kw: Any) -> LlmOutcome:
    return LlmOutcome(agent.name, agent.model, status, detail, **kw)


def _prepare(
    con: sqlite3.Connection,
    agent: LlmAgent,
    settings: LlmSettings,
    snaps: Mapping[Symbol, MarketSnapshot],
    costs: Costs,
    now_epoch: float,
) -> _Prepared | LlmOutcome:
    p = balance(con, agent.name)
    if p["halted"]:
        return _outcome(agent, "HALTED", "portfolio halted")
    # Arena llm_cycle: no request without fresh quotes of both markets, checked per depot.
    qs = latest_quotes(con, now_epoch=now_epoch)
    if qs is None:
        return _outcome(agent, "NO_QUOTES", "stored quotes missing or stale")
    usable = _usable(snaps, now_epoch)
    if not usable:
        return _outcome(agent, "NO_MARKETS", "no current snapshot")
    payloads: dict[Symbol, dict[str, Any]] = {}
    for s, snap in usable.items():
        roundtrip = cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage)
        payloads[s] = feature_payload(
            snap,
            indicators(snap, roundtrip),
            current_weight(p, s, qs),
            costs.fee_rate,
            costs.slippage,
        )
    triggers = market_triggers(usable, costs)
    mids = {s: snap.quote.mid for s, snap in usable.items()}
    if not llm_due(con, agent.name, mids, triggers, now_epoch, settings.cadence):
        return _outcome(agent, "NOT_DUE")
    if hourly_limit_reached(con, agent.name, now_epoch, settings.cadence):
        return _outcome(agent, "HOURLY_LIMIT", f"{settings.cadence.max_calls_per_hour} attempts/h")
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
        trigger_signals=trigger_state(triggers),
        max_requests=1 + MAX_RETRIES,
    )
    limits = Limits(settings.total_daily_eur, agent.daily_eur, agent.pricing)
    try:
        attempt_id = reserve(con, attempt, limits, now=iso(now_epoch), now_epoch=now_epoch)
    except BudgetBlocked as exc:
        return _outcome(agent, "BUDGET_BLOCKED", str(exc))
    assert agent.pricing is not None  # reserve() refuses an attempt without prices
    return _Prepared(agent, attempt_id, agent.pricing, attempt, usable, payloads, schema)


def _ask(
    prepared: _Prepared, settings: LlmSettings, model: SignalModel, clock: Callable[[], float]
) -> None:
    """Runs in a worker thread: network only, no database access."""
    started = time.monotonic()
    try:
        prepared.reply = model.ask(
            model=prepared.agent.model,
            instructions=SYSTEM,
            message=prepared.attempt.context,
            schema=prepared.schema,
            effort=settings.effort,
            max_output_tokens=settings.max_output_tokens,
        )
    except BaseException as exc:
        prepared.error = exc
    finally:
        prepared.latency = time.monotonic() - started
        prepared.answered_at = clock()


def _finish(
    con: sqlite3.Connection,
    prepared: _Prepared,
    fresh: Quotes | None,
    costs: Costs,
    clock: Callable[[], float],
    stops: list[RiskStop] | None = None,
) -> tuple[LlmOutcome, list[MarketResult]]:
    agent, attempt_id, pricing = prepared.agent, prepared.attempt_id, prepared.pricing
    if prepared.error is not None or prepared.reply is None:
        exc = prepared.error or RuntimeError("no reply")
        # Type name only: provider messages may echo parts of the key. Unknown cost keeps the
        # whole reservation (all possible requests), so the budget stays on the safe side -
        # except for rejections that were answered at once and are not billed (bad key, bad
        # request, unknown model): booking them at 3x worst case would drain the budget.
        unbilled = _rejected_unbilled(exc)
        settle(
            con,
            attempt_id,
            pricing,
            status="ERROR",
            usage=Usage(0, 0) if unbilled else None,
            latency_seconds=prepared.latency,
            error=type(exc).__name__,
        )
        if isinstance(exc, API_ERRORS):
            return _outcome(agent, "ERROR", type(exc).__name__, attempt_id=attempt_id), []
        raise exc
    reply = prepared.reply
    markets = ordered(list(prepared.usable))
    problem = reply.problem()
    answers = None
    if not problem:
        try:
            answers = parse_answer(reply.text or "", markets, strategy=agent.name)
        except InvalidSignal as exc:
            problem = f"InvalidSignal: {exc}"
    # Requests before the last one (retries on 429/5xx) have unknown usage: worst case each.
    extra = request_worst_case(prepared.attempt, pricing) * (reply.requests - 1)
    cost = settle(
        con,
        attempt_id,
        pricing,
        status="ERROR" if problem else "OK",
        usage=reply.usage,
        latency_seconds=prepared.latency,
        response_id=reply.response_id,
        error=problem or None,
        extra_cost=extra,
    )
    if answers is None:
        return _outcome(
            agent, "ERROR", problem, attempt_id=attempt_id, usage=reply.usage, cost_eur=cost
        ), []

    decided_at = prepared.answered_at or clock()
    bar_ts = int(decided_at) * 10000 + attempt_id
    model_name = reply.model or agent.model
    results = []
    for s, answer in answers.items():
        snap = prepared.usable[s]
        stale = (
            fresh is None
            or decided_at - snap.fetched_at > MAX_SNAPSHOT_AGE
            or abs(fresh[s].mid / snap.quote.mid - 1) > MAX_MID_DRIFT
        )
        data = dict(
            prepared.payloads[s],
            _llm={
                "attempt": attempt_id,
                "model": model_name,
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
            model_name,
            answer.signal,
            data,
            costs=costs,
            now=iso(decided_at),
            qs=None if stale else fresh,
            status="STALE_QUOTE" if stale else "OK",
            # A BTC trade may trigger the stop: the ETH decision is then stored but not traded,
            # because record_decision reads the halted flag in its own transaction (Arena).
            stops=stops,
        )
        results.append(decision_result(con, agent.name, s, bar_ts, new, attempt=attempt_id))
    regimes = {s: a.regime for s, a in answers.items()}
    return (
        _outcome(
            agent,
            "ASKED",
            attempt_id=attempt_id,
            usage=reply.usage,
            cost_eur=cost,
            regimes=regimes,
        ),
        results,
    )


def run_llm_depots(
    con: sqlite3.Connection,
    agents: Sequence[LlmAgent],
    settings: LlmSettings,
    model: SignalModel,
    snaps: Mapping[Symbol, MarketSnapshot],
    costs: Costs,
    *,
    refresh_quotes: Callable[[], Quotes | None],
    clock: Callable[[], float] = time.time,
    stops: list[RiskStop] | None = None,
) -> list[tuple[LlmOutcome, list[MarketResult]]]:
    """All GPT depots of one cycle; one depot's failure never blocks another (ADR-0012)."""
    now_epoch = clock()
    done: dict[str, tuple[LlmOutcome, list[MarketResult]]] = {}
    prepared: list[_Prepared] = []
    for agent in agents:
        try:
            ready = _prepare(con, agent, settings, snaps, costs, now_epoch)
        except Exception as exc:
            done[agent.name] = (_outcome(agent, "ERROR", type(exc).__name__), [])
            continue
        if isinstance(ready, LlmOutcome):
            done[agent.name] = (ready, [])
        else:
            prepared.append(ready)
    if prepared:
        with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
            for future in [pool.submit(_ask, p, settings, model, clock) for p in prepared]:
                future.result()  # _ask stores its own errors; this only waits
        # The answers may take a while: trade only on quotes fetched after them (Arena recheck).
        # A failing refresh must not lose the billing of answers that were already paid for.
        try:
            fresh = refresh_quotes()
        except Exception:
            fresh = None
        for p in prepared:
            try:
                done[p.agent.name] = _finish(con, p, fresh, costs, clock, stops)
            except Exception as exc:
                done[p.agent.name] = (_outcome(p.agent, "ERROR", type(exc).__name__), [])
    return [done[a.name] for a in agents]
