"""What the overview page shows, read from one snapshot of the bot database (Spec 007).

Only SELECTs on a read-only connection (ADR-0016); every function takes the clock as a parameter
so the thresholds can be tested at their exact boundaries. Values are shown as stored: equity is
after fees and slippage because the paper execution books them.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from llm_trading_bot.llm.budget import FREE_STATUSES, charged_today, day_bounds
from llm_trading_bot.llm.due import iso
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.db import START_CASH
from llm_trading_bot.paper.execution import BUYHOLD
from llm_trading_bot.service.status import ServiceStatus

MAX_POINTS = 240
"""Points per equity line; enough for a smooth line, small enough for a quick page."""
RUNNING_SECONDS = 15 * 60
DELAYED_SECONDS = 60 * 60
TRADES = 20

RUNNING, DELAYED, STOPPED = "läuft", "verzögert", "steht"
NO_SERVICE = "kein Dienst"
SERVICE_RUNNING_SECONDS = 3 * 60
"""The service runs a round every minute (ADR-0017): three missed rounds are a delay."""
SERVICE_DELAYED_SECONDS = 15 * 60


def epoch(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def market_label(name: str) -> str:
    """Stored symbol name (``BTCEUR``) to the market shown everywhere else (``BTC-EUR``)."""
    try:
        return Symbol[name].market
    except KeyError:
        return name


@dataclass(frozen=True)
class DepotRow:
    agent: str
    value: float
    cash: float
    btc: float
    eth: float
    at: str | None
    """Time of the equity point; None before the first cycle (then value = cash)."""
    halted: bool
    pnl_eur: float
    pnl_pct: float
    vs_buyhold_pp: float | None
    """Percentage points ahead of (positive) or behind Buy & Hold; None for Buy & Hold itself."""

    @property
    def reference(self) -> bool:
        return self.agent == BUYHOLD


def depots(con: sqlite3.Connection) -> list[DepotRow]:
    rows = con.execute(
        "SELECT p.agent, p.halted, p.cash AS p_cash, p.btc AS p_btc, p.eth AS p_eth,"
        " e.ts, e.value, e.cash, e.btc, e.eth FROM portfolios p"
        " LEFT JOIN equity e ON e.id = (SELECT id FROM equity WHERE agent=p.agent"
        " ORDER BY ts DESC, id DESC LIMIT 1)"
    ).fetchall()
    raw = []
    for r in rows:
        if r["ts"] is None:
            raw.append((r["agent"], r["p_cash"], r["p_cash"], r["p_btc"], r["p_eth"], None, r))
        else:
            raw.append((r["agent"], r["value"], r["cash"], r["btc"], r["eth"], r["ts"], r))
    bench = next((v for agent, v, *_ in raw if agent == BUYHOLD), None)
    bench_pct = None if bench is None else (bench / START_CASH - 1) * 100
    out = []
    for agent, value, cash, btc, eth, at, r in raw:
        pct = (value / START_CASH - 1) * 100
        vs = None if agent == BUYHOLD or bench_pct is None else pct - bench_pct
        out.append(
            DepotRow(
                agent, value, cash, btc, eth, at, bool(r["halted"]), value - START_CASH, pct, vs
            )
        )
    # Trading depots first (alphabetical), the benchmark last as the reference line.
    return sorted(out, key=lambda d: (d.reference, d.agent))


@dataclass(frozen=True)
class Series:
    agent: str
    points: tuple[tuple[float, float], ...]


def _slice(t: float, start: float, span: float, slices: int) -> int:
    """Slice of time ``t``; shared by ``thin`` and the indexed read so both cut identically."""
    return slices - 1 if span <= 0 else min(int((t - start) / span * slices), slices - 1)


def thin(
    points: Sequence[tuple[float, float]], limit: int = MAX_POINTS
) -> list[tuple[float, float]]:
    """At most ``limit`` points: the first one, then the last point of each of ``limit - 1`` equal
    time slices. The newest point always survives because it is the last of the last slice."""
    if len(points) <= limit:
        return list(points)
    if limit < 2:
        raise ValueError("limit must be at least 2")
    start, end = points[0][0], points[-1][0]
    span = end - start
    slices = limit - 1
    kept: dict[int, tuple[float, float]] = {}
    for point in points[1:]:
        kept[_slice(point[0], start, span, slices)] = point  # later points replace earlier ones
    return [points[0], *(kept[i] for i in sorted(kept))]


def _point(row: sqlite3.Row) -> tuple[float, float]:
    return epoch(row["ts"]), float(row["value"])


def _agent_series(con: sqlite3.Connection, agent: str, limit: int) -> list[tuple[float, float]]:
    # LEARNING(#performance): Sich selbst neu ladende Seite darf nicht die ganze Historie lesen
    """Like ``thin`` over the agent's whole history, but read through the (ts, agent) index:
    a handful of rows per slice instead of every row (one row per depot and minute since
    ADR-0017, the page reloads every 15 s). Equity timestamps are whole seconds in one ISO
    format (``due.iso``), so string order is time order."""
    if limit < 2:
        raise ValueError("limit must be at least 2")
    head = con.execute(
        "SELECT ts, value FROM equity WHERE agent=? ORDER BY ts LIMIT ?", (agent, limit + 1)
    ).fetchall()
    if len(head) <= limit:
        return [_point(r) for r in head]
    first = _point(head[0])
    newest = con.execute(
        "SELECT ts FROM equity WHERE agent=? ORDER BY ts DESC LIMIT 1", (agent,)
    ).fetchone()
    start, end = first[0], epoch(newest["ts"])
    span, slices = end - start, limit - 1

    def upper(k: int) -> float:
        """Latest whole second that still falls into slice ``k``."""
        if k >= slices - 1:
            return end
        t = float(int(start + (k + 1) * span / slices))
        while t > start and _slice(t, start, span, slices) > k:
            t -= 1
        while _slice(t + 1, start, span, slices) <= k:
            t += 1
        return t

    kept, after = [first], start
    while True:
        # The next point after the last kept one names the next non-empty slice (gaps of a
        # paused depot are skipped in one step); its last point is the newest up to the slice end.
        found = con.execute(
            "SELECT ts FROM equity WHERE agent=? AND ts>? ORDER BY ts LIMIT 1", (agent, iso(after))
        ).fetchone()
        if found is None:
            break
        k = _slice(epoch(found["ts"]), start, span, slices)
        last = con.execute(
            "SELECT ts, value FROM equity WHERE agent=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (agent, iso(upper(k))),
        ).fetchone()
        kept.append(_point(last))
        after = kept[-1][0]
        if k >= slices - 1:
            break
    return kept


def equity_series(con: sqlite3.Connection, limit: int = MAX_POINTS) -> list[Series]:
    agents = [r["agent"] for r in con.execute("SELECT DISTINCT agent FROM portfolios")]
    series = [Series(a, tuple(points)) for a in agents if (points := _agent_series(con, a, limit))]
    return sorted(series, key=lambda s: (s.agent == BUYHOLD, s.agent))


CYCLE_LABEL = "Letzter Zyklus"
SERVICE_LABEL = "Dienst"


def level(age_seconds: float | None) -> str:
    if age_seconds is None or age_seconds > DELAYED_SECONDS:
        return STOPPED
    return RUNNING if age_seconds <= RUNNING_SECONDS else DELAYED


@dataclass(frozen=True)
class Pulse:
    label: str
    at: str | None
    age_seconds: float | None
    fixed: str | None = None
    """A level decided from more than the age (the service pulse)."""
    note: str = ""

    @property
    def level(self) -> str:
        return self.fixed or level(self.age_seconds)


def service_pulse(status: ServiceStatus | None, now: float, *, problem: str = "") -> Pulse:
    """Spec 008 AK8: from ``service-status.json`` - is the service doing its rounds?"""
    label = SERVICE_LABEL
    if problem:
        return Pulse(label, None, None, STOPPED, problem)
    if status is None:
        return Pulse(label, None, None, NO_SERVICE, "noch nie gestartet")
    at = status.last_round_at or status.updated_at
    age = max(0.0, now - epoch(at))
    note = {
        "starting": "startet",
        "degraded": f"{status.failures} Runde{'' if status.failures == 1 else 'n'} mit Fehler",
        "stalled": "Runde hing, Neustart",
        "stopping": "wird beendet",
        "stopped": "beendet",
    }.get(status.state, "")
    if status.config_warning:
        note = (note + " · " if note else "") + "Einstellungen ungültig"
    if status.state in ("stopped", "stalled", "stopping") or age > SERVICE_DELAYED_SECONDS:
        fixed = STOPPED
    elif status.state == "running" and age <= SERVICE_RUNNING_SECONDS:
        fixed = RUNNING
    else:
        fixed = DELAYED
    return Pulse(label, at, age, fixed, note)


def liveness(con: sqlite3.Connection, now: float) -> list[Pulse]:
    """Last cycle (newest equity point) and the last stored quote of each market."""
    last = con.execute("SELECT MAX(ts) FROM equity").fetchone()[0]
    pulses = [Pulse(CYCLE_LABEL, last, None if last is None else max(0.0, now - epoch(last)))]
    stored = {r["symbol"]: r["ts"] for r in con.execute("SELECT symbol, ts FROM market")}
    for symbol in Symbol:
        ts = stored.get(symbol.name)
        age = None if ts is None else max(0.0, now - epoch(ts))
        pulses.append(Pulse(f"Kurs {symbol.market}", ts, age))
    return pulses


@dataclass(frozen=True)
class Quote:
    """Last stored quote of a market, as the bot wrote it in its last round."""

    market: str
    ts: str
    bid: float
    ask: float
    last_close: float
    """Close of the last finished candle - what the strategies see."""
    source: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid * 100 if self.mid > 0 else 0.0


def quotes(con: sqlite3.Connection) -> list[Quote]:
    """In the order of ``Symbol``; markets the bot never priced are left out."""
    rows = {
        r["symbol"]: r
        for r in con.execute("SELECT symbol, ts, bid, ask, last_close, source FROM market")
    }
    return [
        Quote(s.market, r["ts"], r["bid"], r["ask"], r["last_close"], r["source"])
        for s in Symbol
        if (r := rows.get(s.name)) is not None
    ]


@dataclass(frozen=True)
class DecisionRow:
    agent: str
    market: str
    ts: str
    action: str
    target: float | None
    confidence: float | None
    status: str
    reason: str


def latest_decisions(con: sqlite3.Connection) -> list[DecisionRow]:
    rows = con.execute(
        "SELECT d.* FROM decisions d JOIN (SELECT agent, symbol, MAX(id) AS id FROM decisions"
        " GROUP BY agent, symbol) m ON d.id = m.id"
    ).fetchall()
    out = [
        DecisionRow(
            r["agent"],
            market_label(r["symbol"]),
            r["ts"],
            r["action"],
            r["target"],
            r["confidence"],
            r["status"],
            r["reason"],
        )
        for r in rows
    ]
    return sorted(out, key=lambda d: (d.agent == BUYHOLD, d.agent, d.market))


@dataclass(frozen=True)
class LlmToday:
    agent: str
    requests: int
    charged: Decimal
    """Known costs plus open reservations, exactly what the budget lock counts (UTC day)."""
    share: Decimal
    exceeded: bool
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    last_status: str | None
    last_error: str | None
    last_at: str | None


def llm_today(con: sqlite3.Connection, now: float, shares: Mapping[str, Decimal]) -> list[LlmToday]:
    """One row per GPT depot in ``shares`` that has a portfolio, plus any other agent that
    called GPT today."""
    # Same UTC day bounds as charged_today, so counts and costs always cover the same calls.
    start, end = day_bounds(now)
    agents = {r["agent"] for r in con.execute("SELECT agent FROM portfolios")}
    called = {
        r["agent"]
        for r in con.execute(
            "SELECT DISTINCT agent FROM llm_calls WHERE ts>=? AND ts<?", (start, end)
        )
    }
    names = sorted((set(shares) & agents) | called)
    out = []
    for agent in names:
        charged, exceeded = charged_today(con, now, agent)
        totals = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
            " COALESCE(SUM(reasoning_tokens),0) FROM llm_calls"
            " WHERE agent=? AND ts>=? AND ts<? AND status NOT IN (?,?)",
            (agent, start, end, *FREE_STATUSES),
        ).fetchone()
        last = con.execute(
            "SELECT ts, status, error FROM llm_calls WHERE agent=? ORDER BY id DESC LIMIT 1",
            (agent,),
        ).fetchone()
        out.append(
            LlmToday(
                agent,
                int(totals[0]),
                charged,
                shares.get(agent, Decimal(0)),
                exceeded,
                int(totals[1]),
                int(totals[2]),
                int(totals[3]),
                None if last is None else last["status"],
                None if last is None else last["error"],
                None if last is None else last["ts"],
            )
        )
    return out


@dataclass(frozen=True)
class TradeRow:
    ts: str
    agent: str
    market: str
    side: str
    qty: float
    fill: float
    notional: float
    fee: float


def recent_trades(con: sqlite3.Connection, limit: int = TRADES) -> list[TradeRow]:
    rows = con.execute(
        "SELECT ts, agent, symbol, side, qty, fill, notional, fee FROM trades"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        TradeRow(
            r["ts"],
            r["agent"],
            market_label(r["symbol"]),
            r["side"],
            r["qty"],
            r["fill"],
            r["notional"],
            r["fee"],
        )
        for r in rows
    ]


@dataclass(frozen=True)
class Overview:
    depots: list[DepotRow]
    series: list[Series]
    pulses: list[Pulse]
    decisions: list[DecisionRow]
    llm: list[LlmToday]
    trades: list[TradeRow]
    quotes: list[Quote] = field(default_factory=list)


def load(
    con: sqlite3.Connection,
    now: float,
    shares: Mapping[str, Decimal],
    *,
    service: Pulse | None = None,
) -> Overview:
    return Overview(
        depots(con),
        equity_series(con),
        ([service] if service else []) + liveness(con, now),
        latest_decisions(con),
        llm_today(con, now, shares),
        recent_trades(con),
        quotes(con),
    )
