# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - quant_cycle, update_market
# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - STRATEGY_TIMEFRAME
"""One live paper cycle: quotes, buy & hold, classic and GPT depots, equity points.

ADR-0004: virtual orders only. ADR-0012: GPT depots (luna trades, sol cross-checks) plus buy & hold;
classic depots are optional. All depots of a cycle see the same snapshot.
"""

import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.llm.cycle import run_llm_depots
from llm_trading_bot.llm.due import iso
from llm_trading_bot.llm.openai_client import SignalModel
from llm_trading_bot.market.models import SYMBOLS, Candle, Quote, QuoteBook, Symbol
from llm_trading_bot.market.snapshot import (
    DATA_ERRORS,
    MarketClient,
    MarketSnapshot,
    build_snapshots,
    error_code,
)
from llm_trading_bot.paper.db import balance, write_tx
from llm_trading_bot.paper.execution import (
    BUYHOLD,
    QUOTE_MAX_AGE,
    Costs,
    Quotes,
    RiskStop,
    current_weight,
    fresh_quotes,
    initialize_buyhold,
    mark,
    record_bar,
    record_decision,
    record_quotes,
    unquoted_holdings,
    value,
)
from llm_trading_bot.paper.observations import ObservationBuffer, note
from llm_trading_bot.paper.report import (
    CycleReport,
    Equity,
    LlmOutcome,
    MarketResult,
    decision_result,
)
from llm_trading_bot.paper.risk import NOT_STOPPED, Controls, release
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload
from llm_trading_bot.strategy.signals import run_strategy

# A classic decision is taken once per closed bar of the strategy's own timeframe (minutes).
STRATEGY_TIMEFRAME: Mapping[str, int] = {"trend": 60, "breakout": 15, "meanrev": 15, "ensemble": 15}


def quote_problem(p: Mapping[str, Any], symbol: Symbol, qs: Quotes) -> str:
    """Why ``symbol`` cannot be decided on ``qs`` (Spec 011 AK7), or "".

    A market without a fresh quote gets no decision, so its bar stays open (Spec 004). A held
    market without one also blocks the other market: the depot's value is unknown then.
    """
    if symbol not in qs:
        return f"MarketDataError: {symbol.market}: no fresh quote - no decision"
    held = unquoted_holdings(p, qs)
    if held:
        markets = ", ".join(s.market for s in held)
        return f"MarketDataError: {markets}: no fresh quote for the held position"
    return ""


def _decide(
    con: sqlite3.Connection,
    snap: MarketSnapshot,
    agent: str,
    qs: Quotes,
    costs: Costs,
    now: str,
    stops: list[RiskStop],
    paused: bool = False,
    observations: ObservationBuffer | None = None,
) -> MarketResult:
    """At most one decision per new closed bar of the agent's own timeframe.

    While trading is paused (ADR-0021) the decision is stored with status PAUSED and not traded,
    so the log shows what the strategy would have done.
    """
    symbol = snap.symbol
    bar_ts = snap.bars[STRATEGY_TIMEFRAME[agent]][-1].ts
    exists = con.execute(
        "SELECT 1 FROM decisions WHERE agent=? AND symbol=? AND bar_ts=?",
        (agent, symbol.name, bar_ts),
    ).fetchone()
    if exists:
        return decision_result(con, agent, symbol, bar_ts, False)
    p = balance(con, agent)
    if p["halted"]:
        # Arena quant_cycle: a stopped depot takes no decision. ADR-0021: not an error.
        return MarketResult(agent, symbol, bar_ts, status="HALTED", reason="drawdown stop")
    problem = quote_problem(p, symbol, qs)
    if problem:
        return MarketResult(agent, symbol, bar_ts, error=problem)
    weight = current_weight(p, symbol, qs)
    ind = indicators(snap, cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage))
    sig = run_strategy(agent, snap, ind, weight)
    context = feature_payload(snap, ind, weight, costs.fee_rate, costs.slippage)
    new = record_decision(
        con,
        agent,
        symbol,
        bar_ts,
        agent.upper(),
        sig,
        context,
        costs=costs,
        now=now,
        qs=qs,
        status="PAUSED" if paused else "OK",
        stops=stops,
        partial=True,
    )
    if new:
        note(
            observations,
            lambda b: b.decision(agent, symbol, bar_ts, now, sig.strategy, sig.version, ind),
        )
    return decision_result(con, agent, symbol, bar_ts, new)


@dataclass
class _Feed:
    """Quotes of one cycle: fetched, stored per market and fetched again once they are older
    than QUOTE_MAX_AGE (Spec 011 AK8), so nothing is traded or valued on old prices."""

    con: sqlite3.Connection
    client: MarketClient
    clock: Callable[[], float]
    observations: ObservationBuffer | None = None
    fetched_at: float = float("-inf")
    errors: list[str] = field(default_factory=list)
    failure: Exception | None = None
    """The exception of the last failed fetch; observed only (Spec 015 AK2)."""
    book: QuoteBook | None = None
    """The book of the last fetch that returned one; observed only."""

    def fetch(self, purpose: str = "start") -> set[Symbol]:
        """Fetch and store; the markets stored by THIS fetch (a failed one stores none)."""
        # Whole seconds, like the stored stamps (iso): the stored age is never above
        # clock() - fetched_at, so current() fetches again before fresh_quotes drops a quote.
        self.fetched_at = float(int(self.clock()))
        try:
            book = self.client.quotes()
        except DATA_ERRORS as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            self.failure, self.book = exc, None
            return set()
        self.failure, self.book = None, book
        # One clock reading for the stored and the observed stamp: as many readings as before.
        arrived = self.clock()
        note(self.observations, lambda b: b.book(purpose, arrived, book))
        stored = set(record_quotes(self.con, book, now=iso(arrived)))
        if problem := book.problem():
            self.errors.append(f"MarketDataError: {problem}")
        return stored

    def current(self) -> dict[Symbol, Quote]:
        now = self.clock()
        if now - self.fetched_at > QUOTE_MAX_AGE:
            self.fetch("recheck")
            now = self.clock()
        return fresh_quotes(self.con, now_epoch=now)


def paper_cycle(
    con: sqlite3.Connection,
    client: MarketClient,
    config: BotConfig,
    *,
    costs: Costs,
    model: SignalModel | None = None,
    clock: Callable[[], float] = time.time,
    stop_requested: Callable[[], bool] = lambda: False,
    controls: Controls | None = None,
    observations: ObservationBuffer | None = None,
) -> CycleReport:
    """Run one cycle. Precondition: the caller holds the WriterLock of this database.

    ``stop_requested`` is asked before the GPT step: while the service is stopping, no new paid
    request is started, but the depots are still valued (Spec 008 AK5).

    Fails closed per market (Spec 011 AK7, ADR-0021): a market without a fresh quote gets no
    decision, so its bar stays open; a depot holding that market decides in neither market and
    gets no equity point. A failing market or depot is reported and skipped; it never blocks
    another market or depot.

    ``controls`` (Spec 011, ADR-0021): release requests are applied first; while trading is
    paused nothing is bought or sold except by the drawdown stop, which keeps running.

    ``observations`` (Spec 015, ADR-0027) only collects what the round saw, in memory; the
    caller writes it after the round. Nothing traded depends on it, and its failures are lost.
    """
    at = int(clock())
    now = iso(at)
    obs = observations
    note(obs, lambda b: b.begin(at))
    controls = controls or Controls()
    report = CycleReport(at, config.agents, paused=controls.paused)
    feed = _Feed(con, client, clock, obs)
    # Also depots switched off meanwhile: the web offers the release for every stopped depot,
    # and a refusal must be reported, not skipped (Spec 011 AK5).
    for agent, requested_at in controls.resumes.items():
        why = release(con, agent, requested_at, clock())
        if not why:
            report.released.append(agent)
        elif why != NOT_STOPPED:
            report.refused.append((agent, why))

    feed.fetch()
    qs = fresh_quotes(con, now_epoch=clock())
    report.quotes_error = feed.errors[0] if feed.errors else ""
    if not qs:
        report.quotes_error = report.quotes_error or "stored quotes missing or stale"
        note(obs, lambda b: _observe_no_quotes(b, report.quotes_error, feed))
        return report
    if set(qs) == set(SYMBOLS) and not controls.paused:
        # Arena: the benchmark buys only on fresh quotes of both markets.
        report.buyhold_bought = initialize_buyhold(con, qs, costs, when=now)

    errors: dict[Symbol, str] = {}
    failures: dict[Symbol, Exception] = {}

    def on_error(symbol: Symbol, exc: Exception) -> None:
        errors[symbol] = f"{type(exc).__name__}: {exc}"
        failures[symbol] = exc

    def on_window(symbol: Symbol, tf: int, rows: tuple[Candle, ...], at: int) -> None:
        note(obs, lambda b: b.window(symbol, tf, at, rows))

    def on_book(book: QuoteBook, at: int) -> None:
        note(obs, lambda b: b.book("snapshot", at, book))

    snaps = build_snapshots(
        client,
        clock=clock,
        on_error=on_error,
        on_window=None if obs is None else on_window,
        on_book=None if obs is None else on_book,
    )
    try:
        for fresh_snap in snaps.values():
            last_minute = fresh_snap.bars[1][-1]
            record_bar(con, fresh_snap.symbol, bar_ts=last_minute.ts, last_close=last_minute.close)

        # One quote set for every classic depot of the round, no older than QUOTE_MAX_AGE.
        classic_qs = feed.current() if config.classic else {}
        for agent in config.classic:
            for symbol in SYMBOLS:
                snap = snaps.get(symbol)
                if snap is None:
                    report.markets.append(
                        MarketResult(agent, symbol, error=errors.get(symbol, "no snapshot"))
                    )
                    continue
                try:
                    report.markets.append(
                        _decide(
                            con,
                            snap,
                            agent,
                            classic_qs,
                            costs,
                            now,
                            report.risk_stops,
                            controls.paused,
                            obs,
                        )
                    )
                except Exception as exc:
                    # CLAUDE.md: a BTC failure never blocks ETH. record_decision rolled back its own
                    # transaction; the error stays visible in the report and the exit code.
                    report.markets.append(
                        MarketResult(agent, symbol, error=f"{type(exc).__name__}: {exc}")
                    )

        if config.llm is not None and stop_requested():
            report.llm.extend(
                LlmOutcome(a.name, a.model, "STOPPING", "service is stopping")
                for a in config.llm.agents
            )
        elif config.llm is not None:
            if model is None:
                raise ValueError("GPT depots configured but no model client given")

            def refresh() -> Quotes | None:
                # Fail closed: only markets stored by THIS fetch count. Falling back to quotes
                # stored before the request would compare two prices from the same moment in the
                # drift check and trade at a price from before the answer (Spec 005 AK6).
                stored = feed.fetch("after_llm")
                return {
                    s: q for s, q in fresh_quotes(con, now_epoch=clock()).items() if s in stored
                }

            for outcome, results in run_llm_depots(
                con,
                config.llm.agents,
                config.llm,
                model,
                snaps,
                costs,
                refresh_quotes=refresh,
                clock=clock,
                stops=report.risk_stops,
                observations=obs,
                paused=controls.paused,
            ):
                report.llm.append(outcome)
                report.markets.extend(results)

        names = (*config.agents, BUYHOLD)
        # Spec 011 AK8: the final valuation never uses quotes older than QUOTE_MAX_AGE.
        latest = feed.current()
        valued = []
        with write_tx(con):
            for name in names:
                p = balance(con, name)
                if held := unquoted_holdings(p, latest):
                    report.unvalued.extend(
                        MarketResult(
                            name,
                            s,
                            error=f"MarketDataError: {s.market}: no fresh quote for the held"
                            " position - no equity point",
                        )
                        for s in held
                    )
                    continue
                if (stop := mark(con, name, latest, now, costs)) is not None:
                    report.risk_stops.append(stop)
                valued.append(name)
        for name in valued:
            p = balance(con, name)
            report.equity.append(Equity(name, value(p, latest), p["cash"], p["btc"], p["eth"]))
    finally:
        # Spec 015 AK4: also when the round fails after the snapshots (e.g. in the
        # valuation), with the decisions known so far; guarded as a whole, so an observing
        # bug can neither undo the round nor hide its error.
        note(obs, lambda b: _observe_snapshots(b, snaps, failures, report))
    return report


def _observe_no_quotes(obs: ObservationBuffer, message: str, feed: _Feed) -> None:
    """No fresh stored quote at the start: no market got as far as a snapshot.

    The code says why: the failed start fetch (``http``, ``parse``), else the code the book
    gave each market (``spread``, ``parse``, ``conflict``), else ``quote_missing``.
    """
    codes: Mapping[Symbol, str] = getattr(feed.book, "codes", None) or {}
    for symbol in SYMBOLS:
        if feed.failure is not None:
            status = error_code(feed.failure)
        else:
            status = codes.get(symbol, "quote_missing")
        _observe_snapshot(obs, symbol, status, message, False)


def _observe_snapshot(
    obs: ObservationBuffer, symbol: Symbol, status: str, message: str | None, decided: bool
) -> None:
    note(obs, lambda b: b.snapshot(symbol, status, message, decided))


def _observe_snapshots(
    obs: ObservationBuffer,
    snaps: Mapping[Symbol, MarketSnapshot],
    failures: Mapping[Symbol, Exception],
    report: CycleReport,
) -> None:
    """Per market: the snapshot's outcome as a code, and whether any depot decided on it."""
    for symbol in SYMBOLS:
        decided = any(m.symbol is symbol and m.new for m in report.markets)
        exc = failures.get(symbol)
        if symbol in snaps:
            status, message = "ok", None
        elif exc is None:
            # Not reported as failed: the round ended before this market got a snapshot.
            status, message = "other", "no snapshot"
        else:
            status, message = error_code(exc), f"{type(exc).__name__}: {exc}"
        _observe_snapshot(obs, symbol, status, message, decided)
