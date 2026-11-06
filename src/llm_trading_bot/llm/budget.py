# Origin: LLM-Trading-Arena@7563724 apps/arena/budget.py (adapted) - reserve, settle, amount
"""Hard daily LLM spend limits in EUR, booked in the bot database (ADR-0011).

Every request is a row in ``llm_calls``. Before it leaves the process its reservation - the worst
case the prices allow - is committed; afterwards the reservation is replaced by the cost computed
from the reported usage. Unknown cost (crash, timeout, missing usage) keeps the reservation, so the
limits err on the safe side. Prices are the operator's estimates, not invoices.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from llm_trading_bot.paper.db import write_tx

MILLION = Decimal(1_000_000)
CENT_FRACTION = Decimal("0.0001")
# Statuses whose request never reached the provider and cost nothing.
FREE_STATUSES = ("BUDGET_BLOCKED", "NOT_SENT")


class BudgetBlocked(RuntimeError):
    """No request may be sent now; the reason is stored on the attempt."""


def amount(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"{name}: not a number: {value!r}") from None
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name}: must be a finite amount >= 0: {value!r}")
    return result


@dataclass(frozen=True)
class Pricing:
    """EUR per 1M tokens."""

    input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True)
class Limits:
    total_daily_eur: Decimal
    agent_daily_eur: Decimal
    pricing: Pricing | None
    """None: no price known for this model - it is never called."""


@dataclass(frozen=True)
class Attempt:
    """What is known before the request: stored first, so provenance survives any failure."""

    agent: str
    markets: str
    ref_mid_btc: float | None
    ref_mid_eth: float | None
    model: str
    prompt_version: str
    prompt_sha256: str
    max_output_tokens: int
    effort: str
    context: str
    input_chars: int
    """Characters of instructions, schema and message; an upper bound for input tokens."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None


def reservation(attempt: Attempt, pricing: Pricing) -> Decimal:
    """Worst case: one token per input character plus the full output allowance, rounded up."""
    worst = (
        Decimal(attempt.input_chars) * pricing.input_per_million
        + Decimal(attempt.max_output_tokens) * pricing.output_per_million
    ) / MILLION
    return worst.quantize(CENT_FRACTION, rounding=ROUND_CEILING)


def _day_bounds(now_epoch: float) -> tuple[str, str]:
    day = datetime.fromtimestamp(now_epoch, UTC).date()
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


def charged_today(
    con: sqlite3.Connection, now_epoch: float, agent: str | None = None
) -> tuple[Decimal, bool]:
    """Known costs plus open reservations today (one agent or all), and whether any call of that
    scope cost more than its reservation."""
    start, end = _day_bounds(now_epoch)
    query = (
        "SELECT reserved_eur, cost_eur, reservation_exceeded FROM llm_calls"
        " WHERE ts>=? AND ts<? AND status NOT IN (?,?)"
    )
    params: tuple[object, ...] = (start, end, *FREE_STATUSES)
    if agent is not None:
        query += " AND agent=?"
        params += (agent,)
    total = Decimal(0)
    exceeded = False
    for row in con.execute(query, params).fetchall():
        value = row["cost_eur"] if row["cost_eur"] is not None else row["reserved_eur"]
        # A sent request without any amount has unknown cost: treat the budget as used up.
        total += amount(value, "llm_calls") if value is not None else Decimal("Infinity")
        exceeded = exceeded or bool(row["reservation_exceeded"])
    return total, exceeded


def _blocking_reason(
    con: sqlite3.Connection, attempt: Attempt, limits: Limits, hold: Decimal, now_epoch: float
) -> str:
    agent_used, agent_exceeded = charged_today(con, now_epoch, attempt.agent)
    total_used, _ = charged_today(con, now_epoch)
    if agent_exceeded:
        return "a call of this depot cost more than its reservation today"
    if agent_used + hold > limits.agent_daily_eur:
        return f"depot budget reached ({agent_used} + {hold} > {limits.agent_daily_eur} EUR)"
    if total_used + hold > limits.total_daily_eur:
        return f"total budget reached ({total_used} + {hold} > {limits.total_daily_eur} EUR)"
    return ""


def reserve(
    con: sqlite3.Connection,
    attempt: Attempt,
    limits: Limits,
    *,
    now: str,
    now_epoch: float,
) -> int:
    """Book the attempt; raise BudgetBlocked (with the row stored) if it must not be sent."""
    with write_tx(con):
        hold: Decimal | None = None
        if limits.pricing is None:
            reason = f"no prices configured for model {attempt.model}"
        else:
            hold = reservation(attempt, limits.pricing)
            reason = _blocking_reason(con, attempt, limits, hold, now_epoch)
        cursor = con.execute(
            "INSERT INTO llm_calls(ts,agent,markets,ref_mid_btc,ref_mid_eth,status,model,"
            "prompt_version,prompt_sha256,max_output_tokens,effort,context,reserved_eur,error)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now,
                attempt.agent,
                attempt.markets,
                attempt.ref_mid_btc,
                attempt.ref_mid_eth,
                "BUDGET_BLOCKED" if reason else "PENDING",
                attempt.model,
                attempt.prompt_version,
                attempt.prompt_sha256,
                attempt.max_output_tokens,
                attempt.effort,
                attempt.context,
                None if reason or hold is None else str(hold),
                reason or None,
            ),
        )
        attempt_id = cursor.lastrowid
    assert attempt_id is not None
    if reason:
        raise BudgetBlocked(reason)
    return attempt_id


def cost_of(usage: Usage, pricing: Pricing) -> Decimal:
    """Upper-bound estimate: cached input is priced like normal input (OpenAI bills it lower) and
    output_tokens already contains the reasoning tokens."""
    return (
        Decimal(usage.input_tokens) * pricing.input_per_million
        + Decimal(usage.output_tokens) * pricing.output_per_million
    ) / MILLION


def settle(
    con: sqlite3.Connection,
    attempt_id: int,
    pricing: Pricing,
    *,
    status: str,
    usage: Usage | None,
    latency_seconds: float | None,
    response_id: str | None = None,
    error: str | None = None,
    sent: bool = True,
) -> Decimal | None:
    """Replace the reservation by the actual estimate; unknown usage keeps the reservation."""
    with write_tx(con):
        row = con.execute("SELECT reserved_eur FROM llm_calls WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown llm attempt {attempt_id}")
        cost = Decimal(0) if not sent else cost_of(usage, pricing) if usage else None
        reserved = amount(row["reserved_eur"], "reserved_eur") if row["reserved_eur"] else None
        exceeded = cost is not None and reserved is not None and cost > reserved
        con.execute(
            "UPDATE llm_calls SET status=?,cost_eur=?,reservation_exceeded=?,response_id=?,"
            "input_tokens=?,output_tokens=?,reasoning_tokens=?,cached_tokens=?,"
            "latency_seconds=?,error=? WHERE id=?",
            (
                status if sent else "NOT_SENT",
                None if cost is None else str(cost),
                int(exceeded),
                response_id,
                usage.input_tokens if usage else None,
                usage.output_tokens if usage else None,
                usage.reasoning_tokens if usage else None,
                usage.cached_tokens if usage else None,
                latency_seconds,
                error,
                attempt_id,
            ),
        )
    return cost
