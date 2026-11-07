# LLM Trading Bot

Trading-Bot für BTC/EUR und ETH/EUR, der Module der
[LLM Trading Arena](https://github.com/bgaebel/LLM-Trading-Arena) übernimmt und 24/7 auf einem
Hetzner-Cloud-Server läuft. **Aktuell nur Paper-Modus** – echte Orders sind gesperrt, bis ein eigener
Meilenstein sie freigibt.

## Setup

Voraussetzung: [uv](https://docs.astral.sh/uv/) (Windows: `winget install --id astral-sh.uv -e`), Python 3.12.

```bash
uv sync
uv run pytest
uv run trading-bot
```

Lint und Typprüfung: `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`

Live-Marktdaten einmal abrufen und prüfen (öffentliche Bitvavo-API, kein Key nötig):

```bash
uv run trading-bot market
```

Signale der klassischen Strategien (Arena v0.3.1) auf Live-Daten:

```bash
uv run trading-bot signals
```

Einen Paper-Zyklus ausführen (virtuelle Orders, Daten in SQLite; die DB liegt außerhalb des Repos). Standard sind
die GPT-Depots `gpt-luna` (Haupt-Trader) und `gpt-sol` (Gegenprobe) plus Buy & Hold; dafür muss `OPENAI_API_KEY`
in der Shell gesetzt sein (nie in eine Datei im Repo schreiben):

```bash
uv run trading-bot paper-cycle --db <pfad>/bot.db
```

Key und Einstellungen lassen sich auch über die lokale Weboberfläche eintragen (nur `127.0.0.1`; auf dem Server per
SSH-Tunnel). Beim ersten Start steht ein Einrichtungscode im Terminal, damit legst du dein Passwort fest. Liegt die
Datenbank im selben Verzeichnis, liest `paper-cycle` die gespeicherten Werte automatisch. Die Startseite zeigt eine
Übersicht aus der Bot-DB (`<pfad>/bot.db`, anderer Ort per `--db`), nur lesend, live aktualisiert:

```bash
uv run trading-bot web --data-dir <pfad>
```

Ohne Key nur mit klassischen Strategien, z. B. `BOT_AGENTS=trend uv run trading-bot paper-cycle --db <pfad>/bot.db`.

Risiko (wie die Arena): Fällt ein Depot 10 % unter seinen Höchststand, verkauft der Bot dessen Coins und stoppt es;
die Weboberfläche zeigt den Abstand zum Hoch, gibt gestoppte Depots wieder frei und hat einen Schalter „Handel
pausieren“ (im Zweifel Pause). Ein ungültiger Kurs eines Marktes blockiert den anderen nicht.

Dauerbetrieb: `run` führt jede Minute (5 s nach dem Minutenwechsel) einen Paper-Zyklus aus, bis Strg+C bzw. SIGTERM.
Einstellungen aus der Weboberfläche greifen ab der nächsten Runde; je Runde eine Logzeile, Stufe per `LOG_LEVEL`.
Die Übersicht zeigt das Lebenszeichen „Dienst“. Auf dem Server läuft das später als systemd-Dienst (`deploy/`):

```bash
uv run trading-bot run --db <pfad>/bot.db
```

Tests gegen die echten APIs sind standardmäßig aus: `uv run pytest -m net` (der OpenAI-Test nur mit Key, < 0,01 $).

Konfiguration per Umgebungsvariablen: `TRADING_MODE` (Standard `paper`; `live` wird abgelehnt), `BOT_AGENTS`
(Standard `gpt-luna,gpt-sol`), `TAKER_FEE` und `SLIPPAGE` (Standard 0,25 % bzw. 0,05 % je Seite),
`LLM_DAILY_BUDGET_EUR` (Standard 3 € pro UTC-Tag), `LLM_EFFORT` (Standard `low`); alle weiteren in
[docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md#build-konfiguration--deployment). Zusätzlich ein Ausgabenlimit im
OpenAI-Konto setzen.

## Dokumentation

- [docs/PROJEKT.md](docs/PROJEKT.md) – Ziel, Nicht-Ziele, Randbedingungen
- [docs/ROADMAP.md](docs/ROADMAP.md) – Meilensteine
- [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md) – Aufbau
- [docs/entscheidungen/](docs/entscheidungen/INDEX.md) – Architekturentscheidungen

## Entwicklung

Gearbeitet wird mit Claude Code nach den Regeln in [CLAUDE.md](CLAUDE.md): Specs vor Code, ADRs sind bindend.
Secrets, Datenbanken und Backups gehören nie ins Repository.
