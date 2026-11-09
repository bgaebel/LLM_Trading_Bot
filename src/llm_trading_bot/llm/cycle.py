# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - llm_cycle
"""The GPT depots of one paper cycle (ADR-0012, ADR-0013).

1. Per depot, in order: fresh quotes? due? hourly cap? -> payload -> reservation (committed).
2. All reserved requests run at the same time with the same snapshot, so a slow depot never delays
   another one (AK7).
3. Per depot, in order: settlement -> strict check -> fresh-quote recheck per market -> decision.

Fails closed at every step: nothing is traded on an invalid, refused, incomplete or stale answer.

Spec 022 (ADR-0032): a GPT USDC depot (``gpt-luna-usdc``, ``gpt-sol-usdc``) gets the same prompt;
its BTC is priced with the costs of the USDC route in the existing cost fields, needs the route's
quotes and is traded and valued over the route (``paper.usdc``, by the depot's name).
"""

import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
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
    MarketAnswer,
    ordered,
    parse_answer,
    prompt_sha256,
    response_schema,
    user_message,
)
from llm_trading_bot.llm.due import Cadence, due_reason, hourly_limit_reached, iso, trigger_state
from llm_trading_bot.llm.openai_client import API_ERRORS, MAX_RETRIES, ModelReply, SignalModel
from llm_trading_bot.market.models import QUOTED, SYMBOLS, Symbol
from llm_trading_bot.market.snapshot import MarketSnapshot
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.db import balance
from llm_trading_bot.paper.execution import (
    Costs,
    Quotes,
    RiskStop,
    covers,
    current_weight,
    fresh_quotes,
    record_decision,
)
from llm_trading_bot.paper.observations import ObservationBuffer, note
from llm_trading_bot.paper.report import LlmOutcome, MarketResult, decision_result
from llm_trading_bot.strategy.indicators import Indicators, indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload
from llm_trading_bot.strategy.signals import run_strategy

# Arena llm_cycle: an answer is only acted on while its snapshot and the price are still current.
MAX_SNAPSHOT_AGE = 120
MAX_FUTURE_SKEW = 10
MAX_MID_DRIFT = 0.02
# The classic strategies whose verdict is part of the payload (strategy/payload.py).
REFERENCE_STRATEGIES = ("trend", "breakout", "meanrev")
REFUSAL_MARK = "[refusal] "
"""Prefix of ``llm_attempts.raw_text`` when the model refused without output text (Spec 015)."""
USDC_SIGNAL_VERSION = f"{PROMPT_VERSION}{usdc.USDC_SUFFIX}"
"""Spec 022: the version of a GPT USDC depot's decisions - the same prompt (``PROMPT_VERSION``,
same hash), another execution (ADR-0029), like ``v0.3.1-usdc`` for the classic rules."""


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
    return _triggers(
        snaps,
        {
            s: indicators(
                snap, cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage)
            )
            for s, snap in snaps.items()
        },
    )


def _triggers(
    snaps: Mapping[Symbol, MarketSnapshot], inds: Mapping[Symbol, Indicators]
) -> dict[Symbol, dict[str, str]]:
    """``market_triggers`` on given indicators: those of the payload, so a GPT USDC depot's
    cadence sees its reference signals with the route's costs, as its prompt does (Spec 022)."""
    triggers: dict[Symbol, dict[str, str]] = {}
    for s, snap in snaps.items():
        ind = inds[s]
        triggers[s] = {n: run_strategy(n, snap, ind, 0.0).action for n in REFERENCE_STRATEGIES}
    return triggers


def _costs_of(
    agent: str, symbol: Symbol, snap: MarketSnapshot, qs: Quotes, costs: Costs
) -> tuple[float, float, float]:
    """Roundtrip cost, fee and slippage per side the depot's prompt sees for ``symbol``.

    Spec 022: for the BTC of a GPT USDC depot those of the route (both legs, both spreads), in
    the same fields the prompt always had - the classic USDC depots decide on exactly these.
    """
    if usdc.routed(agent, symbol):
        return usdc.roundtrip_cost(qs), usdc.ROUTE_FEE, usdc.ROUTE_SLIPPAGE
    roundtrip = cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage)
    return roundtrip, costs.fee_rate, costs.slippage


def own_quotes(agent: str, qs: Quotes | None) -> Quotes | None:
    """The quotes ``agent`` trades and values on: a euro depot never sees the quote-only markets
    (so it runs exactly as before Spec 022); a USDC depot keeps its route's books."""
    if qs is None or usdc.is_usdc(agent):
        return qs
    return {s: q for s, q in qs.items() if s in SYMBOLS}


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
    trigger: str = ""
    """Why the depot is asked (llm.due.DUE_REASONS); observed only (Spec 015 AK4)."""
    trigger_detail: dict[str, object] = field(default_factory=dict)
    indicators: dict[Symbol, Indicators] = field(default_factory=dict)
    """Unrounded indicators behind each payload; observed only."""


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
    paused: bool = False,
    observations: ObservationBuffer | None = None,
) -> _Prepared | LlmOutcome:
    p = balance(con, agent.name)
    if p["halted"]:
        return _outcome(agent, "HALTED", "portfolio halted")
    if paused:
        # ADR-0021: no paid request while nothing may be traded.
        return _outcome(agent, "PAUSED", "trading paused")
    current = _usable(snaps, now_epoch)
    if not current:
        return _outcome(agent, "NO_MARKETS", "no current snapshot")
    # Arena llm_cycle: no request without fresh quotes, checked per depot. Spec 011 AK7: per
    # market - the request covers the markets with a fresh quote, and a held market without
    # one blocks the request (the depot's value and weights would be unknown).
    # Spec 022: the BTC of a GPT USDC depot needs the route's books instead of BTC-EUR's.
    books = QUOTED if usdc.is_usdc(agent.name) else SYMBOLS
    qs = fresh_quotes(con, now_epoch=now_epoch, markets=books)
    usable = {
        s: snap
        for s, snap in current.items()
        if all(m in qs for m in usdc.route_markets(agent.name, s))
    }
    if not usable or not covers(p, qs):
        return _outcome(agent, "NO_QUOTES", "stored quotes missing or stale")
    payloads: dict[Symbol, dict[str, Any]] = {}
    inds: dict[Symbol, Indicators] = {}
    for s, snap in usable.items():
        roundtrip, fee_rate, slippage = _costs_of(agent.name, s, snap, qs, costs)
        inds[s] = indicators(snap, roundtrip)
        payloads[s] = feature_payload(
            snap,
            inds[s],
            current_weight(p, s, qs),
            fee_rate,
            slippage,
        )
    triggers = _triggers(usable, inds)
    mids = {s: snap.quote.mid for s, snap in usable.items()}
    trigger, trigger_detail = due_reason(
        con, agent.name, mids, triggers, now_epoch, settings.cadence
    )
    if trigger is None:
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
        _observe_blocked(observations, exc, agent, trigger, trigger_detail, settings, now_epoch)
        return _outcome(agent, "BUDGET_BLOCKED", str(exc))
    assert agent.pricing is not None  # reserve() refuses an attempt without prices
    return _Prepared(
        agent,
        attempt_id,
        agent.pricing,
        attempt,
        usable,
        payloads,
        schema,
        trigger=trigger,
        trigger_detail=trigger_detail,
        indicators=inds,
    )


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
    observations: ObservationBuffer | None = None,
    cadence: Cadence | None = None,
) -> tuple[LlmOutcome, list[MarketResult]]:
    agent, attempt_id, pricing = prepared.agent, prepared.attempt_id, prepared.pricing
    if prepared.error is not None or prepared.reply is None:
        _observe_attempt(observations, prepared, cadence, None, None)
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
    # Spec 015 AK4: the text as it came, before parsing - also when it turns out invalid. A
    # refusal without text keeps the refusal, marked, so it is not mistaken for an answer.
    raw_text = reply.text
    if not raw_text and reply.refusal:
        raw_text = f"{REFUSAL_MARK}{reply.refusal}"
    markets = ordered(list(prepared.usable))
    problem = reply.problem()
    answers = None
    if not problem:
        try:
            answers = parse_answer(reply.text or "", markets, strategy=agent.name)
        except InvalidSignal as exc:
            problem = f"InvalidSignal: {exc}"
    _observe_attempt(observations, prepared, cadence, raw_text, answers)
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
    fresh = own_quotes(agent.name, fresh)
    if usdc.is_usdc(agent.name):
        answers = {
            s: replace(a, signal=replace(a.signal, version=USDC_SIGNAL_VERSION))
            for s, a in answers.items()
        }
    results = []
    for s, answer in answers.items():
        snap = prepared.usable[s]
        # Spec 011 AK7: a quote lost after the answer costs only its own market. Spec 022: the
        # drift is measured on the market the signal came from (BTC-EUR); a routed BTC also
        # needs the route's books.
        stale = (
            fresh is None
            or s not in fresh
            or any(m not in fresh for m in usdc.route_markets(agent.name, s))
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
            # Without a quote of a held market record_decision stores STALE_QUOTE itself.
            partial=True,
        )
        if new:
            _observe_decision(
                observations, agent.name, s, bar_ts, iso(decided_at), answer, prepared
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


def _observe_attempt(
    obs: ObservationBuffer | None,
    prepared: _Prepared,
    cadence: Cadence | None,
    raw_text: str | None,
    answers: Mapping[Symbol, MarketAnswer] | None,
) -> None:
    """One GPT attempt for the observations: trigger, cadence, raw text, targets as answered."""
    if obs is None:
        return

    def record(b: ObservationBuffer) -> None:
        # The targets as the model gave them: record_decision clamps them to the current weight.
        targets = {
            s.name: {
                "action": a.signal.action,
                "target_fraction": a.signal.target_fraction,
                "confidence": a.signal.confidence,
                "market_regime": a.regime,
            }
            for s, a in (answers or {}).items()
        }
        b.llm_attempt(
            iso(prepared.answered_at),
            prepared.attempt_id,
            prepared.agent.name,
            prepared.trigger,
            prepared.trigger_detail,
            {} if cadence is None else asdict(cadence),
            raw_text,
            targets,
        )

    note(obs, record)


def _observe_blocked(
    obs: ObservationBuffer | None,
    exc: BudgetBlocked,
    agent: LlmAgent,
    trigger: str,
    trigger_detail: Mapping[str, object],
    settings: LlmSettings,
    now_epoch: float,
) -> None:
    """A BUDGET_BLOCKED attempt: due and stored in bot.db, but never sent - no text, no targets."""
    call_id = getattr(exc, "llm_call_id", None)
    if obs is None or call_id is None:  # llm_call_id is NOT NULL: no row without it
        return
    note(
        obs,
        lambda b: b.llm_attempt(
            iso(now_epoch),
            call_id,
            agent.name,
            trigger,
            trigger_detail,
            asdict(settings.cadence),
            None,
            {},
        ),
    )


def _observe_decision(
    obs: ObservationBuffer | None,
    agent: str,
    symbol: Symbol,
    bar_ts: int,
    at: str,
    answer: MarketAnswer,
    prepared: _Prepared,
) -> None:
    note(
        obs,
        lambda b: b.decision(
            agent,
            symbol,
            bar_ts,
            at,
            answer.signal.strategy,
            answer.signal.version,
            prepared.indicators[symbol],
        ),
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
    observations: ObservationBuffer | None = None,
    paused: bool = False,
) -> list[tuple[LlmOutcome, list[MarketResult]]]:
    """All GPT depots of one cycle; one depot's failure never blocks another (ADR-0012).

    ``observations`` (Spec 015) only collects each attempt and decision in memory.
    """
    now_epoch = clock()
    done: dict[str, tuple[LlmOutcome, list[MarketResult]]] = {}
    prepared: list[_Prepared] = []
    for agent in agents:
        try:
            ready = _prepare(con, agent, settings, snaps, costs, now_epoch, paused, observations)
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
                done[p.agent.name] = _finish(
                    con, p, fresh, costs, clock, stops, observations, settings.cadence
                )
            except Exception as exc:
                done[p.agent.name] = (_outcome(p.agent, "ERROR", type(exc).__name__), [])
    return [done[a.name] for a in agents]
