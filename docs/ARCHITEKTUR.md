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
127.0.0.1 (ADR-0015); auf dem Server von außen per HTTPS über Caddy (`--public-host <domain>`, Spec 013, ADR-0024)
oder per SSH-Tunnel.
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
               [execution] Paper-Broker (später: Live-Broker hinter `broker.Broker`, ADR-0022)
                    │
                    v
                [store]    SQLite (WAL, Single-Writer) ──> [backup]
                    │
                    v
               [status]    HTTP nur 127.0.0.1  <── SSH-Tunnel (:8787) / Caddy HTTPS (:8788, ADR-0024)
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
| `src/llm_trading_bot/broker/` | Broker-Naht für M2 (Spec 012, ADR-0022): Vertrag `Broker` und Börsenseite `OrderExchange` mit Live-Typen (`contract`), Paper-Delegation (`paper`), Live-Sperre (`live`), Zustandsmaschine der Order-Vorgänge (`intents`) – ohne I/O, ohne Börsen-Implementierung |
| `src/llm_trading_bot/paper/` | Paper-Ausführung: Schema (`db`), Single-Writer-Sperre (`lock`), virtuelle Orders (`execution`), Zyklus (`cycle`), Ergebnis (`report`) |
| `src/llm_trading_bot/web/` | Weboberfläche: Login (`auth`), Zugangsdaten-Datei (`credentials`), Seiten (`pages`, Übersicht `overview_page`, Stylesheet `style`, Deko-Chart `chart`, Live-Skript `script` als `/app.js`), Übersichtsdaten (`overview`), Nur-Lese-Zugriff auf die Bot-DB (`readonly`, ADR-0016), Server und Routen (`app`), private Dateien (`files`) |
| `src/llm_trading_bot/ops/` | Betrieb: geprüfte Backups, Prüfung und Wiederherstellung der Bot-DB (`backup`, ADR-0018) |
| `src/llm_trading_bot/llm/` | GPT-Depots: Prompt v1 und Antwortprüfung (`contract`), Takt (`due`), Budget (`budget`), Konfiguration (`agents`), OpenAI-Adapter (`openai_client`), Ablauf je Depot (`cycle`) |
| `tools/golden/` | Generator der Golden-Fixtures aus dem Arena-Code; `mutation_check.py` prüft, dass die Tests Änderungen an Schwellen/Gewichten erkennen (nur Entwicklung) |
| `tests/` | pytest-Tests; `broker/` Vertrag, Sperren und Order-Pfad-Prüfung (Spec 012), `broker/fixtures/markets_2026-11.json` öffentliche `/markets`-Antwort; `fakes.py` Test-Doubles; `golden_scenarios.py`/`golden.py` + `strategy/fixtures/` Golden-Tests der Strategien; `golden_execution_cases.py`/`golden_execution_runner.py` + `paper/fixtures/` Golden-Tests der Ausführung; `llm_fakes.py` Fake-Modell; Marker `net` = echte API (standardmäßig aus) |
| `deploy/` | Server-Dateien: `setup-server.sh` härtet einen frischen Server (Spec 002); `release.sh` installiert, aktualisiert und rollt zurück (Spec 009, ADR-0018); systemd-Units `llm-trading-bot.service` (Dienst), `llm-trading-bot-web.service` (Weboberfläche), `llm-trading-bot-backup.service`/`.timer` (tägliches Backup) und `journald@llm-trading-bot.conf` mit Platzhaltern |
| `scripts/` | `deploy.sh` (Deploy von hier: nur geprüfte Commits von `origin/main`, Spec 009), `docs-check.sh` (Doku-Hygiene), `check-units.sh` (Units mit Testwerten per `systemd-analyze verify`), `github-labels.sh` |
| `.github/workflows/` | CI (ruff, mypy, pytest, shellcheck, systemd-Units) und Doku-Prüfung |

## Module & Schnittstellen
### core
- **Zweck:** Konfigurationslogik ohne I/O.
- **Schnittstelle:** `parse_mode(value) -> TradingMode`
- **Invarianten:** fehlender Wert ⇒ `PAPER`; `live` ⇒ `NotImplementedError` bis M2 · relevante ADRs: ADR-0004

### market
- **Zweck:** öffentliche Marktdaten holen und nur geprüfte Daten weitergeben (fail closed). Aus der Arena übernommen (ADR-0003).
- **Schnittstelle:**
  - `models`: `Symbol` (BTCEUR/ETHEUR), `Quote`, `QuoteBook` (gültige Quotes je Markt + `errors`), `Candle`,
    `MarketDataError`
  - `http`: `Transport`-Protocol, `UrllibTransport` (nur GET), `HTTPFailure(status, retry_after)`,
    `TransportError` (abgebrochene/unlesbare Antwort)
  - `bitvavo`: `BitvavoClient.quotes() -> QuoteBook`, `.closed_candles(symbol, interval, now_epoch=)`,
    `fill_tiny_gaps()`
  - `snapshot`: `build_snapshots(client, clock=, min_bars=, on_error=)` → `dict[Symbol, MarketSnapshot]`;
    `DATA_ERRORS` = Fehler, die als „Markt gerade unbrauchbar“ gelten (alles andere ist ein Bug und wird geworfen)
- **Invarianten:** nur öffentliche GET-Endpunkte, keine Keys (ADR-0004); nur abgeschlossene Kerzen; höchstens 2
  fehlende 1m-Kerzen werden ergänzt; Snapshot ungültig bei Lücke in den letzten 60 Kerzen, veralteten Daten,
  Quote älter als 20 s, Spread > 50 bp oder Uhrzeitabweichung > 120 s. Nach HTTP 429 keine Anfrage bis zum Ende
  der Pause (eine `BitvavoClient`-Instanz je Prozess). Ein Markt blockiert den anderen nicht – auch nicht bei
  den Quotes: ein ungültiger, doppelter oder fehlender Eintrag kostet nur seinen Markt (Spec 011); nur eine
  unlesbare Antwort von `/ticker/book` als Ganzes betrifft beide.

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
  - `execution`: `Costs` (Gebühr/Slippage, `from_env`), `record_decision(..., stops=, partial=)`,
    `trade_to_target`, `mark(con, agent, qs, now, costs) -> RiskStop | None` (Drawdown-Stopp, `DRAWDOWN_KILL =
    0.10`), `initialize_buyhold`, `record_quotes` (je Markt), `fresh_quotes` (je Markt), `latest_quotes` (beide
    oder None), `value`, `current_weight`, `covers`, `unquoted_holdings`
  - `cycle`: `paper_cycle(con, client, config, costs=, model=, clock=, stop_requested=, controls=) -> CycleReport`
    (bei Stopp-Wunsch keine GPT-Anfrage, Status `STOPPING`; bewertet wird trotzdem), `STRATEGY_TIMEFRAME`,
    `quote_problem`
  - `risk`: `read_controls(env, secrets) -> Controls` (Handelspause, Freigaben; fail-closed),
    `release_problem` (nur lesend, auch für die Weboberfläche), `release(con, agent, requested_at, now)` (gibt ein gestopptes Depot frei, wenn die Anforderung jünger als sein letzter Trade
    und höchstens `RESUME_MAX_AGE = 24 h` alt ist), `parse_resumes`
  - `report`: `CycleReport` (u. a. `risk_stops`, `unvalued`, `paused`, `released`, `refused`), `MarketResult`
    (Status `HALTED`/`PAUSED` sind keine Fehler), `LlmOutcome`, `Equity`
- **Invarianten:** Schema `user_version = 2` (v2: `llm_calls`); andere Versionen oder Formen werden abgelehnt, nie
  migriert (ADR-0012). Genau ein Schreiber (`WriterLock`). Eine Entscheidung je (Agent, Markt, Bar), mit Trade und Depot in
  einer Transaktion. Kurse je Markt (Spec 011): ein Markt ohne frischen Quote (≤ 20 s) bekommt keine Entscheidung
  (Kerze bleibt offen); hält das Depot dort Bestand, entscheidet es auch im anderen Markt nicht und bekommt keinen
  Equity-Punkt. Vor den klassischen Entscheidungen und vor der Schlussbewertung werden Quotes, die älter als 20 s
  sind, neu geholt. Ein Markt ohne Bestand trägt genau 0,0 bei, deshalb sind Entscheidungen mit Teil-Quotes
  bitgleich zu denen mit beiden (`partial=True` nur aus den Zyklen; Arena-Aufrufe ohne beide Quotes bleiben
  `STALE_QUOTE`). Buy & Hold kauft je Markt einmal und nur mit beiden Quotes.
  Drawdown-Stopp wie Arena (`mark_and_risk`, ADR-0021): Equity < 90 % des Allzeithochs (Mittelkurs) → `halted`,
  Zwangsverkauf aller Bestände als `RISK_SELL` zu `bid × (1 − Slippage)` mit Gebühr (auch unter 50 €), nie für
  Buy & Hold; geprüft bei jeder Bewertung. Freigabe nur auf Anforderung aus der Weboberfläche; dann
  `peak = cash`. Handelspause: keine Trades außer `RISK_SELL`, klassische Entscheidungen als `PAUSED`.
  Ausführung identisch zu Arena@7563724, belegt durch `tests/paper/fixtures/execution_golden_v0.3.1.json` und
  `execution_drawdown_golden_v0.3.1.json` (Grenzfälle und Verläufe bitgenau per SHA-256; `--check` des Generators
  belegt die ältere Datei bytegleich) und `mutation_check.py`. SQL-Rechnungen nicht umformen.

### broker
- **Zweck:** Vertrag, an den M2 einen Live-Broker anschließt (Spec 012, ADR-0022; Entwurf für M2 in ADR-0023).
  In M1 rufen die Zyklen weiter `paper.execution` direkt auf; nichts im laufenden Dienst nutzt das Paket.
- **Schnittstelle:**
  - `contract`: `Broker` (Signaturen von `paper.execution` plus `unresolved`, `dispatch`); Börsenseite
    `OrderExchange` (nur Protocol), `OrderIntent` (BUY: EUR-Budget, SELL/RISK_SELL: Basismenge; UUID4 als
    `client_order_id`, `operator_id: int`), `ExecutedFill`, `OrderOutcome` (Zustand nach ausgeführter Menge,
    `classify`), `MarketRules` (aus dem öffentlichen `GET /markets`), `AccountBalances` – Beträge nur `Decimal`
  - `paper`: `PaperBroker` (reine Delegation); `live`: `LiveTradingLocked`, `refuse_live`;
    `make_broker(mode)` (LIVE → `LiveTradingLocked`)
  - `intents`: `ALLOWED`, `move`, `on_restart` (PLANNED → UNKNOWN), `may_plan`
- **Invarianten:** Kein Order-Code (ADR-0004). `tests/broker/test_no_order_path.py` prüft statisch: in
  `src`, `tests` und `tools` keine privaten Bitvavo-Pfade, kein Auth-Header, keine WebSocket-Order-Aktionen
  (auch als Bytes) und keine börsenartige Klasse; in `src` zusätzlich nur die öffentlichen GET-Pfade, HTTP nur
  über `urllib` in `market/http.py`, keine weitere HTTP-/Prozess-Bibliothek, `hmac` nur in `web/auth.py`, keine
  `BITVAVO_*`/`LIVE_*`-Namen; in `scripts` und `deploy` keine dieser Pfade und kein sendendes `curl` in einer
  Datei, die Bitvavo anspricht; in der Paketliste kein Börsen-SDK – jede Regel mit Negativtest.
  Die Arena-Regeln (Größe, Limits, Risikostopp) liegen in `paper.execution` *unterhalb* der Naht; M2 löst sie golden-geprüft heraus, bevor ein Live-Broker sie nutzt. Beide Golden-Fixtures laufen
  zusätzlich über den `PaperBroker`. Ein Vorgang wird nie erneut gesendet (`intents`).

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
  - `cycle`: `run_llm_depots(con, agents, settings, model, snaps, costs, refresh_quotes=, clock=, stops=,
    paused=)`, `market_triggers(snaps, costs)`
- **Invarianten:** Prompt und Schema festgeschrieben (Hash-Test); Änderung = neue `PROMPT_VERSION`. Ist ein Teil der
  Antwort ungültig, abgelehnt, unvollständig oder kein JSON: kein Trade. Je Versuch eine Zeile `llm_calls` mit
  Reservierung vor dem Aufruf für alle möglichen Anfragen; unbekannte Kosten zählen mit; Wiederholungen werden im
  schlimmsten Fall abgerechnet; Modell ohne Preis wird nie gefragt. Gefragt wird nach den Märkten mit frischem
  Quote (≤ 20 s); fehlt einem gehaltenen Markt der Quote, keine Anfrage (`NO_QUOTES`). Nach der Antwort neu
  geholte Quotes je Markt (Mittelkurs ≤ 2 % vom Snapshot, Snapshot ≤ 120 s), sonst nur dieser Markt
  `STALE_QUOTE`. Gestoppt (`HALTED`) oder pausiert (`PAUSED`): keine Anfrage, keine Kosten. Der
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
    Fehler in Folge, nächster Termin, Konfigurationswarnung, angewandte Handelspause und ihre Quelle),
    `write_status`/`read_status` (ältere Dateien ohne die Pausenfelder bleiben lesbar), `status_path(db)`
  - `logs`: `setup_logging(level, stream, journal=)`, `redact(text)`
- **Invarianten:** `WriterLock` die ganze Laufzeit; die Runde läuft in einem Worker-Thread, der Hauptthread wartet in
  1-s-Scheiben, pingt den Watchdog, bemerkt den Stopp und erzwingt Stall-Grenze und Stopp-Frist (dann `os._exit`,
  SQLite rollt offene Transaktionen zurück). Kein Nachholen verpasster Termine. Ungültige Einstellungen während des
  Betriebs: letzte gültige Konfiguration bleibt, eine Warnung je Fehlerbild. Neuer Key → neuer Client. Keys
  erscheinen weder im Log noch in der Statusdatei. Handelspause und Freigaben liest der Dienst jede Runde getrennt
  davon und fail-closed (unlesbar/ungültig = Pause; Umgebung gewinnt). Ein gestopptes Depot ist kein
  Rundenfehler; jeder Stopp erscheint einmal als WARNING im Journal.

### web
- **Zweck:** Betreiber loggt sich ein und pflegt OpenAI-Key und Einstellungen; `paper-cycle` liest sie (ADR-0015).
- **Schnittstelle:**
  - `app`: `WebApp(data_dir, db=, key_check=, public_host=)` mit `handle(method, path, cookie, form, access)`
    (`Access`: Tunnel oder öffentlich mit Client-Adresse; für den Verbindungstest ein `KeyCheck`, den der Handler
    außerhalb der App-Sperre ausführt und mit `key_checked` beendet), `make_server(app, host, port, public=)` (nur
    127.0.0.1), `serve(..., public_host=, public_port=)`; Routen `/` (Übersicht), `/setup`, `/login`, `/logout`,
    `/settings`, `/settings/test`, `/settings/pause`, `/settings/resume`, `/settings/password`, `/app.css`,
    `/app.js`
  - `readonly`: `reading(db)` – ein Lese-Snapshot je Seitenaufruf (`mode=ro`, `query_only`, ADR-0016), sonst
    `DbUnavailable`
  - `overview`: `load(con, now, shares, service=)` → `Overview` (Depots, Equity-Reihen über den Index ausgedünnt,
    Lebenszeichen, Entscheidungen, GPT heute, Trades, Kurse); `service_pulse(status, now)`
  - `auth`: `PasswordStore` (scrypt; neue Passwörter ≥ 12 Zeichen, nicht häufig), `password_problem`,
    `LoginGuard` (5 Fehlversuche → 60 s; öffentlich verdoppelnd bis 1 h), `PublicGuard` (je Client-Adresse, IPv6
    je /64, globale Obergrenze), `DeviceKeys` (signiertes Geräte-Cookie, 90 Tage), `Sessions` (12 h, im Speicher)
  - `credentials`: `SecretsFile.update(form, agents=, clear_key=)` (prüft mit `load_config`), `set_paused`,
    `request_resume` (schreiben `BOT_PAUSED`/`BOT_RESUME`, auch wenn andere Einstellungen kaputt sind),
    `load_env_file`, `mask`
  - `files`: `write_private` (atomar, 600)
- **Invarianten:** nur 127.0.0.1. Zwei Sockets, der Zugangsweg kommt vom Socket: Tunnel-Port mit Host
  127.0.0.1/localhost (Port frei) und ohne Proxy-Header; öffentlicher Port (nur Caddy) nur mit dem konfigurierten
  Host, POSTs mit `Origin: https://<domain>`, Cookies `__Host-ltb_*` mit `Secure`, Fehlversuche je Client bzw.
  Gerät, keine Einrichtung, Login nur mit Passwort ≥ 12 Zeichen (ADR-0024). POST mit fremdem
  `Origin`/`Sec-Fetch-Site` abgelehnt; Ersteinrichtung nur per Tunnel mit Einrichtungscode aus dem Terminal; jedes Formular
  mit CSRF-Token (Sitzung bzw. Double-Submit-Cookie `ltb_pre` auf Login/Einrichtung); Cookies `ltb_*`, tolerant
  gelesen; Keys nie vollständig im HTML; CSP `script-src 'self'` (nur `/app.js` auf der Übersicht, kein
  Inline-Skript, kein `eval`, ADR-0019), ohne Inline-Stil, Header auf jeder Antwort
  (`end_headers`); `secrets.env` ohne Steuerzeichen/Zeilenumbrüche in Werten; kein Bitvavo-Key-Feld (ADR-0004).
  `secrets.env` und `web-auth.json` liegen im Datenverzeichnis, sind git-ignoriert, nie im Repo.

## Datenfluss & Nebenläufigkeit
`build_snapshots` holt je Markt die Kerzen für 1/15/60/240 min, danach einmal die Quotes (sie veralten am
schnellsten), dann die Prüfung je Markt. `paper_cycle`: Freigaben anwenden → Quotes holen und je Markt speichern →
frische Quotes aus der DB (ohne jeden Ende ohne Entscheidung) → Buy & Hold initialisieren (beide Quotes, keine
Pause) → Snapshots → Quotes älter als 20 s neu holen → je Markt eine Entscheidung pro neuem abgeschlossenem Bar
des Strategie-Zeitrahmens (trend 60 min, sonst 15 min) → GPT-Depots (luna, sol) mit demselben
Snapshot: je Depot frische Quotes? fällig? (stündlich, Bewegung ≥ 1,8 % oder Wechsel eines Referenzsignals, ≥ 10 min
Abstand, ≤ 4/h) → Reservierung; dann alle Anfragen gleichzeitig (Threads, nur Netz) → Quotes einmal neu holen → je
Depot Abrechnung → Prüfung → Entscheidung je Markt → Quotes älter als 20 s neu holen → Equity-Punkt und
Drawdown-Prüfung je Depot. Ein Fehler eines Depots blockiert die anderen nicht.
Der Dienst (`service.run`) startet diesen Zyklus jede Minute um :05 in einem Worker-Thread; dauert er länger, folgt
die nächste Runde am nächsten freien Termin. Anders als in der Arena (getrennte Kurs-, Signal- und LLM-Worker) läuft
alles in einer Runde (ADR-0017); einen schnellen Kurs-/Risiko-Worker gibt es bewusst nicht (ADR-0021, neu
bewerten mit M2). Die Weboberfläche liest
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
übrigen Variablen kommen nur aus der Umgebung des Prozesses (auf dem Server: systemd-Drop-in, das ein Update nie ändert). Der Dienst
liest Umgebung und `secrets.env` jede Runde neu, Umgebungswerte gewinnen (ADR-0017). Ziel: Hetzner Cloud CX23, Ubuntu 24.04, systemd-Unit.
Dazu `LOG_LEVEL` (Standard `INFO`) für `run`. Server ist gehärtet (Spec 002, Ablauf und Prüfbefehle:
`docs/betrieb/server.md`); die systemd-Dateien liegen geprüft in `deploy/` (Spec 008: `Type=notify`,
`WatchdogSec=60`, `TimeoutStopSec=90`, `Restart=on-failure` außer Exit 3, Härtung, Journal ≤ 200 MB / 14 Tage).
Deployment (Spec 009, ADR-0018): `scripts/deploy.sh` schickt `git archive` eines Commits von `origin/main` mit grüner
CI per SSH; `deploy/release.sh` baut daraus `/opt/llm-trading-bot/releases/<sha>` mit eigener venv (uv in fester
Version, `--frozen --no-dev`), prüft die DB lesend, stoppt, sichert, schaltet `current` atomar um und startet; ohne
gesunde Runde binnen 3 min zurück auf das vorige Release (nie die Daten). Daten in `/var/lib/llm-trading-bot`,
Backups täglich (14) und vor jedem Update (5) in `/var/backups/llm-trading-bot`, nur auf dem Server.

## Teststrategie
- **Host-Unit-Tests:** pytest, Logik ohne I/O; Netzwerk über schmale Adapter, in Tests ersetzt.
- **Integration:** echte API-Aufrufe mit Marker kennzeichnen und in CI ausschließen.
- **Nicht automatisiert getestet:** Server-Betrieb (Reboot, Netzausfall, Langlauf) – manuell auf dem CX23.
