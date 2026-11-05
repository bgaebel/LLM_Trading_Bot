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

Lint und Typprüfung: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`

Live-Marktdaten einmal abrufen und prüfen (öffentliche Bitvavo-API, kein Key nötig):

```bash
uv run trading-bot market
```

Signale der klassischen Strategien (Arena v0.3.1) auf Live-Daten:

```bash
uv run trading-bot signals
```

Tests gegen die echte API sind standardmäßig aus: `uv run pytest -m net`.

Konfiguration: `TRADING_MODE` (Standard `paper`; `live` wird abgelehnt).

## Dokumentation

- [docs/PROJEKT.md](docs/PROJEKT.md) – Ziel, Nicht-Ziele, Randbedingungen
- [docs/ROADMAP.md](docs/ROADMAP.md) – Meilensteine
- [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md) – Aufbau
- [docs/entscheidungen/](docs/entscheidungen/INDEX.md) – Architekturentscheidungen

## Entwicklung

Gearbeitet wird mit Claude Code nach den Regeln in [CLAUDE.md](CLAUDE.md): Specs vor Code, ADRs sind bindend.
Secrets, Datenbanken und Backups gehören nie ins Repository.
