# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - llm_due
"""When a depot's model is asked again (ADR-0011): on a fixed interval, or after a large move of
either market, never more than a fixed number of attempts per rolling hour."""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from llm_trading_bot.market.models import Symbol

HOUR = 3600
_REF_COLUMN = {Symbol.BTCEUR: "ref_mid_btc", Symbol.ETHEUR: "ref_mid_eth"}


@dataclass(frozen=True)
class Cadence:
    """Lower bounds keep a typo in the environment from turning into a flood of paid calls."""

    min_interval: int = 900
    event_cooldown: int = 300
    move_threshold: float = 0.018
    max_calls_per_hour: int = 8

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Cadence":
        return cls(
            max(600, int(env.get("LLM_MIN_INTERVAL_SECONDS", "900"))),
            max(300, int(env.get("LLM_EVENT_COOLDOWN_SECONDS", "300"))),
            max(0.005, float(env.get("LLM_EVENT_MOVE_THRESHOLD", ".018"))),
            max(0, int(env.get("LLM_MAX_CALLS_PER_HOUR", "8"))),
        )


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), UTC).isoformat(timespec="seconds")


def llm_due(
    con: sqlite3.Connection,
    agent: str,
    mids: Mapping[Symbol, float],
    now_epoch: float,
    cadence: Cadence,
) -> bool:
    """Every attempt counts as asked, also blocked or failed ones (Arena: attempts, not answers)."""
    row = con.execute(
        "SELECT ts,ref_mid_btc,ref_mid_eth FROM llm_calls WHERE agent=? ORDER BY id DESC LIMIT 1",
        (agent,),
    ).fetchone()
    if not row:
        return True
    elapsed = max(0, now_epoch - datetime.fromisoformat(row["ts"]).timestamp())
    if elapsed >= cadence.min_interval:
        return True
    if elapsed < cadence.event_cooldown:
        return False
    for symbol, mid in mids.items():
        ref = row[_REF_COLUMN[symbol]]
        if ref is not None and ref > 0 and abs(mid / ref - 1) >= cadence.move_threshold:
            return True
    return False


def attempts_last_hour(con: sqlite3.Connection, agent: str, now_epoch: float) -> int:
    count: int = con.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE agent=? AND ts>?", (agent, iso(now_epoch - HOUR))
    ).fetchone()[0]
    return count


def hourly_limit_reached(
    con: sqlite3.Connection, agent: str, now_epoch: float, cadence: Cadence
) -> bool:
    return attempts_last_hour(con, agent, now_epoch) >= cadence.max_calls_per_hour
