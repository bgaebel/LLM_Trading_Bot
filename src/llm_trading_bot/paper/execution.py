# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - fee_rate, slip_rate, value,
#   current_weight, trade_to_target, mark_and_risk (without the drawdown stop)
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


def mark(con: sqlite3.Connection, agent: str, qs: Quotes, now: str) -> None:
    """Update the equity peak and store one equity point per agent and timestamp.

    The Arena's mark_and_risk also enforces the 10 % drawdown stop; that is M1 Nr. 4 and not
    ported yet (Spec 004). ``halted`` is only read here. Caller holds write_tx.
    """
    p = balance(con, agent)
    eq = value(p, qs)
    peak = max(eq, p["peak"])
    con.execute("UPDATE portfolios SET peak=? WHERE agent=?", (peak, agent))
    con.execute(
        "INSERT OR REPLACE INTO equity(ts,agent,value,cash,btc,eth) VALUES (?,?,?,?,?,?)",
        (now, agent, eq, p["cash"], p["btc"], p["eth"]),
    )


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
) -> bool:
    """Persist one decision and its virtual trade atomically; False if the bar was decided.

    Fails closed: without fresh quotes of both markets the decision is stored as STALE_QUOTE
    and nothing is traded.
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
            mark(con, agent, qs, now)
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
