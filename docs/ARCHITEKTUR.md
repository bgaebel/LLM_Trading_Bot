# Architektur

<!-- Lebendes Dokument: beschreibt den IST-Zustand, nicht die Geschichte (die steht in ADRs).
     Ziel: Wer das liest, findet in 2 Minuten die richtige Datei für eine Änderung.
     Bei Strukturänderungen im selben Commit aktualisieren. -->

## Überblick
**IST:** Modusprüfung (`paper` ist Standard, `live` wird abgelehnt) und Marktdaten (`market`, Spec 001):
öffentliche Bitvavo-Quotes und abgeschlossene Kerzen, geprüft zu einem Snapshot je Markt. Darauf Indikatoren,
die fünf klassischen Strategien und der LLM-Kontext (`strategy`, Spec 003, identisch zu Arena v0.3.1).
Paper-Ausführung mit SQLite (`paper`, Spec 004): virtuelle Orders, Depot, Entscheidungen, Trades, Equity.
GPT-Depots (`llm`, Spec 005): `gpt-luna` (Haupt-Trader) und `gpt-sol` (Gegenprobe) plus Buy & Hold (ADR-0012),
eine OpenAI-Anfrage für beide Märkte nur bei Bedarf, hartes EUR-Budget, Provenienz je Versuch (ADR-0013).
Ein Zyklus per `uv run trading-bot paper-cycle --db <pfad>`. Weboberfläche (`web`, Spec 006/007): Login, Übersicht
(Depots, Equity-Verlauf, Lebenszeichen, Entscheidungen, GPT-Kosten, Trades; liest die Bot-DB nur lesend, ADR-0016)
und Eingabe von OpenAI-Key und Einstellungen per `uv run trading-bot web --data-dir <dir> [--db <pfad>]`, nur auf
127.0.0.1 (ADR-0015).
Dauerbetrieb (`service`, Spec 008, ADR-0017): `uv run trading-bot run --db <pfad>` führt jede Minute (:05) einen
Zyklus aus, liest die Einstellungen jede Runde neu, meldet sich bei systemd (Watchdog) und schreibt
`service-status.json` neben der DB; die Übersicht zeigt daraus das Lebenszeichen „Dienst“. Units in `deploy/`.
Diagnose per `uv run trading-bot market` und `uv run trading-bot signals`.
**Zielbild M1** (Bausteine werden schrittweise per Spec aus der Arena übernommen, ADR-0003):

```
Bitvavo REST ──> [market]  Quotes + abgeschlossene Kerzen
                    │
                    v
               [strategy]  Signal (klassisch oder LLM) ──> OpenAI
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
| `src/llm_trading_bot/cli.py` | Einstiegspunkt `main()` (`uv run trading-bot [market|signals|paper-cycle|run|web]`) |
| `src/llm_trading_bot/service/` | Dienst: Takt (`schedule`), Hauptschleife (`run`), systemd-Meldungen (`notify`), Statusdatei (`status`), Logging mit Key-Redaktion (`logs`) |
| `src/llm_trading_bot/core.py` | Reine Logik ohne I/O (aktuell: Modusauflösung) |
| `src/llm_trading_bot/market/` | Marktdaten: Modelle, HTTP-Transport, Bitvavo-Client, Snapshot-Prüfung |
| `src/llm_trading_bot/strategy/` | Indikatoren, klassische Strategien, LLM-Kontext (reine Logik, ADR-0007) |
| `src/llm_trading_bot/paper/` | Paper-Ausführung: Schema (`db`), Single-Writer-Sperre (`lock`), virtuelle Orders (`execution`), Zyklus (`cycle`), Ergebnis (`report`) |
| `src/llm_trading_bot/web/` | Weboberfläche: Login (`auth`), Zugangsdaten-Datei (`credentials`), Seiten (`pages`, Übersicht `overview_page`, Stylesheet `style`, Deko-Chart `chart`), Übersichtsdaten (`overview`), Nur-Lese-Zugriff auf die Bot-DB (`readonly`, ADR-0016), Server und Routen (`app`), private Dateien (`files`) |
| `src/llm_trading_bot/llm/` | GPT-Depots: Prompt v1 und Antwortprüfung (`contract`), Takt (`due`), Budget (`budget`), Konfiguration (`agents`), OpenAI-Adapter (`openai_client`), Ablauf je Depot (`cycle`) |
| `tools/golden/` | Generator der Golden-Fixtures aus dem Arena-Code; `mutation_check.py` prüft, dass die Tests Änderungen an Schwellen/Gewichten erkennen (nur Entwicklung) |
| `tests/` | pytest-Tests; `fakes.py` Test-Doubles; `golden_scenarios.py`/`golden.py` + `strategy/fixtures/` Golden-Tests der Strategien; `golden_execution_cases.py`/`golden_execution_runner.py` + `paper/fixtures/` Golden-Tests der Ausführung; `llm_fakes.py` Fake-Modell; Marker `net` = echte API (standardmäßig aus) |
| `deploy/` | Server-Dateien: `setup-server.sh` härtet einen frischen Server (Spec 002); systemd-Units `llm-trading-bot.service` (Dienst), `llm-trading-bot-web.service` (Weboberfläche) und `journald@llm-trading-bot.conf` mit Platzhaltern (Spec 008, Installation in Nr. 6) |
| `scripts/` | `docs-check.sh` (Doku-Hygiene), `check-units.sh` (Units mit Testwerten per `systemd-analyze verify`), `github-labels.sh` |
| `.github/workflows/` | CI (ruff, mypy, pytest, shellcheck, systemd-Units) und Doku-Prüfung |

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

### paper
- **Zweck:** Signale in virtuelle Orders umsetzen und alles dauerhaft speichern. Kein Börsenzugang (ADR-0004).
- **Schnittstelle:**
  - `db`: `open_db(path, agents=, check_same_thread=)` (legt Schema an oder prüft es, WAL, `busy_timeout`),
    `ensure_portfolios(con, agents)` (Startkapital nur für neue Depots), `check_schema`, `SchemaError`,
    `write_tx(con)` (eine `BEGIN IMMEDIATE`-Transaktion), `balance`, `START_CASH = 10_000`
  - `lock`: `WriterLock(db_path)` (Betriebssystem-Sperre neben der DB), `WriterLockBusy`
  - `execution`: `Costs` (Gebühr/Slippage, `from_env`), `record_decision`, `trade_to_target`, `mark`,
    `initialize_buyhold`, `record_quotes`, `latest_quotes`, `value`, `current_weight`
  - `cycle`: `paper_cycle(con, client, config, costs=, model=, clock=, stop_requested=) -> CycleReport`
    (bei Stopp-Wunsch keine GPT-Anfrage, Status `STOPPING`; bewertet wird trotzdem), `STRATEGY_TIMEFRAME`
  - `report`: `CycleReport`, `MarketResult`, `LlmOutcome`, `Equity`
- **Invarianten:** Schema `user_version = 2` (v2: `llm_calls`); andere Versionen oder Formen werden abgelehnt, nie
  migriert (ADR-0012). Genau ein Schreiber (`WriterLock`). Eine Entscheidung je (Agent, Markt, Bar), mit Trade und Depot in
  einer Transaktion. Ohne frische Quotes beider Märkte (≤ 20 s) kein Trade. Buy & Hold kauft je Markt einmal.
  Ausführung identisch zu Arena@7563724, belegt durch `tests/paper/fixtures/execution_golden_v0.3.1.json`
  (Grenzfälle und Verläufe bitgenau per SHA-256) und `mutation_check.py`. Der 10-%-Drawdown-Stopp der Arena ist
  noch nicht übernommen (M1 Nr. 4); `halted` wird nur gelesen. SQL-Rechnungen nicht umformen.

### llm
- **Zweck:** GPT-Depots fragen und ihre Antworten nur nach strenger Prüfung an `paper` geben (ADR-0012/0013).
  Das Modell ist Ratgeber; Grenzen, Kosten und frische Kurse prüft die Ausführung.
- **Schnittstelle:**
  - `contract`: `PROMPT_VERSION = "llm-v1"`, `SYSTEM`, `response_schema(markets)`, `user_message(payloads)`,
    `prompt_sha256()`, `parse_answer(text, markets, strategy=) -> dict[Symbol, MarketAnswer]`, `InvalidSignal`
  - `due`: `Cadence.from_env`, `llm_due(con, agent, mids, signals, now, cadence)`, `hourly_limit_reached`
  - `budget`: `Pricing`, `Limits`, `Attempt`, `Usage`, `reserve` (committet vor dem Aufruf), `settle`,
    `charged_today`, `BudgetBlocked`
  - `agents`: `load_config(env) -> BotConfig` (`classic`, `llm: LlmSettings`), `MODEL_PRICES`
  - `openai_client`: `SignalModel`-Protocol, `OpenAIResponses` (Responses API, strict `json_schema`, `store=False`,
    60 s je Anfrage, SDK ohne Wiederholung, eigene Wiederholung ≤ 2 nur bei 429/5xx), `ModelReply.problem()`,
    `ModelReply.requests`
  - `cycle`: `run_llm_depots(con, agents, settings, model, snaps, costs, refresh_quotes=, clock=)`,
    `market_triggers(snaps, costs)`
- **Invarianten:** Prompt und Schema festgeschrieben (Hash-Test); Änderung = neue `PROMPT_VERSION`. Ist ein Teil der
  Antwort ungültig, abgelehnt, unvollständig oder kein JSON: kein Trade. Je Versuch eine Zeile `llm_calls` mit
  Reservierung vor dem Aufruf für alle möglichen Anfragen; unbekannte Kosten zählen mit; Wiederholungen werden im
  schlimmsten Fall abgerechnet; Modell ohne Preis wird nie gefragt. Vor jeder Anfrage frische Quotes (≤ 20 s), nach
  der Antwort neu geholte Quotes (Mittelkurs ≤ 2 % vom Snapshot, Snapshot ≤ 120 s), sonst `STALE_QUOTE`. Der
  Signalwechsel-Auslöser vergleicht die Referenzsignale aus Marktsicht (Gewicht 0, `llm_calls.trigger_signals`). Fehler werden nur mit Typnamen
  gespeichert/ausgegeben; der API-Key erscheint nirgends.

### service
- **Zweck:** den Paper-Zyklus dauerhaft betreiben (ADR-0017): ein Prozess, ein Schreiber, eine Runde pro Minute.
- **Schnittstelle:**
  - `schedule`: `next_slot(now, after=)` (erster Termin `k·60 + 5` ≥ now und > after), `skipped(previous, slot)`
  - `run`: `Service(db, client, env=, secrets=, stop=, log=, notifier=, timer=, model_factory=, cycle=, hard_exit=)`
    mit `run() -> int` (0 gestoppt, 1 Konfiguration/Schema, 3 andere Schreibsperre, 4 Runde hängt bzw. Stopp-Frist
    überschritten); `install_stop_signals(stop)`; `STALL_SECONDS = 300`, `STOP_SECONDS = 75`, `PING_SECONDS = 20`
  - `notify`: `Notifier.from_env(env)` mit `ready`, `watchdog`, `status`, `stopping` (Datagramm an `NOTIFY_SOCKET`)
  - `status`: `ServiceStatus` (Zustand `starting`/`running`/`degraded`/`stalled`/`stopping`/`stopped`, letzte Runde,
    Fehler in Folge, nächster Termin, Konfigurationswarnung), `write_status`/`read_status`, `status_path(db)`
  - `logs`: `setup_logging(level, stream, journal=)`, `redact(text)`
- **Invarianten:** `WriterLock` die ganze Laufzeit; die Runde läuft in einem Worker-Thread, der Hauptthread wartet in
  1-s-Scheiben, pingt den Watchdog, bemerkt den Stopp und erzwingt Stall-Grenze und Stopp-Frist (dann `os._exit`,
  SQLite rollt offene Transaktionen zurück). Kein Nachholen verpasster Termine. Ungültige Einstellungen während des
  Betriebs: letzte gültige Konfiguration bleibt, eine Warnung je Fehlerbild. Neuer Key → neuer Client. Keys
  erscheinen weder im Log noch in der Statusdatei.

### web
- **Zweck:** Betreiber loggt sich ein und pflegt OpenAI-Key und Einstellungen; `paper-cycle` liest sie (ADR-0015).
- **Schnittstelle:**
  - `app`: `WebApp(data_dir, db=, key_check=)` mit `handle(method, path, cookie, form)` (für den Verbindungstest ein
    `KeyCheck`, den der Handler außerhalb der App-Sperre ausführt und mit `key_checked` beendet),
    `make_server(app, host, port)` (nur 127.0.0.1), `serve(...)`; Routen `/` (Übersicht), `/setup`, `/login`,
    `/logout`, `/settings`, `/settings/test`, `/app.css`
  - `readonly`: `reading(db)` – ein Lese-Snapshot je Seitenaufruf (`mode=ro`, `query_only`, ADR-0016), sonst
    `DbUnavailable`
  - `overview`: `load(con, now, shares, service=)` → `Overview` (Depots, Equity-Reihen über den Index ausgedünnt,
    Lebenszeichen, Entscheidungen, GPT heute, Trades, Kurse); `service_pulse(status, now)`
  - `auth`: `PasswordStore` (scrypt), `LoginGuard` (5 Fehlversuche → 60 s), `Sessions` (12 h, im Speicher)
  - `credentials`: `SecretsFile.update(form, agents=, clear_key=)` (prüft mit `load_config`), `load_env_file`, `mask`
  - `files`: `write_private` (atomar, 600)
- **Invarianten:** nur 127.0.0.1; Host-Header-Name 127.0.0.1/localhost (Port frei, SSH-Tunnel); POST mit fremdem
  `Origin`/`Sec-Fetch-Site` abgelehnt; Ersteinrichtung nur mit Einrichtungscode aus dem Terminal; jedes Formular
  mit CSRF-Token (Sitzung bzw. Double-Submit-Cookie `ltb_pre` auf Login/Einrichtung); Cookies `ltb_*`, tolerant
  gelesen; Keys nie vollständig im HTML; CSP ohne Skript, ohne Inline-Stil, Header auf jeder Antwort
  (`end_headers`); `secrets.env` ohne Steuerzeichen/Zeilenumbrüche in Werten; kein Bitvavo-Key-Feld (ADR-0004).
  `secrets.env` und `web-auth.json` liegen im Datenverzeichnis, sind git-ignoriert, nie im Repo.

## Datenfluss & Nebenläufigkeit
`build_snapshots` holt je Markt die Kerzen für 1/15/60/240 min, danach einmal die Quotes (sie veralten am
schnellsten), dann die Prüfung je Markt. `paper_cycle`: Quotes holen und speichern → frische Quotes aus der DB
(sonst Ende ohne Entscheidung) → Buy & Hold initialisieren → Snapshots → je Markt eine Entscheidung pro neuem
abgeschlossenem Bar des Strategie-Zeitrahmens (trend 60 min, sonst 15 min) → GPT-Depots (luna, sol) mit demselben
Snapshot: je Depot frische Quotes? fällig? (stündlich, Bewegung ≥ 1,8 % oder Wechsel eines Referenzsignals, ≥ 10 min
Abstand, ≤ 4/h) → Reservierung; dann alle Anfragen gleichzeitig (Threads, nur Netz) → Quotes einmal neu holen → je
Depot Abrechnung → Prüfung → Entscheidung je Markt → Equity-Punkt je Depot. Ein Fehler eines Depots blockiert die
anderen nicht.
Der Dienst (`service.run`) startet diesen Zyklus jede Minute um :05 in einem Worker-Thread; dauert er länger, folgt
die nächste Runde am nächsten freien Termin. Anders als in der Arena (getrennte Kurs-, Signal- und LLM-Worker) läuft
alles in einer Runde (ADR-0017); ein schneller Kurs-/Risiko-Worker folgt mit M1 Nr. 4. Die Weboberfläche liest
parallel nur lesend (ADR-0016) und dazu `service-status.json`.

## Externe Schnittstellen
| Schnittstelle | Protokoll / Format | Spezifikation |
|---|---|---|
| Bitvavo Marktdaten | REST/JSON, öffentlich | https://docs.bitvavo.com/ |
| OpenAI Responses API | REST/JSON über Paket `openai`; `OPENAI_API_KEY` aus der Umgebung oder `secrets.env` (Server: `__DATA_DIR__/secrets.env`, 600, per Weboberfläche gepflegt; Umgebung gewinnt) | https://developers.openai.com/api/docs |

## Build, Konfiguration & Deployment
Build/Test lokal mit uv (siehe `CLAUDE.md`). Konfiguration über Umgebungsvariablen: `TRADING_MODE`; `BOT_AGENTS`
(Standard `gpt-luna,gpt-sol`, klassische wählbar); `TAKER_FEE` 0,0025 und `SLIPPAGE` 0,0005 je Seite, erlaubt
[0, 0,05]; `OPENAI_API_KEY`; `LLM_EFFORT` (`low`); `LLM_DAILY_BUDGET_EUR` (3) und `LLM_DAILY_BUDGET_EUR_GPT_LUNA`/
`_GPT_SOL` (0,50/2,50); `LLM_MIN_INTERVAL_SECONDS` (3600), `LLM_EVENT_COOLDOWN_SECONDS` (600),
`LLM_EVENT_MOVE_THRESHOLD` (0,018), `LLM_MAX_CALLS_PER_HOUR` (4); optional `OPENAI_MODEL_<DEPOT>` mit
`LLM_PRICE_IN_EUR_<DEPOT>`/`LLM_PRICE_OUT_EUR_<DEPOT>`. In `__DATA_DIR__/secrets.env` (600, Dienstbenutzer,
geschrieben von der Weboberfläche, ADR-0015) stehen nur `OPENAI_API_KEY`, `BOT_AGENTS`, `LLM_EFFORT` und die drei
Budgets (`web/credentials.FIELDS`); andere Zeilen dort werden ignoriert und beim nächsten Speichern entfernt. Alle
übrigen Variablen kommen nur aus der Umgebung des Prozesses (auf dem Server: Unit bzw. Drop-in, Nr. 6). Der Dienst
liest Umgebung und `secrets.env` jede Runde neu, Umgebungswerte gewinnen (ADR-0017). Ziel: Hetzner Cloud CX23, Ubuntu 24.04, systemd-Unit.
Dazu `LOG_LEVEL` (Standard `INFO`) für `run`. Server ist gehärtet (Spec 002, Ablauf und Prüfbefehle:
`docs/betrieb/server.md`); die systemd-Dateien liegen geprüft in `deploy/` (Spec 008: `Type=notify`,
`WatchdogSec=60`, `TimeoutStopSec=90`, `Restart=on-failure`, Härtung, Journal ≤ 200 MB / 14 Tage). Installation,
Verzeichnisse, Backup und Update/Rollback folgen in M1 Nr. 6.

## Teststrategie
- **Host-Unit-Tests:** pytest, Logik ohne I/O; Netzwerk über schmale Adapter, in Tests ersetzt.
- **Integration:** echte API-Aufrufe mit Marker kennzeichnen und in CI ausschließen.
- **Nicht automatisiert getestet:** Server-Betrieb (Reboot, Netzausfall, Langlauf) – manuell auf dem CX23.
