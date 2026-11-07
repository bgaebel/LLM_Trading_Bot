"""Trading pause and release of stopped depots (Spec 011 AK5/AK6, ADR-0021).

Both are requested in the web UI, which only writes ``secrets.env`` (ADR-0015/0016); the service
reads them every round and is the only one to change the database (ADR-0017).

- ``BOT_PAUSED=1``: no trades except the drawdown stop's forced sale. Fail closed: a file that
  cannot be read or a value that is not ``1``/``0``/empty means pause. The environment wins.
- ``BOT_RESUME=<depot>@<ISO time>,...``: release a stopped depot, at most once per stop and only
  for a request that is newer than the depot's last trade and at most RESUME_MAX_AGE old.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from llm_trading_bot.paper.db import balance, write_tx
from llm_trading_bot.web.credentials import CONTROL_FIELDS, load_env_file

PAUSE_FIELD, RESUME_FIELD = CONTROL_FIELDS
RESUME_MAX_AGE = 24 * 3600
"""A request older than a day is not acted on: the user may have forgotten why they asked."""

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
            if not sep or not agent or datetime.fromisoformat(ts).tzinfo is None:
                raise ValueError(item)
            at = epoch(ts)
        except (ValueError, OSError, OverflowError):
            # OSError/OverflowError: out-of-range times on some platforms (e.g. Windows)
            bad.append(item[:80])
            continue
        out[agent] = max(at, out.get(agent, at))
    return out, bad


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
        # Say what follows from it: with BOT_PAUSED in the environment only releases are lost.
        effect = "release requests ignored" if PAUSE_FIELD in env else "trading paused"
        warnings.append(f"{secrets.name} unreadable ({type(exc).__name__}): {effect}")
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
        # From the environment it is still the environment that decides: the web switch
        # cannot change it, and the page must say so.
        return Controls(
            True, ENVIRONMENT if source == ENVIRONMENT else INVALID, resumes, tuple(warnings)
        )
    if not paused and source == FILE:
        source = ""  # trading as by default
    return Controls(paused, source, resumes, tuple(warnings))


def last_trade(con: sqlite3.Connection, agent: str) -> str | None:
    row = con.execute("SELECT MAX(ts) FROM trades WHERE agent=?", (agent,)).fetchone()
    traded: str | None = row[0]
    return traded


NOT_STOPPED = "not stopped"


def release_problem(
    con: sqlite3.Connection, agent: str, requested_at: float, now_epoch: float
) -> str:
    """Why ``release`` would not act on this request ("" if it would); reads only, so the web
    UI (read-only, ADR-0016) shows exactly what the service will do.

    The request must be newer than the depot's last trade. A stop leaves a RISK_SELL only when
    there were coins to sell, but a released depot starts from cash alone and can only fall
    again after it traded - so every later stop comes after a trade newer than any request
    that released an earlier one, and an old request never lifts a later stop (Spec 011 AK5).
    No marker rows are needed in the trade history.
    """
    try:
        p = balance(con, agent)
    except KeyError:
        return "unknown depot"
    if not p["halted"]:
        return NOT_STOPPED
    if p["btc"] != 0 or p["eth"] != 0:
        return "the depot still holds coins"
    traded = last_trade(con, agent)
    if traded is not None and requested_at <= epoch(traded):
        return "the request is not newer than the depot's last trade"
    if requested_at > now_epoch:
        # Web UI and service share one clock (ADR-0005): a request ahead of it is broken.
        return "the request lies in the future"
    if now_epoch - requested_at > RESUME_MAX_AGE:
        return "the request is older than 24 h"
    return ""


def release(con: sqlite3.Connection, agent: str, requested_at: float, now_epoch: float) -> str:
    """Release a stopped depot; "" if released, else why not (NOT_STOPPED: nothing to do).

    The depot starts again from its cash: after the forced sale it holds no coins, so cash is
    its whole equity and the new peak. Caller holds the WriterLock.
    """
    why = release_problem(con, agent, requested_at, now_epoch)
    if why:
        return why
    with write_tx(con):
        con.execute(
            "UPDATE portfolios SET halted=0, peak=cash WHERE agent=? AND halted=1", (agent,)
        )
    return ""
