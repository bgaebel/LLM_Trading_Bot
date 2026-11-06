# Architektur

<!-- Lebendes Dokument: beschreibt den IST-Zustand, nicht die Geschichte (die steht in ADRs).
     Ziel: Wer das liest, findet in 2 Minuten die richtige Datei für eine Änderung.
     Bei Strukturänderungen im selben Commit aktualisieren. -->

## Überblick
**IST:** Modusprüfung (`paper` ist Standard, `live` wird abgelehnt) und Marktdaten (`market`, Spec 001):
öffentliche Bitvavo-Quotes und abgeschlossene Kerzen, geprüft zu einem Snapshot je Markt. Darauf Indikatoren,
die fünf klassischen Strategien und der LLM-Kontext (`strategy`, Spec 003, identisch zu Arena v0.3.1).
Diagnose per `uv run trading-bot market` und `uv run trading-bot signals`.
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
| `src/llm_trading_bot/cli.py` | Einstiegspunkt `main()` (`uv run trading-bot [market|signals]`) |
| `src/llm_trading_bot/core.py` | Reine Logik ohne I/O (aktuell: Modusauflösung) |
| `src/llm_trading_bot/market/` | Marktdaten: Modelle, HTTP-Transport, Bitvavo-Client, Snapshot-Prüfung |
| `src/llm_trading_bot/strategy/` | Indikatoren, klassische Strategien, LLM-Kontext (reine Logik, ADR-0007) |
| `tools/golden/` | Generator der Golden-Fixtures aus dem Arena-Code; `mutation_check.py` prüft, dass die Tests Änderungen an Schwellen/Gewichten erkennen (nur Entwicklung) |
| `tests/` | pytest-Tests; `fakes.py` Test-Doubles; `golden_scenarios.py`/`golden.py` + `strategy/fixtures/` Golden-Tests; Marker `net` = echte API (standardmäßig aus) |
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
  - `http`: `Transport`-Protocol, `UrllibTransport` (nur GET), `HTTPFailure(status, retry_after)`,
    `TransportError` (abgebrochene/unlesbare Antwort)
  - `bitvavo`: `BitvavoClient.quotes()`, `.closed_candles(symbol, interval, now_epoch=)`, `fill_tiny_gaps()`
  - `snapshot`: `build_snapshots(client, clock=, min_bars=, on_error=)` → `dict[Symbol, MarketSnapshot]`;
    `DATA_ERRORS` = Fehler, die als „Markt gerade unbrauchbar“ gelten (alles andere ist ein Bug und wird geworfen)
- **Invarianten:** nur öffentliche GET-Endpunkte, keine Keys (ADR-0004); nur abgeschlossene Kerzen; höchstens 2
  fehlende 1m-Kerzen werden ergänzt; Snapshot ungültig bei Lücke in den letzten 60 Kerzen, veralteten Daten,
  Quote älter als 20 s, Spread > 50 bp oder Uhrzeitabweichung > 120 s. Nach HTTP 429 keine Anfrage bis zum Ende
  der Pause (eine `BitvavoClient`-Instanz je Prozess). Ein Markt blockiert den anderen nicht; ein Ausfall von
  `/ticker/book` betrifft beide.

### strategy
- **Zweck:** aus einem `MarketSnapshot` Indikatoren, Signale und den LLM-Kontext berechnen. Kein Netz, keine Orders.
- **Schnittstelle:**
  - `indicators`: `ema`, `rsi`, `atr`, `adx`, `indicators(snapshot, roundtrip_cost_frac)` → `Indicators` (TypedDict)
  - `signals`: `Signal` (action, target_fraction ∈ {0, 0.25, 0.45}, confidence, reason, strategy, version),
    `STRATEGIES`, `STRATEGY_VERSION = "v0.3.1"`, `run_strategy(name, snapshot, indicators, current_weight)`
  - `payload`: `feature_payload(...)` (Kontext der LLM-Agenten), `cost_roundtrip(spread_bps, fee, slippage)`
- **Invarianten:** identisch zu Arena@7563724, belegt durch `tests/strategy/fixtures/strategy_golden_v0.3.1.json`
  (relativ 1e-12, Texte exakt; Grenzfälle und LLM-JSON exakt per SHA-256). Änderungen nur als neue
  Strategieversion; Fixtures nie anpassen (ADR-0007). `uv run python tools/golden/mutation_check.py` muss
  alle Mutationen außer den dokumentierten äquivalenten erkennen.
  Rechenreihenfolge nicht umformen. Deutsche `reason`-Texte sind Teil des Vertrags.

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
