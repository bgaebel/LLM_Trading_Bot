"""What one paper cycle did, for the CLI summary and the tests."""

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal

from llm_trading_bot.llm.budget import Usage
from llm_trading_bot.market.models import Symbol


@dataclass(frozen=True)
class MarketResult:
    agent: str
    symbol: Symbol
    bar_ts: int | None = None
    new: bool = False
    action: str = ""
    target: float = 0.0
    status: str = ""
    traded: bool = False
    reason: str = ""
    error: str = ""
    attempt: int | None = None
    """For GPT decisions: the llm_calls attempt the decision came from."""


@dataclass(frozen=True)
class LlmOutcome:
    """One GPT depot in one cycle: asked or why not, and what it cost."""

    agent: str
    model: str
    status: str
    """ASKED, NOT_DUE, HOURLY_LIMIT, BUDGET_BLOCKED, NO_QUOTES, NO_MARKETS, HALTED,
    STOPPING (not asked because the service is stopping) or ERROR."""
    detail: str = ""
    attempt_id: int | None = None
    usage: Usage | None = None
    cost_eur: Decimal | None = None
    regimes: dict[Symbol, str] = field(default_factory=dict)


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
    agents: tuple[str, ...]
    quotes_error: str = ""
    buyhold_bought: int = 0
    markets: list[MarketResult] = field(default_factory=list)
    llm: list[LlmOutcome] = field(default_factory=list)
    equity: list[Equity] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return (
            not self.quotes_error
            and all(not m.error for m in self.markets)
            and all(o.status != "ERROR" for o in self.llm)
        )


def decision_result(
    con: sqlite3.Connection,
    agent: str,
    symbol: Symbol,
    bar_ts: int,
    new: bool,
    *,
    attempt: int | None = None,
) -> MarketResult:
    row = con.execute(
        "SELECT d.action, d.target, d.status, d.reason,"
        " (SELECT COUNT(*) FROM trades t WHERE t.decision_id = d.id) AS n"
        " FROM decisions d WHERE agent=? AND symbol=? AND bar_ts=?",
        (agent, symbol.name, bar_ts),
    ).fetchone()
    return MarketResult(
        agent,
        symbol,
        bar_ts,
        new,
        row["action"],
        row["target"],
        row["status"],
        row["n"] > 0,
        row["reason"],
        attempt=attempt,
    )
