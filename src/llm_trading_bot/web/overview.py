"""What the overview page shows, read from one snapshot of the bot database (Spec 007).

Only SELECTs on a read-only connection (ADR-0016); every function takes the clock as a parameter
so the thresholds can be tested at their exact boundaries. Values are shown as stored: equity is
after fees and slippage because the paper execution books them.
"""

import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from llm_trading_bot.llm.budget import FREE_STATUSES, charged_today, day_bounds
from llm_trading_bot.llm.due import iso
from llm_trading_bot.market.models import QUOTE_MARKETS, SYMBOLS, Symbol
from llm_trading_bot.paper.db import START_CASH
from llm_trading_bot.paper.execution import BENCHMARKS, BUYHOLD
from llm_trading_bot.paper.usdc import BUYHOLD_USDC, is_usdc, route_markets
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
ACTIVE_SECONDS = 5 * 60
"""Spec 021 (ADR-0031): a depot is shown while its newest equity point is at most this much
older than the newest round in which it could have been valued: the newest equity point of the
database, or the latest stored quote of a market it holds if that is older. The web cannot see
which depots the bot runs (ADR-0016); a running depot gets a point in every round that has fresh
quotes for all it holds, so a quote outage of one market never hides the depots holding it,
however long it lasts. Spec 022 (ADR-0032): a switched-off depot leaves after five rounds
(one a minute, ADR-0017) instead of an hour - a few missed rounds of a running depot (a slow
round, a restart) stay inside it."""
REFERENCES = (BUYHOLD_USDC, BUYHOLD)
"""Spec 021: the reference of the "vs." column, preferred first, among the shown depots."""


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
    """Percentage points ahead of (positive) or behind the reference since the reference's
    start (Spec 021); None for the reference itself, without reference, or for a depot without
    an equity point at that start."""
    from_peak_pct: float = 0.0
    """Equity against the depot's all-time peak in percent (<= 0); ADR-0021 stops at -10 %."""
    stopped_at: str | None = None
    """Time of the depot's latest RISK_SELL (the stop); None if it never had one."""
    active: bool = True
    """Valued recently enough to be shown (``ACTIVE_SECONDS``, Spec 021)."""

    @property
    def reference(self) -> bool:
        """Buy & hold and its USDC twin (Spec 020): never stopped, not ranked as traders."""
        return self.agent in BENCHMARKS

    @property
    def usdc(self) -> bool:
        """Trades its BTC over BTC-USDC (Spec 020); shown with a "USDC" badge."""
        return is_usdc(self.agent)


@dataclass(frozen=True)
class Reference:
    """What the "vs." column compares with (Spec 021, ADR-0031)."""

    agent: str
    since: str
    """Time of the reference's first equity point: the comparison starts there."""
    start: float
    """The reference's equity at ``since``."""


def depots(con: sqlite3.Connection) -> list[DepotRow]:
    """Every depot with a portfolio, also switched-off ones (the release of a stopped depot is
    offered for each of them, Spec 011); ``active`` says which the overview shows."""
    return _depots(con)[0]


def _reference(con: sqlite3.Connection, rows: Sequence[DepotRow]) -> Reference | None:
    shown = {d.agent for d in rows if d.active}
    agent = next((a for a in REFERENCES if a in shown), None)
    if agent is None:
        return None
    # Through the (ts, agent) index; rows before the reference's start are a fixed number.
    first = con.execute(
        "SELECT ts, value FROM equity WHERE agent=? ORDER BY ts LIMIT 1", (agent,)
    ).fetchone()
    if first is None or not first["value"] > 0:
        return None
    return Reference(agent, first["ts"], float(first["value"]))


def _vs(con: sqlite3.Connection, d: DepotRow, ref: Reference, ref_now: float) -> float | None:
    """Both returns over the same span: from the reference's first point until now."""
    if d.agent == ref.agent or d.at is None or not d.active:
        return None
    base = con.execute(
        "SELECT value FROM equity WHERE agent=? AND ts<=? ORDER BY ts DESC LIMIT 1",
        (d.agent, ref.since),
    ).fetchone()
    if base is None or not base["value"] > 0:
        return None
    return (d.value / float(base["value"]) - 1) * 100 - (ref_now / ref.start - 1) * 100


def _active_since(
    agent: str, r: sqlite3.Row, newest: str | None, quoted: Mapping[str, str]
) -> str | None:
    """Oldest equity stamp that still counts as active (``ACTIVE_SECONDS``); None: shown anyway.

    The bot values a depot only in rounds with a fresh quote for every market it holds
    (``paper.cycle``, Spec 011 AK8), so the bound is the newest round of the database, or the
    latest stored quote of a held market if that is older. Without it an outage of one market
    longer than an hour would hide every depot holding it, the reference included.
    """
    if newest is None:
        return None  # before the first round every depot is shown at its start cash
    bound = newest
    for symbol, held in ((Symbol.BTCEUR, r["p_btc"]), (Symbol.ETHEUR, r["p_eth"])):
        if held == 0:
            continue
        for market in route_markets(agent, symbol):
            ts = quoted.get(market.name)
            if ts is None:
                return None  # never quoted: the depot could not have been valued since
            # Quote and equity stamps are both due.iso: string order is time order.
            bound = min(bound, ts)
    return iso(epoch(bound) - ACTIVE_SECONDS)


def _depots(con: sqlite3.Connection) -> tuple[list[DepotRow], Reference | None]:
    rows = con.execute(
        "SELECT p.agent, p.halted, p.peak, p.cash AS p_cash, p.btc AS p_btc, p.eth AS p_eth,"
        # The stop's forced sale is the depot's last trade; a stop without coins to sell has
        # none, and an older forced sale belongs to an earlier stop (Spec 011).
        " (SELECT CASE WHEN side='RISK_SELL' THEN ts END FROM trades WHERE agent=p.agent"
        " ORDER BY id DESC LIMIT 1) AS stopped_at,"
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
    # Equity stamps are whole seconds in one ISO format (due.iso): string order is time order.
    stamps: list[str] = [r["ts"] for r in rows if r["ts"] is not None]
    newest = max(stamps, default=None)
    quoted = {m["symbol"]: m["ts"] for m in con.execute("SELECT symbol, ts FROM market")}
    out = []
    for agent, value, cash, btc, eth, at, r in raw:
        since = _active_since(agent, r, newest, quoted)
        pct = (value / START_CASH - 1) * 100
        peak = r["peak"]
        out.append(
            DepotRow(
                agent,
                value,
                cash,
                btc,
                eth,
                at,
                bool(r["halted"]),
                value - START_CASH,
                pct,
                None,
                # The peak includes the latest point (mark updates both), so this is <= 0.
                (value / peak - 1) * 100 if peak > 0 else 0.0,
                r["stopped_at"],
                # Before the first round no depot has a point: all are shown at their start cash.
                since is None or (at is not None and at >= since),
            )
        )
    ref = _reference(con, out)
    if ref is not None:
        ref_now = next(d.value for d in out if d.agent == ref.agent)
        out = [replace(d, vs_buyhold_pp=_vs(con, d, ref, ref_now)) for d in out]
    # Trading depots first (alphabetical), the benchmarks last as the reference lines.
    return sorted(out, key=lambda d: (d.reference, d.agent)), ref


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


def equity_series(
    con: sqlite3.Connection, limit: int = MAX_POINTS, agents: Collection[str] | None = None
) -> list[Series]:
    """One line per depot in ``agents`` (default: every depot with a portfolio)."""
    if agents is None:
        agents = [r["agent"] for r in con.execute("SELECT DISTINCT agent FROM portfolios")]
    series = [Series(a, tuple(points)) for a in agents if (points := _agent_series(con, a, limit))]
    return sorted(series, key=lambda s: (s.agent in BENCHMARKS, s.agent))


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
    gap = data_gap(status)
    if gap:
        note = gap
    if status.config_warning:
        note = (note + " · " if note else "") + "Einstellungen ungültig"
    if status.state in ("stopped", "stalled", "stopping") or age > SERVICE_DELAYED_SECONDS:
        fixed = STOPPED
    elif (status.state == "running" or gap) and age <= SERVICE_RUNNING_SECONDS:
        # A round that only lacked exchange data ran on time: the bot runs (user 2026-11-07).
        fixed = RUNNING
    else:
        fixed = DELAYED
    return Pulse(label, at, age, fixed, note)


def data_gap(status: ServiceStatus) -> str:
    """The note for a degraded round whose reported problem is only market data from Bitvavo
    (a missing minute, stale or unusable data), else "". The status file names the first
    problem of the round: "<depot> <market>: MarketDataError: ..." or "quotes: ..."."""
    if status.state != "degraded" or status.last_result != "incomplete":
        return ""
    head, sep, rest = status.last_error.partition(": ")
    if not sep or not rest.startswith("MarketDataError:"):
        return ""
    if head == "quotes":
        return "Kurs von Bitvavo unbrauchbar, der Markt bleibt ohne Entscheidung"
    market = head.rpartition(" ")[2]
    if "gap in recent" in rest:
        return f"{market} ohne Entscheidung: Marktdaten-Lücke bei Bitvavo"
    return f"{market} ohne Entscheidung: Marktdaten von Bitvavo unbrauchbar"


def _quote_only_shown(con: sqlite3.Connection, now: float) -> set[str]:
    """Spec 020: the quote-only markets (BTC-USDC, USDC-EUR) the overview shows.

    The bot stores them only while a USDC depot runs. Switched off, their last row stays
    forever and would read as a market-data failure the bot does not have (on the public view
    too). So: shown while a USDC depot got an equity point in the newest round (in use: a stale
    quote is a real problem then), or while the quote is younger than DELAYED_SECONDS (an
    outage that also cost the USDC depots their equity point stays visible for that long).
    """
    stored = {r["symbol"]: r["ts"] for r in con.execute("SELECT symbol, ts FROM market")}
    newest = con.execute(
        "SELECT agent FROM equity WHERE ts = (SELECT MAX(ts) FROM equity)"
    ).fetchall()
    in_use = any(is_usdc(r["agent"]) for r in newest)
    return {
        s.name
        for s in QUOTE_MARKETS
        if (ts := stored.get(s.name)) is not None and (in_use or now - epoch(ts) <= DELAYED_SECONDS)
    }


def liveness(con: sqlite3.Connection, now: float) -> list[Pulse]:
    """Last cycle (newest equity point) and the last stored quote of each market."""
    last = con.execute("SELECT MAX(ts) FROM equity").fetchone()[0]
    pulses = [Pulse(CYCLE_LABEL, last, None if last is None else max(0.0, now - epoch(last)))]
    stored = {r["symbol"]: r["ts"] for r in con.execute("SELECT symbol, ts FROM market")}
    quote_only = _quote_only_shown(con, now)
    shown = (*SYMBOLS, *(s for s in QUOTE_MARKETS if s.name in quote_only))
    for symbol in shown:
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


def quotes(con: sqlite3.Connection, now: float) -> list[Quote]:
    """In the order of ``Symbol``; markets the bot never priced are left out, and quote-only
    markets by the same rule as in ``liveness`` (no frozen BTC-USDC price in the ticker)."""
    rows = {
        r["symbol"]: r
        for r in con.execute("SELECT symbol, ts, bid, ask, last_close, source FROM market")
    }
    shown = {s.name for s in SYMBOLS} | _quote_only_shown(con, now)
    return [
        Quote(s.market, r["ts"], r["bid"], r["ask"], r["last_close"], r["source"])
        for s in Symbol
        if s.name in shown and (r := rows.get(s.name)) is not None
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


DECISION_WINDOW = 50_000
"""Newest decisions searched for signals: about three months at ~500 decisions a day."""


def latest_decisions(con: sqlite3.Connection) -> list[DecisionRow]:
    """Per depot and market the newest BUY or SELL; holds are the norm and only crowd the table
    (user 2026-11-08). A signal older than the window is not shown - the trades still are."""
    # LEARNING(#performance): Sich selbst neu ladende Seite darf nicht die ganze Historie lesen
    # MATERIALIZED: else SQLite groups over the (agent, symbol, bar_ts) index - the whole table.
    rows = con.execute(
        "WITH w AS MATERIALIZED (SELECT id, agent, symbol FROM decisions"
        " WHERE id > (SELECT COALESCE(MAX(id), 0) FROM decisions) - ?"
        " AND action IN ('BUY', 'SELL'))"
        " SELECT d.* FROM decisions d JOIN (SELECT MAX(id) AS id FROM w GROUP BY agent, symbol) m"
        " ON d.id = m.id",
        (DECISION_WINDOW,),
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
    return sorted(out, key=lambda d: (d.agent in BENCHMARKS, d.agent, d.market))


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


def recent_trades(
    con: sqlite3.Connection, limit: int = TRADES, agents: Collection[str] | None = None
) -> list[TradeRow]:
    """The newest trades of ``agents`` (default: of every depot)."""
    where = ""
    args: tuple[str, ...] = ()
    if agents is not None:
        names = sorted(agents)
        if not names:
            return []
        where, args = f" WHERE agent IN ({','.join('?' for _ in names)})", tuple(names)
    rows = con.execute(
        "SELECT ts, agent, symbol, side, qty, fill, notional, fee FROM trades"
        f"{where} ORDER BY id DESC LIMIT ?",
        (*args, limit),
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
    reference: Reference | None = None
    """What the "vs." column compares with (Spec 021); None: no reference shown."""


@dataclass
class SignalCache:
    """``latest_decisions`` reads the full rows of its window (no index covers ``action``): about
    0.1 s at 50 000 decisions. New decisions come once a closed bar, the page every few seconds -
    so the rows are kept until the newest decision changes (Spec 019 review)."""

    key: tuple[int, str] | None = None
    rows: list[DecisionRow] = field(default_factory=list)

    def get(self, con: sqlite3.Connection) -> list[DecisionRow]:
        # Id and time of the newest row: a restored or replaced database with the same newest id
        # but another history still reads again.
        newest = con.execute("SELECT id, ts FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        key = None if newest is None else (int(newest[0]), str(newest[1]))
        if key is None or key != self.key:
            self.rows = latest_decisions(con) if key is not None else []
            self.key = key
        return list(self.rows)


def load(
    con: sqlite3.Connection,
    now: float,
    shares: Mapping[str, Decimal],
    *,
    service: Pulse | None = None,
    signals: SignalCache | None = None,
) -> Overview:
    """Spec 021: only active depots, in every list (also on the public view, ADR-0028); the
    history of the others stays in the database."""
    rows, ref = _depots(con)
    shown = {d.agent for d in rows if d.active}
    decisions = latest_decisions(con) if signals is None else signals.get(con)
    return Overview(
        [d for d in rows if d.active],
        equity_series(con, agents=sorted(shown)),
        ([service] if service else []) + liveness(con, now),
        [d for d in decisions if d.agent in shown],
        # Spec 022: also the GPT costs - a switched-off GPT depot would add its share to the
        # day's budget shown at the top.
        [r for r in llm_today(con, now, shares) if r.agent in shown],
        recent_trades(con, agents=shown),
        quotes(con, now),
        ref,
    )
