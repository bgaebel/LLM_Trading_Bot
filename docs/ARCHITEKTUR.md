# Architektur

<!-- Lebendes Dokument: beschreibt den IST-Zustand, nicht die Geschichte (die steht in ADRs).
     Ziel: Wer das liest, findet in 2 Minuten die richtige Datei für eine Änderung.
     Bei Strukturänderungen im selben Commit aktualisieren. -->

## Überblick
**IST:** Modusprüfung (`paper` ist Standard, `live` wird abgelehnt) und Marktdaten (`market`, Spec 001):
öffentliche Bitvavo-Quotes und abgeschlossene Kerzen, geprüft zu einem Snapshot je Markt. Diagnose per
`uv run trading-bot market`.
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
| `src/llm_trading_bot/` | Anwendungspaket |
| `src/llm_trading_bot/cli.py` | Einstiegspunkt `main()` (`uv run trading-bot [market]`) |
| `src/llm_trading_bot/core.py` | Reine Logik ohne I/O (aktuell: Modusauflösung) |
| `src/llm_trading_bot/market/` | Marktdaten: Modelle, HTTP-Transport, Bitvavo-Client, Snapshot-Prüfung |
| `tests/` | pytest-Tests; `tests/fakes.py` Test-Doubles; Marker `net` = echte API (standardmäßig aus) |
| `deploy/` | Server-Skripte; `setup-server.sh` härtet einen frischen Server (Spec 002) |
| `.github/workflows/` | CI (ruff, mypy, pytest, shellcheck) und Doku-Prüfung |

## Module & Schnittstellen
### core
- **Zweck:** Konfigurationslogik ohne I/O.
- **Schnittstelle:** `parse_mode(value) -> TradingMode`
- **Invarianten:** fehlender Wert ⇒ `PAPER`; `live` ⇒ `NotImplementedError` bis M2 · relevante ADRs: ADR-0004

### market
- **Zweck:** öffentliche Marktdaten holen und nur geprüfte Daten weitergeben (fail closed). Aus der Arena übernommen (ADR-0003).
- **Schnittstelle:**
  - `models`: `Symbol` (BTCEUR/ETHEUR), `Quote`, `Candle`, `MarketDataError`
  - `http`: `Transport`-Protocol, `UrllibTransport` (nur GET), `HTTPFailure(status, retry_after)`
  - `bitvavo`: `BitvavoClient.quotes()`, `.closed_candles(symbol, interval, now_epoch=)`, `fill_tiny_gaps()`
  - `snapshot`: `build_snapshots(client, clock=, min_bars=, on_error=)` → `dict[Symbol, MarketSnapshot]`
- **Invarianten:** nur öffentliche GET-Endpunkte, keine Keys (ADR-0004); nur abgeschlossene Kerzen; höchstens 2
  fehlende 1m-Kerzen werden ergänzt; Snapshot ungültig bei Lücke in den letzten 60 Kerzen, veralteten Daten,
  Quote älter als 20 s, Spread > 50 bp oder Uhrzeitabweichung > 120 s. Nach HTTP 429 keine Anfrage bis zum Ende
  der Pause (eine `BitvavoClient`-Instanz je Prozess). Ein Markt blockiert den anderen nicht; ein Ausfall von
  `/ticker/book` betrifft beide.

## Datenfluss & Nebenläufigkeit
`build_snapshots` holt je Markt die Kerzen für 1/15/60/240 min, danach einmal die Quotes (sie veralten am
schnellsten), dann die Prüfung je Markt. Worker-Schleife und Takt folgen in M1 Nr. 5. Vorgabe aus der Arena: Marktabfrage, Signal- und LLM-Worker laufen getrennt; ein
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
