# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - quant_cycle
# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - STRATEGY_TIMEFRAME
"""One live paper cycle: quotes, buy & hold, one decision per new closed bar, equity points.

ADR-0004: virtual orders only. ADR-0008: one active agent plus the buy & hold benchmark.
"""

import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from llm_trading_bot.market.models import SYMBOLS, Symbol
from llm_trading_bot.market.snapshot import DATA_ERRORS, MarketClient, build_snapshots
from llm_trading_bot.paper.db import balance, write_tx
from llm_trading_bot.paper.execution import (
    BUYHOLD,
    Costs,
    current_weight,
    initialize_buyhold,
    latest_quotes,
    mark,
    record_decision,
    record_quotes,
    value,
)
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import cost_roundtrip, feature_payload
from llm_trading_bot.strategy.signals import run_strategy

# A decision is taken once per closed bar of the strategy's own timeframe (minutes).
STRATEGY_TIMEFRAME: Mapping[str, int] = {"trend": 60, "breakout": 15, "meanrev": 15, "ensemble": 15}
DEFAULT_AGENT = "trend"


def active_agent(env: Mapping[str, str]) -> str:
    """BOT_AGENT selects the active strategy; the LLM agent (M1 3a) will join this list."""
    agent = env.get("BOT_AGENT", DEFAULT_AGENT).strip() or DEFAULT_AGENT
    if agent not in STRATEGY_TIMEFRAME:
        raise ValueError(f"BOT_AGENT must be one of {sorted(STRATEGY_TIMEFRAME)}: {agent!r}")
    return agent


def iso(epoch: float) -> str:
    """Same text form as the Arena's utcnow()."""
    return datetime.fromtimestamp(int(epoch), UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class MarketResult:
    symbol: Symbol
    bar_ts: int | None = None
    new: bool = False
    action: str = ""
    target: float = 0.0
    status: str = ""
    traded: bool = False
    reason: str = ""
    error: str = ""


@dataclass(frozen=True)
class Equity:
    agent: str
    value: float
    cash: float
    btc: float
    eth: float


@dataclass
class CycleReport:
    at: int
    agent: str
    quotes_error: str = ""
    buyhold_bought: int = 0
    markets: list[MarketResult] = field(default_factory=list)
    equity: list[Equity] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.quotes_error and all(not m.error for m in self.markets)


def _result(
    con: sqlite3.Connection, agent: str, symbol: Symbol, bar_ts: int, new: bool
) -> MarketResult:
    row = con.execute(
        "SELECT d.action, d.target, d.status, d.reason,"
        " (SELECT COUNT(*) FROM trades t WHERE t.decision_id = d.id) AS n"
        " FROM decisions d WHERE agent=? AND symbol=? AND bar_ts=?",
        (agent, symbol.name, bar_ts),
    ).fetchone()
    return MarketResult(
        symbol,
        bar_ts,
        new,
        row["action"],
        row["target"],
        row["status"],
        row["n"] > 0,
        row["reason"],
    )


def paper_cycle(
    con: sqlite3.Connection,
    client: MarketClient,
    agent: str,
    *,
    costs: Costs,
    clock: Callable[[], float] = time.time,
) -> CycleReport:
    """Run one cycle. Precondition: the caller holds the WriterLock of this database.

    Fails closed like the Arena's quant_cycle: without fresh quotes of both markets no decision
    is taken at all, so the bar stays open and is decided once quotes are back. A market whose
    candles fail is reported and skipped; it never blocks the other market.
    """
    at = int(clock())
    now = iso(at)
    report = CycleReport(at, agent)

    try:
        record_quotes(con, client.quotes(), now=now)
    except DATA_ERRORS as exc:
        report.quotes_error = f"{type(exc).__name__}: {exc}"
    qs = latest_quotes(con, now_epoch=clock())
    if qs is None:
        report.quotes_error = report.quotes_error or "stored quotes missing or stale"
        return report
    report.buyhold_bought = initialize_buyhold(con, qs, costs, when=now)

    errors: dict[Symbol, str] = {}

    def on_error(symbol: Symbol, exc: Exception) -> None:
        errors[symbol] = f"{type(exc).__name__}: {exc}"

    snaps = build_snapshots(client, clock=clock, on_error=on_error)
    tf = STRATEGY_TIMEFRAME[agent]
    for symbol in SYMBOLS:
        snap = snaps.get(symbol)
        if snap is None:
            report.markets.append(MarketResult(symbol, error=errors.get(symbol, "no snapshot")))
            continue
        bar_ts = snap.bars[tf][-1].ts
        exists = con.execute(
            "SELECT 1 FROM decisions WHERE agent=? AND symbol=? AND bar_ts=?",
            (agent, symbol.name, bar_ts),
        ).fetchone()
        p = balance(con, agent)
        if exists or p["halted"]:
            report.markets.append(
                _result(con, agent, symbol, bar_ts, False)
                if exists
                else MarketResult(symbol, bar_ts, error="portfolio halted")
            )
            continue
        weight = current_weight(p, symbol, qs)
        ind = indicators(
            snap, cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage)
        )
        sig = run_strategy(agent, snap, ind, weight)
        context = feature_payload(snap, ind, weight, costs.fee_rate, costs.slippage)
        new = record_decision(
            con, agent, symbol, bar_ts, agent.upper(), sig, context, costs=costs, now=now, qs=qs
        )
        report.markets.append(_result(con, agent, symbol, bar_ts, new))

    with write_tx(con):
        for name in (agent, BUYHOLD):
            mark(con, name, qs, now)
    for name in (agent, BUYHOLD):
        p = balance(con, name)
        report.equity.append(Equity(name, value(p, qs), p["cash"], p["btc"], p["eth"]))
    return report
