"""Trading pause and release of stopped depots (Spec 011 AK5/AK6, ADR-0021).

Both are requested in the web UI, which only writes ``secrets.env`` (ADR-0015/0016); the service
reads them every round and is the only one to change the database (ADR-0017).

- ``BOT_PAUSED=1``: no trades except the drawdown stop's forced sale. Fail closed: a file that
  cannot be read or a value that is not ``1``/``0``/empty means pause. The environment wins.
- ``BOT_RESUME=<depot>@<ISO time>,...``: release a stopped depot, at most once per stop and only
  for a request that is younger than the stop and at most RESUME_MAX_AGE old.
"""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from llm_trading_bot.llm.due import iso
from llm_trading_bot.paper.db import balance, write_tx
from llm_trading_bot.web.credentials import CONTROL_FIELDS, load_env_file

PAUSE_FIELD, RESUME_FIELD = CONTROL_FIELDS
RESUME_MAX_AGE = 24 * 3600
"""A request older than a day is not acted on: the user may have forgotten why they asked."""
MAX_FUTURE_SKEW = 300
"""Web UI and service run on one machine; more than a few minutes ahead is a broken clock."""

ENVIRONMENT, FILE, INVALID = "environment", "file", "invalid"


@dataclass(frozen=True)
class Controls:
    """What the service applies in one round."""

    paused: bool = False
    source: str = ""
    """Where ``paused`` came from: environment, file, invalid (fail closed) or "" (default)."""
    resumes: Mapping[str, float] = field(default_factory=dict)
    """Depot -> epoch of its latest release request."""
    warnings: tuple[str, ...] = ()


def epoch(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def parse_resumes(text: str) -> tuple[dict[str, float], list[str]]:
    """``depot@ISO,...`` -> {depot: epoch}; unreadable entries are skipped and named."""
    out: dict[str, float] = {}
    bad: list[str] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        agent, sep, ts = item.partition("@")
        try:
            if not sep or not agent:
                raise ValueError(item)
            at = epoch(ts)
            if datetime.fromisoformat(ts).tzinfo is None:
                raise ValueError(item)
        except ValueError:
            bad.append(item[:80])
            continue
        out[agent] = max(at, out.get(agent, at))
    return out, bad


def format_resumes(resumes: Mapping[str, str]) -> str:
    return ",".join(f"{agent}@{ts}" for agent, ts in sorted(resumes.items()))


def _paused(value: str) -> bool | None:
    return {"1": True, "0": False, "": False}.get(value.strip())


def read_controls(env: Mapping[str, str], secrets: Path) -> Controls:
    """Read pause and release requests apart from the rest of the configuration: a broken
    budget value must not keep the last pause state, and a broken pause must not trade."""
    warnings: list[str] = []
    try:
        stored = load_env_file(secrets)
    except (OSError, UnicodeError, ValueError) as exc:
        stored = None
        warnings.append(f"{secrets.name} unreadable ({type(exc).__name__}): trading paused")
    resumes: dict[str, float] = {}
    if stored is not None:
        resumes, bad = parse_resumes(stored.get(RESUME_FIELD, ""))
        warnings.extend(f"{RESUME_FIELD}: unreadable entry {b!r} ignored" for b in bad)
    if PAUSE_FIELD in env:
        raw, source = env[PAUSE_FIELD], ENVIRONMENT
    elif stored is None:
        return Controls(True, INVALID, resumes, tuple(warnings))
    else:
        raw, source = stored.get(PAUSE_FIELD, ""), FILE
    paused = _paused(raw)
    if paused is None:
        warnings.append(f"{PAUSE_FIELD}={raw[:20]!r} is not 1 or 0: trading paused")
        return Controls(True, INVALID, resumes, tuple(warnings))
    if not paused and source == FILE:
        source = ""  # trading as by default
    return Controls(paused, source, resumes, tuple(warnings))


RELEASED = "RELEASED"
"""Status of the marker row a release leaves in ``decisions`` (symbol RELEASE_SYMBOL)."""
RELEASE_SYMBOL = "DEPOT"


def last_stop(con: sqlite3.Connection, agent: str) -> str | None:
    """Time of the latest forced sale (RISK_SELL), the visible trace of a stop."""
    row = con.execute(
        "SELECT MAX(ts) FROM trades WHERE agent=? AND side='RISK_SELL'", (agent,)
    ).fetchone()
    stopped: str | None = row[0]
    return stopped


def last_release(con: sqlite3.Connection, agent: str) -> str | None:
    row = con.execute(
        "SELECT MAX(ts) FROM decisions WHERE agent=? AND status=?", (agent, RELEASED)
    ).fetchone()
    released: str | None = row[0]
    return released


NOT_STOPPED = "not stopped"


def release_problem(
    con: sqlite3.Connection, agent: str, requested_at: float, now_epoch: float
) -> str:
    """Why ``release`` would not act on this request ("" if it would); reads only, so the web
    UI (read-only, ADR-0016) shows exactly what the service will do.

    A stop leaves a RISK_SELL only when it had coins to sell; a stop right after a sale to zero
    leaves none. So a request must also be newer than the last release: every applied request
    is used up, and an old one can never lift a later stop (Spec 011 AK5).
    """
    try:
        p = balance(con, agent)
    except KeyError:
        return "unknown depot"
    if not p["halted"]:
        return NOT_STOPPED
    if p["btc"] != 0 or p["eth"] != 0:
        return "the depot still holds coins"
    stopped = last_stop(con, agent)
    if stopped is not None and requested_at <= epoch(stopped):
        return "the request is older than the stop"
    released = last_release(con, agent)
    if released is not None and requested_at <= epoch(released):
        return "the request was already used"
    if requested_at > now_epoch + MAX_FUTURE_SKEW:
        return "the request lies in the future"
    if now_epoch - requested_at > RESUME_MAX_AGE:
        return "the request is older than 24 h"
    return ""


def release(con: sqlite3.Connection, agent: str, requested_at: float, now_epoch: float) -> str:
    """Release a stopped depot; "" if released, else why not (NOT_STOPPED: nothing to do).

    The depot starts again from its cash: after the forced sale it holds no coins, so cash is
    its whole equity and the new peak. A marker row in ``decisions`` (no schema change) records
    the release, shows it in the overview and uses the request up. Caller holds the WriterLock.
    """
    why = release_problem(con, agent, requested_at, now_epoch)
    if why:
        return why
    now = iso(now_epoch)
    with write_tx(con):
        con.execute(
            "UPDATE portfolios SET halted=0, peak=cash WHERE agent=? AND halted=1", (agent,)
        )
        con.execute(
            "INSERT OR IGNORE INTO decisions"
            " (ts,agent,symbol,bar_ts,model,action,target,confidence,reason,inputs,status)"
            " VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?)",
            (
                now,
                agent,
                RELEASE_SYMBOL,
                -int(now_epoch),
                "RISK",
                "HOLD",
                "Risikostopp auf Anforderung freigegeben",
                json.dumps({"requested_at": iso(requested_at)}),
                RELEASED,
            ),
        )
    return ""
