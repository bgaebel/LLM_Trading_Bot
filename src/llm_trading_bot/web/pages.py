"""HTML of the web UI. Every dynamic value goes through ``html.escape``; no inline script or style
(the CSP forbids both), so injected markup cannot run."""

from collections.abc import Mapping
from html import escape

from llm_trading_bot.llm.agents import LLM_DEFAULTS
from llm_trading_bot.web.credentials import AGENT_CHOICES, mask

CSS = """
:root { --bg: #f6f7f9; --card: #fff; --text: #1d2430; --muted: #5d6878; --line: #d9dee6;
  --accent: #1f6feb; --ok: #1a7f37; --bad: #c62828; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0f141b; --card: #171e27; --text: #e6ebf2; --muted: #9aa6b5; --line: #2a3442;
    --accent: #4c8dff; --ok: #3fb950; --bad: #ff6b6b; } }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--bg); color: var(--text); }
header { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px;
  border-bottom: 1px solid var(--line); background: var(--card); }
header h1 { font-size: 17px; margin: 0; }
header form { margin: 0; }
main { max-width: 720px; margin: 24px auto; padding: 0 16px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 20px; margin-bottom: 18px; }
h2 { font-size: 16px; margin: 0 0 12px; }
label { display: block; font-weight: 600; margin: 12px 0 4px; }
.hint { color: var(--muted); font-size: 13px; margin: 2px 0 0; }
input[type=text], input[type=password], input[type=number], select { width: 100%; padding: 9px 10px;
  border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--text);
  font: inherit; }
.row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.check { display: flex; gap: 8px; align-items: center; font-weight: 400; margin: 6px 0; }
button { margin-top: 16px; padding: 9px 16px; border: 0; border-radius: 6px;
  background: var(--accent); color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
button.quiet { background: transparent; color: var(--accent); border: 1px solid var(--line);
  margin-top: 0; }
.msg { padding: 10px 12px; border-radius: 6px; margin-bottom: 16px; border: 1px solid var(--line); }
.msg.ok { border-color: var(--ok); color: var(--ok); }
.msg.bad { border-color: var(--bad); color: var(--bad); }
code { font-size: 13px; }
.sep { margin-top: 22px; }
"""


def _page(title: str, body: str, *, csrf: str | None = None) -> str:
    logout = (
        '<form method="post" action="/logout">'
        f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
        '<button class="quiet" type="submit">Abmelden</button></form>'
        if csrf
        else ""
    )
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · LLM Trading Bot</title>"
        '<link rel="stylesheet" href="/app.css"></head><body>'
        f"<header><h1>LLM Trading Bot · Paper</h1>{logout}</header>"
        f"<main>{body}</main></body></html>"
    )


def _message(message: str, ok: bool) -> str:
    if not message:
        return ""
    return f'<div class="msg {"ok" if ok else "bad"}" role="status">{escape(message)}</div>'


def setup_page(message: str = "") -> str:
    return _page(
        "Einrichtung",
        _message(message, False) + '<div class="card"><h2>Ersteinrichtung</h2>'
        '<p class="hint">Der Einrichtungscode steht im Terminal, in dem '
        "<code>trading-bot web</code> läuft. Er gilt nur, bis ein Passwort festgelegt ist.</p>"
        '<form method="post" action="/setup">'
        '<label for="code">Einrichtungscode</label>'
        '<input id="code" name="code" type="text" autocomplete="off" required>'
        '<label for="password">Neues Passwort (mindestens 8 Zeichen)</label>'
        '<input id="password" name="password" type="password" autocomplete="new-password" '
        'minlength="8" required>'
        '<label for="password2">Passwort wiederholen</label>'
        '<input id="password2" name="password2" type="password" autocomplete="new-password" '
        'minlength="8" required>'
        '<button type="submit">Passwort festlegen</button></form></div>',
    )


def login_page(message: str = "") -> str:
    return _page(
        "Anmelden",
        _message(message, False) + '<div class="card"><h2>Anmelden</h2>'
        '<form method="post" action="/login">'
        '<label for="password">Passwort</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" '
        "required autofocus>"
        '<button type="submit">Anmelden</button></form></div>',
    )


def settings_page(
    values: Mapping[str, str], *, csrf: str, message: str = "", ok: bool = True
) -> str:
    key = values.get("OPENAI_API_KEY")
    key_state = f"gespeichert ({escape(mask(key))})" if key else "nicht gesetzt"
    selected = values.get("BOT_AGENTS", ",".join(LLM_DEFAULTS)).split(",")
    labels = {
        "gpt-luna": "gpt-luna – gpt-6-luna, Haupt-Trader",
        "gpt-sol": "gpt-sol – gpt-6.1-sol, Gegenprobe",
        "trend": "trend – klassisch",
        "breakout": "breakout – klassisch",
        "meanrev": "meanrev – klassisch",
        "ensemble": "ensemble – klassisch",
    }
    depots = "".join(
        f'<label class="check"><input type="checkbox" name="agents" value="{escape(a)}"'
        f"{' checked' if a in selected else ''}> {escape(labels[a])}</label>"
        for a in AGENT_CHOICES
    )
    effort = values.get("LLM_EFFORT", "low")
    efforts = "".join(
        f'<option value="{e}"{" selected" if e == effort else ""}>{e}</option>'
        for e in ("low", "medium", "high")
    )

    def amount(name: str, label: str, default: str) -> str:
        return (
            f'<div><label for="{name}">{escape(label)}</label>'
            f'<input id="{name}" name="{name}" type="number" min="0" step="0.01" '
            f'value="{escape(values.get(name, ""))}" placeholder="{default}"></div>'
        )

    hidden = f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
    body = (
        _message(message, ok) + '<div class="card"><h2>OpenAI-Zugang</h2>'
        f"<p>API-Key: <strong>{key_state}</strong></p>"
        '<form method="post" action="/settings">'
        + hidden
        + '<label for="key">Neuer API-Key</label>'
        '<input id="key" name="OPENAI_API_KEY" type="password" autocomplete="off" '
        'placeholder="sk-… (leer lassen = unverändert)">'
        '<p class="hint">Der Key wird nur in <code>secrets.env</code> im Datenverzeichnis '
        "gespeichert und nie wieder vollständig angezeigt. Bitvavo-Keys gibt es erst mit dem "
        "Echtgeld-Meilenstein.</p>"
        '<label class="check"><input type="checkbox" name="clear_key" value="1"> '
        "gespeicherten Key löschen</label>"
        '<h2 class="sep">Depots</h2>' + depots + '<h2 class="sep">Budget und Reasoning</h2>'
        '<div class="row">'
        + amount("LLM_DAILY_BUDGET_EUR", "Gesamt € / Tag", "3")
        + amount("LLM_DAILY_BUDGET_EUR_GPT_LUNA", "gpt-luna € / Tag", "0.50")
        + amount("LLM_DAILY_BUDGET_EUR_GPT_SOL", "gpt-sol € / Tag", "2.50")
        + '<div><label for="effort">Reasoning-Effort</label>'
        f'<select id="effort" name="LLM_EFFORT">{efforts}</select></div>'
        "</div>"
        '<p class="hint">Leere Felder nutzen die Standardwerte. Gespeichert wird nur, was die '
        "Prüfung des Bots besteht.</p>"
        '<button type="submit">Speichern</button></form></div>'
        '<div class="card"><h2>Verbindung testen</h2>'
        '<p class="hint">Fragt bei OpenAI nur ab, ob das Modell gpt-6-luna erreichbar ist – '
        "kostenlos, ohne Token-Verbrauch.</p>"
        '<form method="post" action="/settings/test">'
        + hidden
        + '<button type="submit">Gespeicherten Key testen</button></form></div>'
    )
    return _page("Einstellungen", body, csrf=csrf)
