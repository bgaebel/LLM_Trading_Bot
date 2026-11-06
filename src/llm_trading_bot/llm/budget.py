# Origin: LLM-Trading-Arena@7563724 apps/arena/budget.py (adapted) - reserve, settle, settings
"""Hard daily LLM spend limit in EUR, booked in the bot database (ADR-0009).

Every attempt is a row in ``llm_calls``. Before a request leaves the process its reservation is
committed; afterwards the reservation is replaced by the cost computed from the reported usage.
Unknown cost (crash, timeout, missing usage) keeps the reservation, so the limit errs on the safe
side. Prices are the operator's estimates, not invoices.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from llm_trading_bot.paper.db import write_tx

DEFAULT_MODEL = "gpt-6.1-sol"
# gpt-6.1-sol list prices in USD per 1M tokens, taken 1:1 as EUR: rather too high (ADR-0009).
DEFAULT_PRICES = (Decimal("2.00"), Decimal("10.00"))
MILLION = Decimal(1_000_000)
# Statuses whose attempt never reached the provider and cost nothing.
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
class BudgetSettings:
    daily_cap_eur: Decimal
    call_reserve_eur: Decimal
    input_eur_per_million: Decimal | None
    output_eur_per_million: Decimal | None

    @classmethod
    def from_env(cls, env: Mapping[str, str], model: str) -> "BudgetSettings":
        cap = amount(env.get("LLM_DAILY_BUDGET_EUR", "3"), "LLM_DAILY_BUDGET_EUR")
        hold = amount(env.get("LLM_CALL_RESERVE_EUR", "0.10"), "LLM_CALL_RESERVE_EUR")
        names = ("OPENAI_INPUT_EUR_PER_MILLION", "OPENAI_OUTPUT_EUR_PER_MILLION")
        raw = [env.get(n) for n in names]
        if any(r is not None for r in raw) and not all(r is not None for r in raw):
            raise ValueError("OPENAI_INPUT_EUR_PER_MILLION and _OUTPUT_ must be set together")
        if all(r is not None for r in raw):
            prices: tuple[Decimal | None, Decimal | None] = (
                amount(raw[0], names[0]),
                amount(raw[1], names[1]),
            )
        elif model == DEFAULT_MODEL:
            prices = DEFAULT_PRICES
        else:
            # Unknown model without prices: blocked at reservation time, never guessed.
            prices = (None, None)
        return cls(cap, hold, *prices)


@dataclass(frozen=True)
class Attempt:
    """What is known before the request: stored first, so provenance survives any failure."""

    agent: str
    symbol: str
    ref_mid: float
    model: str
    max_output_tokens: int
    effort: str
    prompt_sha256: str
    context: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None


def _day_bounds(now_epoch: float) -> tuple[str, str]:
    day = datetime.fromtimestamp(now_epoch, UTC).date()
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


def charged_today(con: sqlite3.Connection, now_epoch: float) -> tuple[Decimal, bool]:
    """Known costs plus open reservations of today, and whether any call exceeded its reserve."""
    start, end = _day_bounds(now_epoch)
    rows = con.execute(
        "SELECT reserved_eur, cost_eur, reservation_exceeded FROM llm_calls"
        " WHERE ts>=? AND ts<? AND status NOT IN (?,?)",
        (start, end, *FREE_STATUSES),
    ).fetchall()
    total = Decimal(0)
    exceeded = False
    for row in rows:
        value = row["cost_eur"] if row["cost_eur"] is not None else row["reserved_eur"]
        # A sent attempt without any amount has unknown cost: treat the day as used up.
        total += amount(value, "llm_calls") if value is not None else Decimal("Infinity")
        exceeded = exceeded or bool(row["reservation_exceeded"])
    return total, exceeded


def reserve(
    con: sqlite3.Connection,
    attempt: Attempt,
    settings: BudgetSettings,
    *,
    now: str,
    now_epoch: float,
) -> int:
    """Book the attempt; raise BudgetBlocked (with the row stored) if it must not be sent."""
    with write_tx(con):
        reason = ""
        if settings.input_eur_per_million is None or settings.output_eur_per_million is None:
            reason = f"no prices configured for model {attempt.model}"
        elif settings.call_reserve_eur <= 0:
            reason = "LLM_CALL_RESERVE_EUR must be positive"
        else:
            used, exceeded = charged_today(con, now_epoch)
            if exceeded:
                reason = "a call cost more than its reservation today"
            elif used + settings.call_reserve_eur > settings.daily_cap_eur:
                reason = f"daily budget reached ({used} + {settings.call_reserve_eur} EUR)"
        cursor = con.execute(
            "INSERT INTO llm_calls(ts,agent,symbol,ref_mid,status,model,max_output_tokens,effort,"
            "prompt_sha256,context,reserved_eur,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now,
                attempt.agent,
                attempt.symbol,
                attempt.ref_mid,
                "BUDGET_BLOCKED" if reason else "PENDING",
                attempt.model,
                attempt.max_output_tokens,
                attempt.effort,
                attempt.prompt_sha256,
                attempt.context,
                None if reason else str(settings.call_reserve_eur),
                reason or None,
            ),
        )
        attempt_id = cursor.lastrowid
    assert attempt_id is not None
    if reason:
        raise BudgetBlocked(reason)
    return attempt_id


def cost_of(usage: Usage, settings: BudgetSettings) -> Decimal | None:
    """Upper-bound estimate: cached input is priced like normal input (OpenAI bills it lower) and
    output_tokens already contains the reasoning tokens."""
    if settings.input_eur_per_million is None or settings.output_eur_per_million is None:
        return None
    return (
        Decimal(usage.input_tokens) * settings.input_eur_per_million
        + Decimal(usage.output_tokens) * settings.output_eur_per_million
    ) / MILLION


def settle(
    con: sqlite3.Connection,
    attempt_id: int,
    settings: BudgetSettings,
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
        cost = Decimal(0) if not sent else cost_of(usage, settings) if usage else None
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
