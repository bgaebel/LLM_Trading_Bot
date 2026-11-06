# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - llm_due, daily call count
"""When the model is asked again: on a fixed interval or after a large move (Arena v0.3.1)."""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Cadence:
    """Lower bounds keep a typo in the environment from turning into a flood of paid calls."""

    min_interval: int = 1800
    event_cooldown: int = 600
    move_threshold: float = 0.018
    max_daily_calls: int = 60

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Cadence":
        return cls(
            max(600, int(env.get("LLM_MIN_INTERVAL_SECONDS", "1800"))),
            max(300, int(env.get("LLM_EVENT_COOLDOWN_SECONDS", "600"))),
            max(0.005, float(env.get("LLM_EVENT_MOVE_THRESHOLD", ".018"))),
            max(0, int(env.get("MAX_DAILY_LLM_CALLS", "60"))),
        )


def llm_due(
    con: sqlite3.Connection,
    agent: str,
    symbol: str,
    mid: float,
    now_epoch: float,
    cadence: Cadence,
) -> bool:
    row = con.execute(
        "SELECT ts,ref_mid FROM llm_calls WHERE agent=? AND symbol=? ORDER BY id DESC LIMIT 1",
        (agent, symbol),
    ).fetchone()
    if not row:
        return True
    previous = datetime.fromisoformat(row["ts"]).timestamp()
    elapsed = max(0, now_epoch - previous)
    if elapsed >= cadence.min_interval:
        return True
    ref_mid: float = row["ref_mid"]
    return (
        elapsed >= cadence.event_cooldown
        and ref_mid > 0
        and abs(mid / ref_mid - 1) >= cadence.move_threshold
    )


def calls_today(con: sqlite3.Connection, agent: str, now_epoch: float) -> int:
    """Every attempt counts, also blocked or failed ones (Arena: the cap counts ATTEMPTS)."""
    day = datetime.fromtimestamp(now_epoch, UTC).strftime("%Y-%m-%d")
    count: int = con.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE agent=? AND ts>=?", (agent, day)
    ).fetchone()[0]
    return count
