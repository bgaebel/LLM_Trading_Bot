# Architektur

<!-- Lebendes Dokument: beschreibt den IST-Zustand, nicht die Geschichte (die steht in ADRs).
     Ziel: Wer das liest, findet in 2 Minuten die richtige Datei für eine Änderung.
     Bei Strukturänderungen im selben Commit aktualisieren. -->

## Überblick
**IST:** Gerüst – Paket `llm_trading_bot` mit Modusprüfung (`paper` ist Standard, `live` wird abgelehnt).
**Zielbild M1** (Bausteine werden schrittweise per Spec aus der Arena übernommen, ADR-0003):

```
Bitvavo REST ──> [market]  Quotes + abgeschlossene Kerzen
                    │
                    v
               [strategy]  Signal (klassisch oder LLM) ──> OpenAI / Anthropic
                    │
                    v
                 [risk]    Limits, Fail-closed bei veralteten Daten
                    │
                    v
               [execution] Paper-Broker (später: Live-Broker hinter derselben Schnittstelle)
                    │
                    v
                [store]    SQLite (WAL, Single-Writer) ──> [backup]
                    │
                    v
               [status]    HTTP nur 127.0.0.1  <── SSH-Tunnel vom Desktop
```

## Verzeichnisse
| Pfad | Verantwortung |
|---|---|
| `src/llm_trading_bot/` | Anwendungspaket, Einstiegspunkt `main()` (`uv run trading-bot`) |
| `src/llm_trading_bot/core.py` | Reine Logik ohne I/O (aktuell: Modusauflösung) |
| `tests/` | pytest-Tests |
| `deploy/` | Server-Skripte; `setup-server.sh` härtet einen frischen Server (Spec 002) |
| `.github/workflows/` | CI (ruff, mypy, pytest, shellcheck) und Doku-Prüfung |

## Module & Schnittstellen
### core
- **Zweck:** Konfigurationslogik ohne I/O.
- **Schnittstelle:** `parse_mode(value) -> TradingMode`
- **Invarianten:** fehlender Wert ⇒ `PAPER`; `live` ⇒ `NotImplementedError` bis M2 · relevante ADRs: ADR-0004

## Datenfluss & Nebenläufigkeit
Noch nicht umgesetzt. Vorgabe aus der Arena: Marktabfrage, Signal- und LLM-Worker laufen getrennt; ein
langsamer LLM-Aufruf blockiert die Marktabfrage nicht; ein Fehler bei BTC blockiert ETH nicht.

## Externe Schnittstellen
| Schnittstelle | Protokoll / Format | Spezifikation |
|---|---|---|
| Bitvavo Marktdaten | REST/JSON, öffentlich | https://docs.bitvavo.com/ |
| OpenAI / Anthropic | REST/JSON, API-Key in `.env` auf dem Server | jeweilige API-Doku |

## Build, Konfiguration & Deployment
Build/Test lokal mit uv (siehe `CLAUDE.md`). Konfiguration über Umgebungsvariablen (`TRADING_MODE`, später
`.env` auf dem Server, Rechte 600). Ziel: Hetzner Cloud CX23, Ubuntu 24.04, systemd-Unit.
Server ist gehärtet (Spec 002, Ablauf und Prüfbefehle: `docs/betrieb/server.md`); das Deployment des Bots
(Unit, Verzeichnisse, Backup, Update/Rollback) folgt in M1 Nr. 6.

## Teststrategie
- **Host-Unit-Tests:** pytest, Logik ohne I/O; Netzwerk über schmale Adapter, in Tests ersetzt.
- **Integration:** echte API-Aufrufe mit Marker kennzeichnen und in CI ausschließen.
- **Nicht automatisiert getestet:** Server-Betrieb (Reboot, Netzausfall, Langlauf) – manuell auf dem CX23.
