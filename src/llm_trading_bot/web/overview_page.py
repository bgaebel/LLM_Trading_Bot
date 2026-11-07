"""HTML of the overview page (Spec 007). Same rules as ``pages``: every value is escaped (decision
reasons come from the LLM), no script, no style attribute - the equity chart is SVG with
attributes only. The real chart says so, to stay apart from the "DEMO" backdrop."""

from collections.abc import Sequence
from datetime import UTC, datetime
from html import escape

from llm_trading_bot.paper.db import START_CASH
from llm_trading_bot.paper.execution import BUYHOLD
from llm_trading_bot.web import pages
from llm_trading_bot.web.overview import (
    CYCLE_LABEL,
    DELAYED,
    RUNNING,
    SERVICE_LABEL,
    DecisionRow,
    DepotRow,
    LlmToday,
    Overview,
    Pulse,
    Series,
    TradeRow,
    epoch,
)

REFRESH_SECONDS = 15
"""Fallback reload without scripts (``<noscript>``); with them /app.js updates live (Spec 010).
Not 60 s: in lockstep with the 60-s rounds every load would show the same age (Spec 008)."""
CHART_W, CHART_H = 800, 260
PLOT = (64, 16, 788, 222)
"""Left, top, right, bottom of the plot area inside the chart's viewBox."""
NBSP = " "


def money(value: float, places: int = 2) -> str:
    return f"{value:,.{places}f}".replace(",", NBSP) + " €"


def signed(value: float, unit: str, places: int = 2) -> str:
    return f"{value:+,.{places}f}".replace(",", NBSP) + unit


def when(ts: str | None, *, seconds: bool = False) -> str:
    if ts is None:
        return "–"
    return datetime.fromisoformat(ts).strftime(f"%Y-%m-%d %H:%M{':%S' if seconds else ''} UTC")


def age(seconds: float | None) -> str:
    if seconds is None:
        return "nie"
    if seconds < 90:
        return f"vor {int(seconds)} s"
    if seconds < 90 * 60:
        return f"vor {round(seconds / 60)} min"
    if seconds < 48 * 3600:
        return f"vor {round(seconds / 3600)} h"
    return f"vor {round(seconds / 86400)} Tagen"


def _tone(level: str) -> str:
    return "ok" if level == RUNNING else "warn" if level == DELAYED else "bad"


def _table(head: Sequence[str], rows: Sequence[Sequence[str]], cls: str = "") -> str:
    """Cells are already escaped HTML; the wrapper scrolls on narrow screens, not the page."""
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (
        f'<div class="scroll"><table class="data {cls}"><thead><tr>{th}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _age_html(at: str | None, seconds: float | None) -> str:
    """The age as text, plus its epoch for /app.js to count on every second (Spec 010)."""
    if at is None or seconds is None:
        return escape(age(seconds))
    return f'<span data-at="{int(epoch(at))}">{escape(age(seconds))}</span>'


def _pulses(pulses: Sequence[Pulse]) -> str:
    items = "".join(
        f'<div class="pulse-row {_tone(p.level)}"><i class="led"></i><span>{escape(p.label)}</span>'
        # Seconds: a round a minute later must show as a new time, not as the same minute.
        f"<b>{escape(p.level)}</b><small>{escape(when(p.at, seconds=True))} · "
        f"{_age_html(p.at, p.age_seconds)}"
        + (f" · {escape(p.note)}" if p.note else "")
        + "</small></div>"
        for p in pulses
    )
    return f'<div class="pulses">{items}</div>'


def _depots(rows: Sequence[DepotRow]) -> str:
    def name(d: DepotRow) -> str:
        badges = ' <span class="badge">Referenz</span>' if d.reference else ""
        badges += ' <span class="badge stop">gestoppt</span>' if d.halted else ""
        return f"<b>{escape(d.agent)}</b>{badges}"

    def gain(value: float, text: str) -> str:
        return f'<span class="{"up" if value >= 0 else "down"}">{escape(text)}</span>'

    body = [
        (
            name(d),
            escape(money(d.value)),
            gain(d.pnl_eur, f"{signed(d.pnl_eur, ' €')} ({signed(d.pnl_pct, ' %')})"),
            "–"
            if d.vs_buyhold_pp is None
            else gain(d.vs_buyhold_pp, signed(d.vs_buyhold_pp, " pp")),
            escape(money(d.cash)),
            escape(f"{d.btc:.6f}"),
            escape(f"{d.eth:.6f}"),
            escape(when(d.at)),
        )
        for d in rows
    ]
    head = ("Depot", "Equity", "seit Start", "vs. Buy &amp; Hold", "Cash", "BTC", "ETH", "Stand")
    return _table(head, body, "depots")


def equity_chart(series: Sequence[Series]) -> str:
    drawn = [s for s in series if s.points]
    times = [t for s in drawn for t, _ in s.points]
    if not times or max(times) == min(times):
        return '<p class="hint">Noch kein Verlauf – dafür braucht es mindestens zwei Zyklen.</p>'
    t0, t1 = min(times), max(times)
    values = [v for s in drawn for _, v in s.points] + [START_CASH]
    low, high = min(values), max(values)
    pad = max((high - low) * 0.08, 1.0)
    low, high = low - pad, high + pad
    left, top, right, bottom = PLOT

    def x(t: float) -> float:
        return round(left + (t - t0) / (t1 - t0) * (right - left), 1)

    def y(v: float) -> float:
        return round(bottom - (v - low) / (high - low) * (bottom - top), 1)

    lines = []
    legend = []
    for n, s in enumerate(drawn):
        tone = "bench" if s.agent == BUYHOLD else f"c{n % 4}"
        pts = " ".join(f"{x(t)},{y(v)}" for t, v in s.points)
        lines.append(f'<polyline class="eq-line {tone}" points="{pts}"/>')
        last_t, last_v = s.points[-1]
        lines.append(f'<circle class="eq-dot {tone}" cx="{x(last_t)}" cy="{y(last_v)}" r="3"/>')
        legend.append(
            f'<span class="eq-key {tone}">{escape(s.agent)} {escape(money(last_v))}</span>'
        )
    start_y = y(START_CASH)
    grid = "".join(
        f'<line class="eq-grid" x1="{left}" x2="{right}" y1="{gy}" y2="{gy}"/>'
        for gy in (top, (top + bottom) / 2, bottom)
    )
    labels = (
        f'<text class="eq-axis" x="{left - 8}" y="{top + 4}" text-anchor="end">'
        f"{escape(f'{high:,.0f}'.replace(',', NBSP))}</text>"
        f'<text class="eq-axis" x="{left - 8}" y="{bottom + 4}" text-anchor="end">'
        f"{escape(f'{low:,.0f}'.replace(',', NBSP))}</text>"
        f'<text class="eq-axis" x="{left}" y="{bottom + 22}">{escape(when_epoch(t0))}</text>'
        f'<text class="eq-axis" x="{right}" y="{bottom + 22}" text-anchor="end">'
        f"{escape(when_epoch(t1))}</text>"
        f'<line class="eq-start" x1="{left}" x2="{right}" y1="{start_y}" y2="{start_y}"/>'
        f'<text class="eq-axis start" x="{right - 4}" y="{start_y - 5}" text-anchor="end">'
        f"Start {escape(money(START_CASH, 0))}</text>"
    )
    return (
        f'<svg class="equity" viewBox="0 0 {CHART_W} {CHART_H}" role="img" '
        'aria-label="Equity-Verlauf aller Depots (echte Paper-Daten)">'
        f"{grid}{labels}{''.join(lines)}</svg>"
        f'<div class="eq-legend">{"".join(legend)}</div>'
    )


def when_epoch(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%d.%m. %H:%M UTC")


def _decisions(rows: Sequence[DecisionRow]) -> str:
    def action(d: DecisionRow) -> str:
        return f'<span class="act {escape(d.action.lower())}">{escape(d.action)}</span>'

    def number(value: float | None, places: int = 2) -> str:
        return "–" if value is None else escape(f"{value:.{places}f}")

    body = [
        (
            f"<b>{escape(d.agent)}</b>",
            escape(d.market),
            escape(when(d.ts)),
            action(d),
            number(d.target),
            number(d.confidence),
            f'<span class="st {"ok" if d.status == "OK" else "warn"}">{escape(d.status)}</span>',
            f'<span class="reason">{escape(d.reason)}</span>',
        )
        for d in rows
    ]
    head = ("Depot", "Markt", "Zeit", "Aktion", "Ziel", "Konf.", "Status", "Begründung")
    return _table(head, body, "decisions")


def _llm(rows: Sequence[LlmToday]) -> str:
    if not rows:
        return '<p class="hint">Keine GPT-Depots aktiv.</p>'
    cards = []
    for r in rows:
        share = r.share if r.share > 0 else None
        used = 0.0 if share is None else min(float(r.charged / share), 1.0)
        status = escape(r.last_status or "–") + (
            f" · {escape(r.last_error)}" if r.last_error else ""
        )
        cards.append(
            f'<div class="llm-card{" over" if r.exceeded else ""}">'
            f'<div class="llm-top"><b>{escape(r.agent)}</b>'
            f"<span>{r.requests} {'Anfrage' if r.requests == 1 else 'Anfragen'}</span></div>"
            f'<svg class="meter" viewBox="0 0 200 8" role="img" aria-label="Budget verbraucht">'
            f'<rect class="meter-bed" width="200" height="8"/>'
            f'<rect class="meter-fill" width="{round(used * 200, 1)}" height="8"/></svg>'
            f'<div class="llm-cost">{escape(f"{r.charged:.4f}")} € von '
            f"{escape('–' if share is None else f'{share:.2f}')} €</div>"
            f'<div class="llm-meta">Tokens {r.input_tokens} rein · {r.output_tokens} raus · '
            f"{r.reasoning_tokens} Reasoning</div>"
            f'<div class="llm-meta">Letzte Anfrage: {status} · {escape(when(r.last_at))}</div>'
            "</div>"
        )
    return (
        f'<div class="llm-cards">{"".join(cards)}</div>'
        '<p class="hint">Heute = UTC-Tag wie die Budgetsperre; Kosten inkl. offener '
        "Reservierungen. "
        "Anteile aus <code>secrets.env</code> bzw. Standardwerten – setzt der laufende Bot andere "
        "Werte per Umgebung, weicht die Anzeige ab.</p>"
    )


def _trades(rows: Sequence[TradeRow]) -> str:
    if not rows:
        return '<p class="hint">Noch keine virtuellen Trades.</p>'
    body = [
        (
            escape(when(t.ts)),
            f"<b>{escape(t.agent)}</b>",
            escape(t.market),
            f'<span class="act {"buy" if t.side == "BUY" else "sell"}">{escape(t.side)}</span>',
            escape(f"{t.qty:.6f}"),
            escape(money(t.fill)),
            escape(money(t.notional)),
            escape(money(t.fee, 4)),
        )
        for t in rows
    ]
    return _table(("Zeit", "Depot", "Markt", "Seite", "Menge", "Kurs", "Betrag", "Gebühr"), body)


def _number(value: float) -> str:
    return f"{value:,.2f}".replace(",", NBSP)


def ticker(view: Overview) -> list[tuple[str, str]]:
    """Strip under the header: the stored quotes and every depot, instead of fixed hints."""
    items: list[tuple[str, str]] = []
    for q in view.quotes:
        base = q.market.split("-")[0]
        items += [
            (q.market, money(q.mid)),
            (f"{base} Geld / Brief", f"{_number(q.bid)} / {_number(q.ask)}"),
            (f"{base} Spread", f"{q.spread_pct:.3f} %"),
            (f"{base} Kerzenschluss", money(q.last_close)),
        ]
    if view.quotes:
        newest = max(view.quotes, key=lambda q: q.ts)
        items.append(("Kurse", f"{when(newest.ts, seconds=True)} · {newest.source}"))
    for d in view.depots:
        label = "Buy & Hold" if d.reference else d.agent
        items.append((label, f"{money(d.value)} · {signed(d.pnl_pct, ' %')}"))
    return [*items, ("Modus", "Paper – kein Echtgeld")] if items else list(pages.TICKER)


def _readouts(view: Overview) -> str:
    # By label, not position: the service pulse (Spec 008) comes first when there is a status file.
    cycle = next((p for p in view.pulses if p.label == CYCLE_LABEL), None)
    service = next((p for p in view.pulses if p.label == SERVICE_LABEL), None)
    bot = service or cycle
    # Only depots valued in the newest round: a deselected depot keeps its last, older value.
    traders = [d for d in view.depots if not d.reference]
    newest = max((d.at for d in traders if d.at), default=None)
    current = [d for d in traders if newest is None or d.at == newest]
    leader = max(current, key=lambda d: d.value, default=None)
    charged = sum((r.charged for r in view.llm), start=0) if view.llm else 0
    budget = sum((r.share for r in view.llm), start=0) if view.llm else 0
    cells = [
        # Values are HTML here: every text goes through escape (the age through _age_html).
        ("Bot", escape(bot.level) if bot else "–", "" if bot and bot.level == RUNNING else "off"),
        ("Letzter Zyklus", _age_html(cycle.at, cycle.age_seconds) if cycle else "–", ""),
        (
            "Bestes Depot",
            "–" if leader is None else escape(f"{leader.agent} {signed(leader.pnl_pct, ' %')}"),
            "",
        ),
        ("GPT heute", escape(f"{charged:.2f} / {budget:.2f} €"), ""),
    ]
    return (
        '<div class="readouts">'
        + "".join(
            f'<div class="readout"><small>{escape(label)}</small>'
            f"<b{' class="off"' if off else ''}>{value}</b></div>"
            for label, value, off in cells
        )
        + "</div>"
    )


def overview_page(
    view: Overview | None, *, csrf: str, problem: str = "", now: float | None = None
) -> str:
    """``view`` is None when the database cannot be shown; ``problem`` then says why. ``now``
    keeps the ticker running across reloads."""
    if view is None:
        body = (
            '<section class="panel">'
            + pages._head("00", "Übersicht", "amber")
            + f'<div class="msg bad" role="status">{escape(problem)}</div>'
            '<p class="hint">Die Übersicht liest die Datenbank des Bots nur lesend (ADR-0016). '
            "Sie erscheint, sobald <code>trading-bot paper-cycle</code> einen Zyklus geschrieben "
            "hat.</p></section>"
        )
    else:
        body = (
            _readouts(view)
            + '<div class="grid">'
            + '<section class="panel span-5">'
            + pages._head("01", "Lebenszeichen")
            + _pulses(view.pulses)
            + '<p class="hint">Dienst: läuft ≤ 3 min nach der letzten Runde · verzögert ≤ 15 min. '
            "Zyklus und Kurse: läuft ≤ 15 min · verzögert ≤ 60 min. Danach steht.</p></section>"
            + '<section class="panel span-7">'
            + pages._head("02", "GPT heute", "amber")
            + _llm(view.llm)
            + "</section>"
            + '<section class="panel span-12">'
            + pages._head("03", "Depots", "warm")
            + _depots(view.depots)
            + '<p class="hint">Startkapital je Depot 10 000 €; Werte nach Gebühren und Slippage. '
            "pp = Prozentpunkte gegenüber Buy &amp; Hold.</p></section>"
            + '<section class="panel span-12 real">'
            + pages._head("04", "Equity-Verlauf · echte Paper-Daten")
            + equity_chart(view.series)
            + "</section>"
            + '<section class="panel span-12">'
            + pages._head("05", "Letzte Entscheidungen", "warm")
            + _decisions(view.decisions)
            + "</section>"
            + '<section class="panel span-12">'
            + pages._head("06", "Letzte Trades (virtuell)")
            + _trades(view.trades)
            + "</section></div>"
        )
    if now is not None:
        # Server time for /app.js: the browser's clock may be off, the ages must not be.
        body = f'<span hidden data-now="{int(now)}"></span>' + body
    return pages._page(
        "Übersicht",
        body,
        csrf=csrf,
        active="/",
        refresh=REFRESH_SECONDS,
        ticker=pages.TICKER if view is None else ticker(view),
        now=now,
        script=True,
    )
