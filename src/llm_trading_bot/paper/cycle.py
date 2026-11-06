# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - quant_cycle, update_market
# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - STRATEGY_TIMEFRAME
"""One live paper cycle: quotes, buy & hold, classic and GPT depots, equity points.

ADR-0004: virtual orders only. ADR-0012: GPT depots (luna trades, sol cross-checks) plus buy & hold;
classic depots are optional. All depots of a cycle see the same snapshot.
"""

import sqlite3
import time
from collections.abc import Callable, Mapping

from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.llm.cycle import run_llm_depots
from llm_trading_bot.llm.due import iso
from llm_trading_bot.llm.openai_client import SignalModel
from llm_trading_bot.market.models import SYMBOLS, Symbol
from llm_trading_bot.market.snapshot import (
    DATA_ERRORS,
    MarketClient,
    MarketSnapshot,
    build_snapshots,
)
from llm_trading_bot.paper.db import balance, write_tx
from llm_trading_bot.paper.execution import (
    BUYHOLD,
    Costs,
    Quotes,
    current_weight,
    initialize_buyhold,
    latest_quotes,
    mark,
    record_bar,
    record_decision,
    record_quotes,
    value,
)
from llm_trading_bot.paper.report import (
    CycleReport,
    Equity,
    MarketResult,
    decision_result,
)
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload
from llm_trading_bot.strategy.signals import run_strategy

# A classic decision is taken once per closed bar of the strategy's own timeframe (minutes).
STRATEGY_TIMEFRAME: Mapping[str, int] = {"trend": 60, "breakout": 15, "meanrev": 15, "ensemble": 15}


def _decide(
    con: sqlite3.Connection,
    snap: MarketSnapshot,
    agent: str,
    qs: Quotes,
    costs: Costs,
    now: str,
) -> MarketResult:
    """At most one decision per new closed bar of the agent's own timeframe."""
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
        return MarketResult(agent, symbol, bar_ts, error="portfolio halted")
    weight = current_weight(p, symbol, qs)
    ind = indicators(snap, cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage))
    sig = run_strategy(agent, snap, ind, weight)
    context = feature_payload(snap, ind, weight, costs.fee_rate, costs.slippage)
    new = record_decision(
        con, agent, symbol, bar_ts, agent.upper(), sig, context, costs=costs, now=now, qs=qs
    )
    return decision_result(con, agent, symbol, bar_ts, new)


def _store_quotes(
    con: sqlite3.Connection, client: MarketClient, clock: Callable[[], float]
) -> tuple[Quotes | None, str]:
    error = ""
    try:
        record_quotes(con, client.quotes(), now=iso(clock()))
    except DATA_ERRORS as exc:
        error = f"{type(exc).__name__}: {exc}"
    return latest_quotes(con, now_epoch=clock()), error


def paper_cycle(
    con: sqlite3.Connection,
    client: MarketClient,
    config: BotConfig,
    *,
    costs: Costs,
    model: SignalModel | None = None,
    clock: Callable[[], float] = time.time,
) -> CycleReport:
    """Run one cycle. Precondition: the caller holds the WriterLock of this database.

    Fails closed like the Arena's quant_cycle: without fresh quotes of both markets no decision
    is taken at all, so the bar stays open. A failing market or depot is reported and skipped;
    it never blocks another market or depot.
    """
    at = int(clock())
    now = iso(at)
    report = CycleReport(at, config.agents)

    qs, report.quotes_error = _store_quotes(con, client, clock)
    if qs is None:
        report.quotes_error = report.quotes_error or "stored quotes missing or stale"
        return report
    report.buyhold_bought = initialize_buyhold(con, qs, costs, when=now)

    errors: dict[Symbol, str] = {}

    def on_error(symbol: Symbol, exc: Exception) -> None:
        errors[symbol] = f"{type(exc).__name__}: {exc}"

    snaps = build_snapshots(client, clock=clock, on_error=on_error)
    for fresh_snap in snaps.values():
        last_minute = fresh_snap.bars[1][-1]
        record_bar(con, fresh_snap.symbol, bar_ts=last_minute.ts, last_close=last_minute.close)

    for agent in config.classic:
        for symbol in SYMBOLS:
            snap = snaps.get(symbol)
            if snap is None:
                report.markets.append(
                    MarketResult(agent, symbol, error=errors.get(symbol, "no snapshot"))
                )
                continue
            try:
                report.markets.append(_decide(con, snap, agent, qs, costs, now))
            except Exception as exc:
                # CLAUDE.md: a BTC failure never blocks ETH. record_decision rolled back its own
                # transaction; the error stays visible in the report and the exit code.
                report.markets.append(
                    MarketResult(agent, symbol, error=f"{type(exc).__name__}: {exc}")
                )

    if config.llm is not None:
        if model is None:
            raise ValueError("GPT depots configured but no model client given")

        def refresh() -> Quotes | None:
            return _store_quotes(con, client, clock)[0]

        for outcome, results in run_llm_depots(
            con,
            config.llm.agents,
            config.llm,
            model,
            snaps,
            costs,
            refresh_quotes=refresh,
            clock=clock,
        ):
            report.llm.append(outcome)
            report.markets.extend(results)

    names = (*config.agents, BUYHOLD)
    latest = latest_quotes(con, now_epoch=clock()) or qs
    with write_tx(con):
        for name in names:
            mark(con, name, latest, now)
    for name in names:
        p = balance(con, name)
        report.equity.append(Equity(name, value(p, latest), p["cash"], p["btc"], p["eth"]))
    return report
