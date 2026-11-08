"""HTML of the web UI. Every dynamic value goes through ``html.escape``; no inline script or style
(the CSP forbids both), so injected markup cannot run. Decoration is inline SVG with presentation
attributes and classes from ``/app.css``."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape

from llm_trading_bot.llm.agents import (
    LLM_DEFAULTS,
    MAX_OUTPUT_TOKENS,
    MODEL_PRICES,
    USDC_AGENTS,
    USDC_FIELD,
)
from llm_trading_bot.llm.openai_client import EFFORTS
from llm_trading_bot.web import chart
from llm_trading_bot.web.credentials import AGENT_CHOICES, PUBLIC_OVERVIEW_FIELD, mask
from llm_trading_bot.web.style import CSS, TICKER_SECONDS

__all__ = [
    "CSS",
    "impressum_missing_page",
    "legal_page",
    "login_page",
    "settings_page",
    "setup_page",
]

TICKER = (
    ("BTC/EUR", "ETH/EUR"),
    ("Modus", "Paper – kein Echtgeld"),
    ("Kerzen", "nur abgeschlossene"),
    ("gpt-luna", "Haupt-Trader"),
    ("gpt-sol", "Gegenprobe"),
    ("Buy & Hold", "Referenz"),
    ("GPT", "nur bei Bedarf · max. 4/h"),
    ("Budget", "hartes EUR-Tageslimit"),
    ("Fail closed", "keine Order ohne frische Kurse"),
    ("Zugang", "HTTPS mit Login · SSH-Tunnel"),
)


_TRACES = (
    ("", "M-10 140 H220 L270 190 H520 L560 150 H760"),
    ("warm", "M1450 90 H1180 L1130 140 H960 L920 100 H820"),
    ("", "M-10 420 H120 L170 470 V640 L210 680 H420"),
    ("warm", "M1450 360 H1300 L1250 410 V500 L1210 540 H1100"),
    ("", "M300 -10 V60 L340 100 H460"),
    ("", "M60 910 V820 L110 770 H300 L340 730 H520"),
    ("warm", "M640 910 V860 L680 820 H760"),
)
_NODES = (
    ("hot", 760, 150),
    ("", 220, 140),
    ("", 520, 190),
    ("warm", 820, 100),
    ("", 960, 140),
    ("", 420, 680),
    ("hot", 170, 470),
    ("warm", 1100, 540),
    ("", 460, 100),
    ("", 520, 730),
    ("warm", 1250, 410),
    ("", 760, 820),
)


def _backdrop() -> str:
    traces = "".join(
        f'<path class="trace {kind}" d="{d}"/><path class="pulse {kind}{" slow" * (i % 3 == 2)}"'
        f' d="{d}"/>'
        for i, (kind, d) in enumerate(_TRACES)
    )
    nodes = "".join(f'<circle class="node {k}" cx="{x}" cy="{y}" r="4"/>' for k, x, y in _NODES)
    pins = "".join(
        f'<line class="chip-pin" x1="{x}" y1="236" x2="{x}" y2="248"/>'
        f'<line class="chip-pin" x1="{x}" y1="312" x2="{x}" y2="324"/>'
        for x in range(1252, 1340, 14)
    )
    chip = f'<rect class="chip-pad" x="1240" y="248" width="104" height="64" rx="4"/>{pins}'
    axes = "".join(
        f'<line class="axis" x1="0" x2="1440" y1="{y}" y2="{y}"/>' for y in (580, 680, 780, 880)
    )
    return (
        '<svg class="backdrop" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" '
        f'aria-hidden="true" focusable="false">{axes}{traces}{nodes}{chip}{chart.svg()}</svg>'
    )


BACKDROP = _backdrop()

LOGO = (
    '<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">'
    '<circle class="logo-ring" cx="20" cy="20" r="18.5"/>'
    '<path class="logo-hex" d="M20 4 L34 12 V28 L20 36 L6 28 V12 Z"/>'
    '<line class="logo-wick" x1="14" y1="13" x2="14" y2="27"/>'
    '<rect class="logo-up" x="12" y="16" width="4" height="8"/>'
    '<line class="logo-wick" x1="20" y1="11" x2="20" y2="25"/>'
    '<rect class="logo-down" x="18" y="13" width="4" height="9"/>'
    '<line class="logo-wick" x1="26" y1="10" x2="26" y2="24"/>'
    '<rect class="logo-up" x="24" y="12" width="4" height="9"/></svg>'
)

RADAR = (
    '<svg class="radar" viewBox="0 0 240 240" role="img" '
    'aria-label="Dekorativer Radar-Scanner">'
    '<defs><radialGradient id="sweep" cx="120" cy="120" r="100" gradientUnits="userSpaceOnUse">'
    '<stop offset="0" stop-color="#2de2e6" stop-opacity="0"/>'
    '<stop offset="1" stop-color="#2de2e6" stop-opacity="0.45"/></radialGradient></defs>'
    '<circle class="outer" cx="120" cy="120" r="114"/>'
    '<circle class="ring" cx="120" cy="120" r="100"/>'
    '<circle class="ring dash" cx="120" cy="120" r="75"/>'
    '<circle class="ring" cx="120" cy="120" r="50"/>'
    '<circle class="ring dash" cx="120" cy="120" r="25"/>'
    '<line class="cross" x1="20" y1="120" x2="220" y2="120"/>'
    '<line class="cross" x1="120" y1="20" x2="120" y2="220"/>'
    '<path class="sweep" fill="url(#sweep)" d="M120 120 L120 20 A100 100 0 0 1 190.7 49.3 Z"/>'
    '<circle class="blip" cx="158" cy="86" r="3"/><circle class="ping" cx="158" cy="86" r="3"/>'
    '<circle class="blip warm" cx="84" cy="150" r="3"/>'
    '<circle class="ping warm" cx="84" cy="150" r="3"/>'
    '<circle class="blip" cx="140" cy="172" r="2"/>'
    '<text x="126" y="31">BTC</text><text x="196" y="117">ETH</text>'
    '<text x="126" y="216">EUR</text></svg>'
)


TABS = (("/", "Übersicht"), ("/settings", "Einstellungen"))


def _page(
    title: str,
    body: str,
    *,
    csrf: str | None = None,
    active: str | None = None,
    refresh: int | None = None,
    ticker: Sequence[tuple[str, str]] = TICKER,
    now: float | None = None,
    script: bool = False,
    login: bool = False,
    backdrop: bool = True,
) -> str:
    """``ticker``: (label, value) pairs for the strip under the header. ``now`` (wall clock) lets
    a reloading page continue the strip where the last load left it. ``script`` loads /app.js
    (ADR-0019); the meta refresh then only works as a fallback without scripts. ``login``: the
    overview without a session (ADR-0028) links to the login instead of offering a logout.
    ``backdrop`` False: an update request of /app.js, which keeps the backdrop it has."""
    if csrf:
        logout = (
            '<form method="post" action="/logout">'
            f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
            '<button class="quiet" type="submit">Abmelden</button></form>'
        )
    else:
        logout = '<a class="quiet" href="/login">Anmelden</a>' if login else ""
    items = "".join(f"<span><b>{escape(head)}</b>{escape(text)}</span>" for head, text in ticker)
    phase = "" if now is None else f" tp-{int(now) % TICKER_SECONDS}"
    # Tabs only after login: before it, every route leads to login or setup anyway.
    tabs = (
        '<nav class="tabs" aria-label="Bereiche">'
        + "".join(
            f'<a href="{href}"{' class="active" aria-current="page"' if href == active else ""}>'
            f"{escape(label)}</a>"
            for href, label in TABS
        )
        + "</nav>"
        if csrf
        else ""
    )
    reload = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    # ADR-0028: the overview without login names no files. /app.js compares the views: an owner
    # page that gets the view without login back has lost its session -> to the login.
    view = "" if not script else ' data-view="owner"' if csrf else ' data-view="public"'
    files = "" if login else "<span>Zugangsdaten in secrets.env (600)</span>"
    if script:
        # ADR-0019: our own file only - the CSP allows no inline script. Without scripts the
        # page still reloads itself.
        reload = '<script src="/app.js" defer></script>' + (
            f"<noscript>{reload}</noscript>" if reload else ""
        )
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="dark">'
        + reload
        + f"<title>{escape(title)} · LLM Trading Bot</title>"
        '<link rel="stylesheet" href="/app.css"></head>'
        + ('<body class="live">' if refresh else "<body>")
        + (BACKDROP if backdrop else "")
        # Header and ticker stay on screen while the page scrolls (user 2026-11-08).
        + '<div class="masthead">'
        + f'<header><a class="brand" href="/">{LOGO}<div><p class="brand-name">LLM '
        '<span>Trading</span> Bot</p><div class="brand-sub">Paper-Terminal · BTC/EUR · ETH/EUR'
        f"</div></div></a>{tabs}"
        '<div class="status"><span class="chip ok"><i class="led"></i>Paper</span>'
        '<span class="chip lock"><i class="led"></i>Echtgeld gesperrt</span>'
        f"{logout}</div></header>"
        # The track holds the items twice so the loop has no visible seam.
        f'<div class="ticker" aria-hidden="true"><div class="ticker-track{phase}">'
        f"{items}{items}</div></div></div><main{view}>{body}</main>"
        "<footer><span>Paper-Modus · kein Echtgeld (ADR-0004)</span>"
        + files
        # Spec 014: on every page, also before login - a public site has to name its operator.
        + '<nav class="legal-links" aria-label="Rechtliches"><a href="/impressum">Impressum</a>'
        '<a href="/datenschutz">Datenschutz</a></nav></footer></body></html>'
    )


def _hidden(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{escape(csrf)}">'


def _message(message: str, ok: bool) -> str:
    if not message:
        return ""
    return f'<div class="msg {"ok" if ok else "bad"}" role="status">{escape(message)}</div>'


def _head(tag: str, title: str, tone: str = "") -> str:
    return (
        f'<div class="panel-head"><span class="tag {tone}">{escape(tag)}</span>'
        f'<h2>{escape(title)}</h2><span class="leds"><i></i><i></i><i></i></span></div>'
    )


def _auth(title: str, form: str, lines: tuple[tuple[str, str, str], ...]) -> str:
    console = "".join(
        f'<div>{escape(k)}<span class="dots"></span><b class="{tone}">{escape(v)}</b></div>'
        for k, v, tone in lines
    )
    return (
        f'<div class="auth"><div class="scope">{RADAR}<div class="console">{console}'
        f'<div class="cursor">bereit</div></div></div>'
        f'<div class="panel">{_head("Zugang", title)}{form}</div></div>'
    )


_CONSOLE = (
    ("verbindung", "lokal", ""),
    ("handelsmodus", "paper", ""),
    ("echtgeld", "gesperrt", "amber"),
)


def setup_page(message: str = "", *, csrf: str) -> str:
    form = (
        _message(message, False) + '<p class="lead">Der Einrichtungscode steht im Terminal, in dem '
        "<code>trading-bot web</code> läuft. Er gilt nur, bis ein Passwort festgelegt ist.</p>"
        '<form method="post" action="/setup">'
        + _hidden(csrf)
        + '<label for="code">Einrichtungscode</label>'
        '<input id="code" name="code" type="text" autocomplete="off" spellcheck="false" required>'
        '<label for="password">Neues Passwort (mindestens 12 Zeichen)</label>'
        '<input id="password" name="password" type="password" autocomplete="new-password" '
        'minlength="12" required>'
        '<label for="password2">Passwort wiederholen</label>'
        '<input id="password2" name="password2" type="password" autocomplete="new-password" '
        'minlength="12" required>'
        '<button type="submit">Passwort festlegen</button></form>'
    )
    lines = (*_CONSOLE, ("passwort", "fehlt", "amber"))
    return _page("Einrichtung", _auth("Ersteinrichtung", form, lines))


def setup_closed_page() -> str:
    """ADR-0024: on the public way there is no setup - no form, no cookie, no code."""
    text = (
        '<p class="lead">Noch kein Passwort festgelegt. Die Einrichtung geht nur über den '
        "SSH-Tunnel (siehe <code>docs/betrieb/server.md</code>).</p>"
    )
    lines = (*_CONSOLE, ("passwort", "fehlt", "amber"))
    return _page("Einrichtung", _auth("Einrichtung nur per SSH-Tunnel", text, lines))


def legal_page(title: str, html: str) -> str:
    """Impressum or privacy policy; ``html`` comes from ``legal.render`` (already escaped). No
    logout button and no tabs: the page is the same with or without login."""
    return _page(title, f'<article class="panel legal">{html}</article>')


def impressum_missing_page() -> str:
    """AK3: no path and no reason - both would describe the server."""
    return legal_page(
        "Impressum",
        "<h1>Impressum nicht hinterlegt</h1>"
        "<p>Das Impressum ist auf diesem Server noch nicht hinterlegt.</p>",
    )


def login_page(message: str = "", *, csrf: str) -> str:
    form = (
        _message(message, False)
        + '<p class="lead">Anmelden, um API-Key, Depots und Budget zu verwalten.</p>'
        '<form method="post" action="/login">'
        + _hidden(csrf)
        + '<label for="password">Passwort</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" '
        "required autofocus>"
        '<button type="submit">Anmelden</button></form>'
    )
    return _page("Anmelden", _auth("Anmelden", form, (*_CONSOLE, ("sitzung", "keine", "amber"))))


def budget_value(values: Mapping[str, str], name: str, default: str | Decimal) -> Decimal:
    try:
        amount = Decimal(values.get(name) or default)
    except InvalidOperation:
        amount = Decimal(default)
    return amount if amount.is_finite() and amount >= 0 else Decimal(default)


def _gauge(total: Decimal, luna: Decimal, sol: Decimal) -> str:
    """Share of the daily budget per depot; drawn with SVG attributes because the CSP forbids
    style attributes."""
    scale = max(total, luna + sol, Decimal("0.01"))
    width = 600

    def w(part: Decimal) -> float:
        return round(float(part / scale) * width, 1)

    luna_w, sol_w = w(luna), w(sol)
    free_w = max(round(width - luna_w - sol_w, 1), 0.0)
    ticks = "".join(
        f'<line class="tick" x1="{x}" x2="{x}" y1="24" y2="30"/>'
        f'<text x="{x}" y="40" text-anchor="{anchor}">{q}%</text>'
        for x, q, anchor in (
            (1, 0, "start"),
            (150, 25, "middle"),
            (300, 50, "middle"),
            (450, 75, "middle"),
            (599, 100, "end"),
        )
    )
    return (
        '<svg class="gauge" viewBox="0 0 600 42" role="img" '
        f'aria-label="Budgetaufteilung: gpt-luna {luna} €, gpt-sol {sol} € von {total} €">'
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="6" height="6" fill="#000000"/>'
        '<rect width="2" height="6" fill="#3a3c42"/></pattern></defs>'
        '<rect class="bed" x="0.5" y="0.5" width="599" height="21" rx="1"/>'
        f'<rect class="luna" x="1" y="1" width="{max(luna_w - 1, 0)}" height="20"/>'
        f'<rect class="sol" x="{luna_w}" y="1" width="{sol_w}" height="20"/>'
        f'<rect fill="url(#hatch)" x="{luna_w + sol_w}" y="1" width="{free_w}" height="20"/>'
        f"{ticks}</svg>"
    )


# Spec 005 AK10, 2026-11-06: one real request per model (BTC+ETH, effort low). Only this level is
# measured; the other levels are shown by price per token and the hard output limit, not guessed.
MEASURED_EFFORT = "low"
MEASURED: Mapping[str, tuple[int, int, int]] = {
    # model: (input tokens, output tokens, of which reasoning)
    "gpt-6-luna": (1566, 367, 89),
    "gpt-6.1-sol": (1566, 259, 0),
}
MAX_REQUESTS_PER_DAY = 24 * 4  # ADR-0013: at most 4 requests per hour and depot


def _eur(amount: Decimal, places: int = 4) -> str:
    return f"{amount:.{places}f} €"


def _reasoning_costs(effort: str, shares: Mapping[str, Decimal]) -> str:
    """What the effort costs: price per reasoning token, the measured request and the worst case.
    The budget per depot caps the day in any case."""
    million = Decimal(1_000_000)
    cols = []
    for depot, (model, _share) in LLM_DEFAULTS.items():
        price = MODEL_PRICES[model]
        tokens_in, tokens_out, reasoning = MEASURED[model]
        measured = (
            tokens_in * price.input_per_million + tokens_out * price.output_per_million
        ) / million
        worst = (
            tokens_in * price.input_per_million + MAX_OUTPUT_TOKENS * price.output_per_million
        ) / million
        day = worst * MAX_REQUESTS_PER_DAY
        share = shares[depot]
        cols.append(
            (
                depot,
                _eur(price.output_per_million / 1000),
                f"{reasoning}",
                _eur(measured, 5),
                _eur(measured * MAX_REQUESTS_PER_DAY, 2),
                _eur(worst),
                _eur(min(day, share), 2) + (" (Budget)" if day > share else ""),
            )
        )
    rows = (
        ("je 1.000 Reasoning-Tokens", 1),
        (f"Reasoning-Tokens gemessen bei {MEASURED_EFFORT}", 2),
        (f"gemessen bei {MEASURED_EFFORT} je Anfrage", 3),
        (f"bei {MEASURED_EFFORT}, {MAX_REQUESTS_PER_DAY} Anfragen/Tag (Maximum)", 4),
        (f"Obergrenze je Anfrage ({MAX_OUTPUT_TOKENS:,} Output-Tokens)".replace(",", "."), 5),
        (f"Obergrenze pro Tag ({MAX_REQUESTS_PER_DAY} Anfragen)", 6),
    )
    head = "".join(f"<th>{escape(c[0])}</th>" for c in cols)
    body = "".join(
        f"<tr><th>{escape(label)}</th>"
        + "".join(f"<td>{escape(c[i])}</td>" for c in cols)
        + "</tr>"
        for label, i in rows
    )
    note = (
        "Gemessen ist nur diese Stufe."
        if effort == MEASURED_EFFORT
        else f"„{escape(effort)}“ ist nicht gemessen: Das Modell entscheidet selbst, wie viele "
        "Reasoning-Tokens es braucht – höhere Stufen meist ein Vielfaches."
    )
    return (
        f'<table class="costs"><thead><tr><th>Kosten</th>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
        f'<p class="hint">Reasoning-Tokens werden wie Output abgerechnet. {note} Mehr als die '
        "Obergrenze kann eine Anfrage nicht kosten; das Tagesbudget je Depot stoppt vorher.</p>"
    )


@dataclass(frozen=True)
class Stopped:
    agent: str
    since: str | None
    """Time of the depot's latest forced sale; None if it was halted without one."""
    requested: bool
    """A release request newer than the stop is waiting for the next round."""


@dataclass(frozen=True)
class Trading:
    """Trading pause and stopped depots (Spec 011 AK5/AK6, ADR-0021)."""

    requested_pause: bool
    """What secrets.env asks for."""
    applied_pause: bool | None
    """What the service applied in its last round; None without a readable status file."""
    source: str = ""
    """Where the applied state came from (status file): environment, file, invalid or ""."""
    stopped: tuple[Stopped, ...] = ()
    problem: str = ""
    """Why the depots could not be read."""

    @property
    def locked(self) -> bool:
        """The service's environment sets BOT_PAUSED: the switch has no effect."""
        return self.source == "environment"


def trading_note(t: Trading) -> str:
    """One line on the pause for the overview and the settings page; "" while trading runs."""
    if t.applied_pause:
        if t.locked:
            return "Handel pausiert – durch die Umgebung des Dienstes festgelegt (BOT_PAUSED)"
        if t.source == "invalid":
            return "Handel pausiert – Einstellung unlesbar oder ungültig, im Zweifel Pause"
        if not t.requested_pause:
            return "Handel pausiert – Fortsetzen angefordert, greift in der nächsten Runde"
        return "Handel pausiert – nur der Risikostopp verkauft noch"
    if t.requested_pause:
        if t.locked:
            return "Pause angefordert, aber die Umgebung des Dienstes legt BOT_PAUSED fest"
        return "Pause angefordert, greift in der nächsten Runde"
    return ""


def public_trading_note(t: Trading) -> str:
    """The pause for the overview without login (ADR-0028): what happens, not where it is set."""
    if t.applied_pause:
        return "Handel pausiert – nur der Risikostopp verkauft noch"
    if t.requested_pause:
        return "Pause angefordert, greift in der nächsten Runde"
    return ""


def _when(ts: str | None) -> str:
    return "–" if ts is None else datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M UTC")


def _trading_panel(t: Trading, hidden: str) -> str:
    note = trading_note(t)
    paused = t.applied_pause if t.applied_pause is not None else t.requested_pause
    state = note or "Handel läuft"
    want = "0" if t.requested_pause else "1"
    label = "Handel fortsetzen" if t.requested_pause else "Handel pausieren"
    switch = (
        '<p class="hint">Der Schalter wirkt nicht, solange die Umgebung des Dienstes '
        "<code>BOT_PAUSED</code> setzt.</p>"
        if t.locked
        else '<form method="post" action="/settings/pause" class="inline">'
        + hidden
        + f'<input type="hidden" name="paused" value="{want}">'
        + f'<button type="submit"{"" if t.requested_pause else ' class="alt"'}>{label}</button>'
        "</form>"
    )
    if t.problem:
        depots = f'<p class="hint">{escape(t.problem)}</p>'
    elif not t.stopped:
        depots = '<p class="hint">Kein Depot im Risikostopp.</p>'
    else:
        rows = []
        for s in t.stopped:
            since = f" seit {_when(s.since)}" if s.since else ""
            action = (
                "<small>Freigabe angefordert – greift in der nächsten Runde</small>"
                if s.requested
                else '<form method="post" action="/settings/resume" class="inline">'
                + hidden
                + f'<input type="hidden" name="agent" value="{escape(s.agent)}">'
                + '<button type="submit" class="alt">Wieder freigeben</button></form>'
            )
            rows.append(
                f'<li><b>{escape(s.agent)}</b> <span class="badge stop">Risikostopp'
                f"{escape(since)}</span> {action}</li>"
            )
        depots = f'<ul class="stopped">{"".join(rows)}</ul>'
    return (
        '<section class="panel">'
        + _head("R", "Handel & Risikostopp", "amber")
        + f'<div class="keystate{" off" if paused else ""}"><i class="led"></i>'
        f"<strong>{escape(state)}</strong></div>"
        + switch
        + '<p class="hint">Pause: kein Depot kauft oder verkauft mehr, auch GPT wird nicht '
        "gefragt; nur der Risikostopp verkauft weiter. Greift ab der nächsten Runde "
        "(höchstens 1 min).</p>"
        + depots
        + '<p class="hint">Risikostopp: Fällt ein Depot 10 % unter seinen Höchststand, verkauft '
        "der Bot dessen Coins und stoppt es. Nach der Freigabe handelt es wieder, sein Bargeld "
        "ist der neue Höchststand. Eine Freigabe gilt 24 h.</p></section>"
    )


_BADGE_CLASS = {"LLM": " llm", "USDC": " usdc"}
"""Depot kinds with their own badge colour; the classic ones keep the plain badge."""


def settings_page(
    values: Mapping[str, str],
    *,
    csrf: str,
    message: str = "",
    ok: bool = True,
    trading: Trading | None = None,
) -> str:
    key = values.get("OPENAI_API_KEY")
    key_state = f"gespeichert ({escape(mask(key))})" if key else "nicht gesetzt"
    selected = values.get("BOT_AGENTS", ",".join(LLM_DEFAULTS)).split(",")
    # The bot's default (Spec 020 AK4): a missing USDC choice runs all of them.
    selected += values.get(USDC_FIELD, ",".join(USDC_AGENTS)).split(",")
    labels = {
        "gpt-luna": ("gpt-6-luna · Haupt-Trader", "LLM"),
        "gpt-sol": ("gpt-6.1-sol · Gegenprobe", "LLM"),
        "trend": ("Trendfolge", "KLASSISCH"),
        "breakout": ("Ausbruch", "KLASSISCH"),
        "meanrev": ("Mean Reversion", "KLASSISCH"),
        "ensemble": ("Mehrheitsentscheid", "KLASSISCH"),
        # Spec 020: the same rules, BTC bought and sold over BTC-USDC, cash in euro.
        "trend-usdc": ("Trendfolge · BTC über USDC", "USDC"),
        "breakout-usdc": ("Ausbruch · BTC über USDC", "USDC"),
        "meanrev-usdc": ("Mean Reversion · BTC über USDC", "USDC"),
        "ensemble-usdc": ("Mehrheitsentscheid · BTC über USDC", "USDC"),
    }
    depots = "".join(
        f'<label class="tile"><input type="checkbox" name="agents" value="{escape(a)}"'
        f"{' checked' if a in selected else ''}>"
        f'<span class="tile-name">{escape(a)}</span>'
        f'<span class="tile-sub">{escape(labels[a][0])}</span>'
        f'<span class="badge{_BADGE_CLASS.get(labels[a][1], "")}">'
        f"{escape(labels[a][1])}</span></label>"
        for a in AGENT_CHOICES
    )
    effort = values.get("LLM_EFFORT", "low")
    efforts = "".join(
        f'<option value="{e}"{" selected" if e == effort else ""}>{e}</option>' for e in EFFORTS
    )
    total = budget_value(values, "LLM_DAILY_BUDGET_EUR", "3")
    luna = budget_value(values, "LLM_DAILY_BUDGET_EUR_GPT_LUNA", "0.50")
    sol = budget_value(values, "LLM_DAILY_BUDGET_EUR_GPT_SOL", "2.50")
    active = [a for a in AGENT_CHOICES if a in selected]

    def amount(name: str, label: str, default: str) -> str:
        return (
            f'<div><label for="{name}">{escape(label)}</label>'
            f'<input id="{name}" name="{name}" type="number" min="0" step="0.01" '
            f'value="{escape(values.get(name, ""))}" placeholder="{default}"></div>'
        )

    def readout(label: str, value: str, off: bool = False) -> str:
        cls = ' class="off"' if off else ""
        return f'<div class="readout"><small>{escape(label)}</small><b{cls}>{value}</b></div>'

    hidden = _hidden(csrf)
    readouts = (
        '<div class="readouts">'
        + readout("API-Key", escape(mask(key)) if key else "fehlt", off=not key)
        + readout("Depots aktiv", f"{len(active)} / {len(AGENT_CHOICES)}", off=not active)
        + readout("Tagesbudget", f"{escape(f'{total:.2f}')} €")
        + readout("Reasoning", escape(effort))
        + "</div>"
    )
    body = (
        _message(message, ok)
        + readouts
        + (_trading_panel(trading, hidden) if trading is not None else "")
        + '<form method="post" action="/settings" class="grid">'
        + hidden
        + '<section class="panel span-5">'
        + _head("01", "OpenAI-Zugang")
        + f'<div class="keystate{"" if key else " off"}"><i class="led"></i>'
        f"API-Key: <strong>{key_state}</strong></div>"
        '<label for="key">Neuer API-Key</label>'
        '<input id="key" name="OPENAI_API_KEY" type="password" autocomplete="off" '
        'spellcheck="false" placeholder="sk-… (leer lassen = unverändert)">'
        '<p class="hint">Der Key wird nur in <code>secrets.env</code> im Datenverzeichnis '
        "gespeichert und nie wieder vollständig angezeigt. Bitvavo-Keys gibt es erst mit dem "
        "Echtgeld-Meilenstein.</p>"
        '<label class="check"><input type="checkbox" name="clear_key" value="1"> '
        "gespeicherten Key löschen</label></section>"
        '<section class="panel span-7">'
        + _head("02", "Reasoning", "amber")
        + '<label for="effort">Reasoning-Effort</label>'
        f'<select id="effort" name="LLM_EFFORT">{efforts}</select>'
        '<p class="hint">Mehr Effort heißt mehr Reasoning-Tokens je Anfrage – und höhere '
        "Kosten. Standard: <code>low</code>.</p>"
        + _reasoning_costs(effort, {"gpt-luna": luna, "gpt-sol": sol})
        + "</section>"
        '<section class="panel span-12">'
        + _head("03", "Depots", "warm")
        + f'<div class="tiles">{depots}</div>'
        '<p class="hint">Buy &amp; Hold läuft immer als Referenz mit, Buy &amp; Hold USDC '
        "dazu, sobald ein USDC-Depot läuft. USDC-Depots handeln BTC über BTC-USDC "
        "(0,05 % statt 0,25 % Gebühr) und tauschen je Trade Euro in USDC und zurück.</p>"
        "</section>"
        '<section class="panel span-12">'
        + _head("04", "Budget € / Tag")
        + _gauge(total, luna, sol)
        + '<div class="legend">'
        f'<span class="l-luna">gpt-luna {escape(f"{luna:.2f}")} €</span>'
        f'<span class="l-sol">gpt-sol {escape(f"{sol:.2f}")} €</span>'
        f'<span class="l-free">Gesamtlimit {escape(f"{total:.2f}")} €</span></div>'
        '<div class="fields">'
        + amount("LLM_DAILY_BUDGET_EUR", "Gesamt € / Tag", "3")
        + amount("LLM_DAILY_BUDGET_EUR_GPT_LUNA", "gpt-luna € / Tag", "0.50")
        + amount("LLM_DAILY_BUDGET_EUR_GPT_SOL", "gpt-sol € / Tag", "2.50")
        + "</div>"
        '<div class="actions"><p class="hint">Leere Felder nutzen die Standardwerte. Gespeichert '
        "wird nur, was die Prüfung des Bots besteht.</p>"
        '<button type="submit">Speichern</button></div></section></form>'
        '<section class="panel">'
        + _head("05", "Verbindung testen", "warm")
        + '<p class="hint">Fragt bei OpenAI nur ab, ob das Modell gpt-6-luna erreichbar ist – '
        "kostenlos, ohne Token-Verbrauch.</p>"
        '<form method="post" action="/settings/test">'
        + hidden
        + '<button class="alt" type="submit">Gespeicherten Key testen</button></form></section>'
        '<section class="panel">'
        + _head("06", "Passwort ändern", "amber")
        + '<form method="post" action="/settings/password">'
        + hidden
        + '<label for="old">Altes Passwort</label>'
        '<input id="old" name="old" type="password" autocomplete="current-password" required>'
        '<label for="new">Neues Passwort (mindestens 12 Zeichen)</label>'
        '<input id="new" name="new" type="password" autocomplete="new-password" '
        'minlength="12" required>'
        '<label for="new2">Neues Passwort wiederholen</label>'
        '<input id="new2" name="new2" type="password" autocomplete="new-password" '
        'minlength="12" required>'
        '<p class="hint">Für die Anmeldung aus dem Internet braucht es mindestens 12 Zeichen, '
        "die nicht leicht zu erraten sind. Andere Sitzungen werden danach abgemeldet.</p>"
        '<button type="submit">Passwort ändern</button></form></section>'
        + _public_panel(values, hidden)
    )
    return _page("Einstellungen", body, csrf=csrf, active="/settings")


def public_overview_on(values: Mapping[str, str]) -> bool:
    """ADR-0028: on unless switched off; any other stored value counts as off (fail closed)."""
    return values.get(PUBLIC_OVERVIEW_FIELD, "1") == "1"


def _public_panel(values: Mapping[str, str], hidden: str) -> str:
    on = public_overview_on(values)
    want, label = ("0", "Nur mit Login") if on else ("1", "Ohne Login zeigen")
    return (
        '<section class="panel">'
        + _head("07", "Übersicht ohne Login", "warm")
        + f'<div class="keystate{"" if on else " off"}"><i class="led"></i>'
        + ("Die Übersicht ist ohne Login lesbar." if on else "Die Übersicht verlangt den Login.")
        + "</div>"
        '<p class="hint">Ohne Login nur lesend: Depots, Equity, Signale, Trades, Lebenszeichen, '
        "GPT-Kosten – ohne Fehlertexte. Einstellungen, Pause und Freigaben bleiben immer hinter "
        "dem Login. Suchmaschinen werden gebeten, die Seite nicht aufzunehmen.</p>"
        '<form method="post" action="/settings/public" class="inline">'
        + hidden
        + f'<input type="hidden" name="public" value="{want}">'
        + f'<button type="submit"{' class="alt"' if on else ""}>{label}</button></form>'
        "</section>"
    )
