# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - fee_rate, slip_rate, value,
#   current_weight, trade_to_target, mark_and_risk (drawdown log row -> RiskStop),
#   record_live_quotes (without ticks and risk), latest_quotes
# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - update_market (live branch)
# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - record_decision,
#   initialize_buyhold
"""Virtual order execution, identical to Arena v0.3.1 (ADR-0007, golden fixtures).

ADR-0004: nothing here talks to an exchange; a "trade" is a row in the paper database. The SQL
arithmetic is kept as in the Arena on purpose: the golden digests compare results bit for bit.
"""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from llm_trading_bot.market.models import SYMBOLS, MarketDataError, Quote, Symbol
from llm_trading_bot.paper.db import balance, write_tx
from llm_trading_bot.strategy.payload import ARENA_SLIPPAGE, ARENA_TAKER_FEE
from llm_trading_bot.strategy.signals import Signal

MAX_SYMBOL_WEIGHT = 0.45
MAX_TOTAL_WEIGHT = 0.90
MIN_TRADE_EUR = 50.0
# Moves smaller than this share of equity are rebalancing noise and not worth the fees.
REBALANCE_BAND = 0.02
MAX_COST_RATE = 0.05
REASON_MAX = 550
BUYHOLD = "buyhold"
# Older quotes no longer describe the price a virtual order would get (Arena quant_cycle: 20 s).
QUOTE_MAX_AGE = 20
# ADR-0021: the Arena's stop - a depot 10 % below its equity peak is sold out and halted.
DRAWDOWN_KILL = 0.10

Quotes = Mapping[Symbol, Quote]
_COLUMN = {Symbol.BTCEUR: "btc", Symbol.ETHEUR: "eth"}


@dataclass(frozen=True)
class Costs:
    """Fee and slippage per side as fractions; defaults are the Arena code's (Spec 004 AK8)."""

    fee_rate: float = ARENA_TAKER_FEE
    slippage: float = ARENA_SLIPPAGE

    def __post_init__(self) -> None:
        for name, x in (("TAKER_FEE", self.fee_rate), ("SLIPPAGE", self.slippage)):
            # Written as a range check so NaN is refused as well.
            if not 0 <= x <= MAX_COST_RATE:
                raise ValueError(f"{name} outside [0, {MAX_COST_RATE}]: {x!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Costs":
        return cls(
            float(env.get("TAKER_FEE", repr(ARENA_TAKER_FEE))),
            float(env.get("SLIPPAGE", repr(ARENA_SLIPPAGE))),
        )


@dataclass(frozen=True)
class RiskStop:
    """A drawdown stop that ``mark`` just triggered (the Arena writes an ALERT log row instead)."""

    agent: str
    equity: float  # before the forced sale
    peak: float
    at: str


def value(p: Mapping[str, Any], qs: Quotes) -> float:
    total: float = p["cash"] + sum(p[_COLUMN[s]] * qs[s].mid for s in SYMBOLS)
    return total


def current_weight(p: Mapping[str, Any], symbol: Symbol, qs: Quotes) -> float:
    equity = value(p, qs)
    return float(p[_COLUMN[symbol]] * qs[symbol].mid / equity) if equity > 0 else 0


def trade_to_target(
    con: sqlite3.Connection,
    agent: str,
    symbol: Symbol,
    target: float,
    qs: Quotes,
    decision_id: int | None,
    costs: Costs,
    *,
    executed_at: str,
) -> None:
    """Move one position towards ``target`` of equity within the limits. Caller holds write_tx."""
    p = balance(con, agent)
    if p["halted"] and target > current_weight(p, symbol, qs):
        return
    col = _COLUMN[symbol]
    q = qs[symbol]
    eq = value(p, qs)
    present = p[col] * q.mid
    desired = min(MAX_SYMBOL_WEIGHT, max(0.0, target)) * eq
    diff = desired - present
    if abs(diff) < max(MIN_TRADE_EUR, REBALANCE_BAND * eq):
        return
    rate = costs.fee_rate
    slip = costs.slippage
    if diff > 0:
        other = sum(p[_COLUMN[s]] * qs[s].mid for s in SYMBOLS if s != symbol)
        max_portfolio_notional = max(0.0, MAX_TOTAL_WEIGHT * eq - other - present)
        spend = min(diff, max_portfolio_notional, p["cash"] / (1 + rate))
        if spend < MIN_TRADE_EUR:
            return
        fill = q.ask * (1 + slip)
        qty = spend / fill
        fee = spend * rate
        con.execute(
            f"UPDATE portfolios SET cash=cash-?,{col}={col}+? WHERE agent=?",
            (spend + fee, qty, agent),
        )
        side, notional = "BUY", spend
    else:
        fill = q.bid * (1 - slip)
        qty = min(p[col], abs(diff) / q.mid)
        notional = qty * fill
        if notional < MIN_TRADE_EUR:
            return
        fee = notional * rate
        con.execute(
            f"UPDATE portfolios SET cash=cash+?,{col}=MAX(0,{col}-?) WHERE agent=?",
            (notional - fee, qty, agent),
        )
        side = "SELL"
    con.execute(
        "INSERT INTO trades(ts,agent,symbol,side,qty,fill,notional,fee,slip_rate,decision_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (executed_at, agent, symbol.name, side, qty, fill, notional, fee, slip, decision_id),
    )


def mark(
    con: sqlite3.Connection, agent: str, qs: Quotes, now: str, costs: Costs
) -> RiskStop | None:
    """Update the peak, enforce the drawdown stop and store one equity point per timestamp.

    ADR-0021: the Arena's mark_and_risk. Below ``peak * (1 - DRAWDOWN_KILL)`` (mid valuation)
    every depot except buy & hold is halted and sold out at ``bid * (1 - slippage)``, even
    below MIN_TRADE_EUR; the depot is never released here. Caller holds write_tx.
    """
    p = balance(con, agent)
    eq = value(p, qs)
    peak = max(eq, p["peak"])
    con.execute("UPDATE portfolios SET peak=? WHERE agent=?", (peak, agent))
    stop = None
    if agent != BUYHOLD and not p["halted"] and peak > 0 and eq < peak * (1 - DRAWDOWN_KILL):
        con.execute("UPDATE portfolios SET halted=1 WHERE agent=?", (agent,))
        stop = RiskStop(agent, eq, peak, now)
        for symbol in SYMBOLS:
            p = balance(con, agent)
            col = _COLUMN[symbol]
            qty = p[col]
            if qty <= 0:
                continue
            fill = qs[symbol].bid * (1 - costs.slippage)
            gross = qty * fill
            fee = gross * costs.fee_rate
            con.execute(
                f"UPDATE portfolios SET cash=cash+?,{col}=0 WHERE agent=?", (gross - fee, agent)
            )
            con.execute(
                "INSERT INTO trades(ts,agent,symbol,side,qty,fill,notional,fee,slip_rate,"
                "decision_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (now, agent, symbol.name, "RISK_SELL", qty, fill, gross, fee, costs.slippage),
            )
        p = balance(con, agent)
        eq = value(p, qs)
    con.execute(
        "INSERT OR REPLACE INTO equity(ts,agent,value,cash,btc,eth) VALUES (?,?,?,?,?,?)",
        (now, agent, eq, p["cash"], p["btc"], p["eth"]),
    )
    return stop


def record_decision(
    con: sqlite3.Connection,
    agent: str,
    symbol: Symbol,
    bar_ts: int,
    model: str,
    sig: Signal,
    data: Mapping[str, Any],
    *,
    costs: Costs,
    now: str,
    qs: Quotes | None,
    status: str = "OK",
    stops: list[RiskStop] | None = None,
) -> bool:
    """Persist one decision and its virtual trade atomically; False if the bar was decided.

    Fails closed: without fresh quotes of both markets the decision is stored as STALE_QUOTE
    and nothing is traded. A drawdown stop the trade triggers is appended to ``stops``.
    """
    with write_tx(con):
        p = balance(con, agent)
        reason = sig.reason
        if status == "OK":
            sig.validate()
            reason = sig.reason.strip()[:REASON_MAX]
        action: str = sig.action
        target = sig.target_fraction
        if status == "OK":
            if qs is None or set(qs) != set(SYMBOLS):
                status, action, target = "STALE_QUOTE", "HOLD", 0
            else:
                current = current_weight(p, symbol, qs)
                if action == "HOLD":
                    target = current
                if action == "BUY" and target < current:
                    target = current
                if action == "SELL" and target > current:
                    target = current
                if agent == BUYHOLD and p[_COLUMN[symbol]] > 0:
                    action, target = "HOLD", current
        cursor = con.execute(
            "INSERT OR IGNORE INTO decisions"
            " (ts,agent,symbol,bar_ts,model,action,target,confidence,reason,inputs,status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                now,
                agent,
                symbol.name,
                bar_ts,
                model,
                action,
                target,
                sig.confidence,
                reason,
                json.dumps(data, ensure_ascii=False),
                status,
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted and status == "OK" and action != "HOLD" and not p["halted"]:
            assert qs is not None  # status stays OK only with both quotes
            trade_to_target(
                con, agent, symbol, target, qs, cursor.lastrowid, costs, executed_at=now
            )
            stop = mark(con, agent, qs, now, costs)
            if stop is not None and stops is not None:
                stops.append(stop)
        return inserted


def initialize_buyhold(con: sqlite3.Connection, qs: Quotes, costs: Costs, *, when: str) -> int:
    """Buy the benchmark once per market on FRESH bid/ask quotes; restarts never buy twice."""
    if set(qs) != set(SYMBOLS):
        raise MarketDataError("buy & hold needs fresh quotes of both markets")
    for quote in qs.values():
        quote.validate()
    count = 0
    for symbol in SYMBOLS:
        has_trade = con.execute(
            "SELECT 1 FROM trades WHERE agent=? AND symbol=? AND side=? LIMIT 1",
            (BUYHOLD, symbol.name, "BUY"),
        ).fetchone()
        if has_trade:
            continue
        context = {
            "strategy": "buy_and_hold",
            "source": "BITVAVO-LIVE-QUOTE",
            "initial_allocation_pct": 45,
            "requires_candles": False,
            "bid": qs[symbol].bid,
            "ask": qs[symbol].ask,
        }
        sig = Signal(
            "BUY",
            0.45,
            1.0,
            "Passiver Benchmark: einmaliger Kauf auf frischem Geld-/Briefkurs",
            BUYHOLD,
        )
        # bar_ts -1 is a sentinel apart from candle decisions, so the key is stable over restarts.
        if record_decision(
            con, BUYHOLD, symbol, -1, "BUY&HOLD", sig, context, costs=costs, now=when, qs=qs
        ):
            count += 1
    return count


def record_quotes(con: sqlite3.Connection, qs: Quotes, *, now: str) -> None:
    """Store the latest valid bid/ask of BOTH markets; partial or invalid sets are refused."""
    if set(qs) != set(SYMBOLS):
        raise MarketDataError("quotes of both markets required")
    for quote in qs.values():
        quote.validate()
    with write_tx(con):
        for symbol in SYMBOLS:
            q = qs[symbol]
            con.execute(
                "INSERT INTO market(symbol,ts,bar_ts,bid,ask,last_close,source)"
                " VALUES (?,?,0,?,?,?,'BITVAVO-LIVE')"
                " ON CONFLICT(symbol) DO UPDATE SET ts=excluded.ts,"
                " bid=excluded.bid,ask=excluded.ask,source=excluded.source",
                (symbol.name, now, q.bid, q.ask, q.mid),
            )


def latest_quotes(
    con: sqlite3.Connection, *, now_epoch: float, max_age_seconds: int = QUOTE_MAX_AGE
) -> dict[Symbol, Quote] | None:
    """Both stored quotes if each is valid and 0..max_age old; otherwise None (fail closed)."""
    rows = con.execute("SELECT symbol,ts,bid,ask FROM market").fetchall()
    if len(rows) != len(SYMBOLS):
        return None
    qs: dict[Symbol, Quote] = {}
    for r in rows:
        try:
            age = now_epoch - datetime.fromisoformat(r["ts"]).timestamp()
            if not 0 <= age <= max_age_seconds:
                return None
            quote = Quote(float(r["bid"]), float(r["ask"]))
            quote.validate()
            qs[Symbol[r["symbol"]]] = quote
        except (TypeError, ValueError, KeyError):
            return None
    return qs if set(qs) == set(SYMBOLS) else None


def record_bar(con: sqlite3.Connection, symbol: Symbol, *, bar_ts: int, last_close: float) -> None:
    """Newest closed 1m bar per market, next to its stored quote (Arena update_market, live)."""
    with write_tx(con):
        con.execute(
            "UPDATE market SET bar_ts=?,last_close=? WHERE symbol=?",
            (bar_ts, last_close, symbol.name),
        )
