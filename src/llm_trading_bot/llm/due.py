# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - llm_due
"""When a depot's model is asked again (ADR-0013): as rarely as possible, as often as needed.

New information for the model appears when an hour has passed, a market moved strongly, or a classic
strategy changed its verdict. Asking more often only repeats the same picture.
"""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from llm_trading_bot.market.models import Symbol

HOUR = 3600
_REF_COLUMN = {Symbol.BTCEUR: "ref_mid_btc", Symbol.ETHEUR: "ref_mid_eth"}

ReferenceSignals = Mapping[Symbol, Mapping[str, str]]


@dataclass(frozen=True)
class Cadence:
    """Lower bounds keep a typo in the environment from turning into a flood of paid calls."""

    min_interval: int
    event_cooldown: int
    move_threshold: float
    max_calls_per_hour: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Cadence":
        return cls(
            max(900, int(env.get("LLM_MIN_INTERVAL_SECONDS", "3600"))),
            max(300, int(env.get("LLM_EVENT_COOLDOWN_SECONDS", "600"))),
            max(0.005, float(env.get("LLM_EVENT_MOVE_THRESHOLD", ".018"))),
            max(0, int(env.get("LLM_MAX_CALLS_PER_HOUR", "4"))),
        )


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), UTC).isoformat(timespec="seconds")


def trigger_state(signals: ReferenceSignals) -> str:
    """What the next attempt compares against: market-only signals, stored as JSON."""
    return json.dumps({symbol.market: dict(now) for symbol, now in signals.items()}, sort_keys=True)


def _previous_signals(stored: str) -> dict[str, object]:
    try:
        markets = json.loads(stored)
    except (ValueError, TypeError):
        return {}
    return markets if isinstance(markets, dict) else {}


DUE_REASONS = ("first", "interval", "move", "signal_change", "new_market")
"""Why a depot is asked (Spec 015 AK2); stored with each attempt in the observations."""


def llm_due(
    con: sqlite3.Connection,
    agent: str,
    mids: Mapping[Symbol, float],
    signals: ReferenceSignals,
    now_epoch: float,
    cadence: Cadence,
) -> bool:
    """Whether to ask now; the same rule as ``due_reason``, which also says why."""
    return due_reason(con, agent, mids, signals, now_epoch, cadence)[0] is not None


def due_reason(
    con: sqlite3.Connection,
    agent: str,
    mids: Mapping[Symbol, float],
    signals: ReferenceSignals,
    now_epoch: float,
    cadence: Cadence,
) -> tuple[str | None, dict[str, object]]:
    """Every attempt counts as asked, also blocked or failed ones (Arena: attempts, not answers).

    Returns the reason and the values it was decided on (elapsed seconds, moves against the
    reference mids, changed reference signals) - only for the observations, never for trading.
    """
    row = con.execute(
        "SELECT ts,ref_mid_btc,ref_mid_eth,trigger_signals FROM llm_calls WHERE agent=?"
        " ORDER BY id DESC LIMIT 1",
        (agent,),
    ).fetchone()
    if not row:
        return "first", {}
    elapsed = max(0, now_epoch - datetime.fromisoformat(row["ts"]).timestamp())
    detail: dict[str, object] = {"elapsed": elapsed}
    if elapsed >= cadence.min_interval:
        return "interval", detail
    if elapsed < cadence.event_cooldown:
        return None, detail
    moves: dict[str, float] = {}
    detail["moves"] = moves
    moved = False
    for symbol, mid in mids.items():
        ref = row[_REF_COLUMN[symbol]]
        if ref is not None and ref > 0:
            moves[symbol.market] = mid / ref - 1
            # The first market over the threshold decides; later ones only add their value.
            moved = moved or abs(mid / ref - 1) >= cadence.move_threshold
    if moved:
        return "move", detail
    # ``signals`` must not depend on the depot's own position (computed with weight 0): otherwise
    # the depot's own trade would look like new market information and trigger another paid call.
    previous = _previous_signals(row["trigger_signals"])
    changed = {
        symbol.market: {"before": previous.get(symbol.market), "now": dict(now)}
        for symbol, now in signals.items()
        if previous.get(symbol.market) != dict(now)
    }
    detail["changed"] = changed
    if not _signals_changed(previous, signals):
        return None, detail
    # A market that was not part of the last request counts as changed: its picture is new.
    known = any(market in previous for market in changed)
    return ("signal_change" if known else "new_market"), detail


def _signals_changed(previous: Mapping[str, object], signals: ReferenceSignals) -> bool:
    return any(previous.get(symbol.market) != dict(now) for symbol, now in signals.items())


def attempts_last_hour(con: sqlite3.Connection, agent: str, now_epoch: float) -> int:
    count: int = con.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE agent=? AND ts>?", (agent, iso(now_epoch - HOUR))
    ).fetchone()[0]
    return count


def hourly_limit_reached(
    con: sqlite3.Connection, agent: str, now_epoch: float, cadence: Cadence
) -> bool:
    return attempts_last_hour(con, agent, now_epoch) >= cadence.max_calls_per_hour
