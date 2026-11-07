# Spec 012: Echtgeld-Schnittstelle als Entwurf (Broker-Naht)

- **Status:** fertig
- **Issue:** – · **Branch:** `feat/s012-broker-naht` · **Meilenstein:** M1 (Nr. 8)
- **Erstellt:** 2026-11-07

## Ziel
Der Code bekommt den Vertrag, an den M2 einen Live-Broker anschließt: eine Broker-Schnittstelle auf
Entscheidungsebene (heute nur vom unveränderten Paper-Broker erfüllt) und die Börsenseite als geprüfte Typen und
Zustandsmaschine für echte Orders. Echtgeld bleibt nachweislich unmöglich: Es gibt keinen Order-Code, und Tests
schlagen an, sobald jemand welchen hinzufügt. Für M2 liegt ein ADR-Vorschlag vor, der auf den belegten
Eigenschaften der Bitvavo-Trading-API aufbaut. Am laufenden Dienst ändert sich nichts (ADR-0022, ADR-0023).

## Akzeptanzkriterien
- [x] AK1: Paket `llm_trading_bot/broker/` mit `contract.py` (Protocols `Broker` und `OrderExchange`, Typen
  `OrderIntent`, `OrderOutcome`, `ExecutedFill`, `MarketRules`, `AccountBalances`, `OrderSide`, `OrderState`),
  `paper.py` (`PaperBroker`), `live.py` (`LiveTradingLocked`, `refuse_live`), `intents.py` (Zustandsmaschine) und
  `make_broker(mode)`. `Broker` hat `record_decision`, `mark`, `initialize_buyhold` (Signaturen wie
  `paper.execution`), `unresolved(con)` und `dispatch(con)` (Paper: leer bzw. nichts zu tun); der Docstring hält fest,
  dass ein Live-Broker innerhalb einer Transaktion nur PLANNED-Vorgänge anlegt und ausschließlich in `dispatch`
  außerhalb jeder Transaktion sendet. `PaperBroker` erfüllt `Broker` statisch. — Nachweis: `uv run mypy src tests
  tools` (mit `_: Broker = PaperBroker()` in `tests/broker/test_broker_contract.py`); Laufzeit-Invarianten AK7
- [x] AK2: Der `PaperBroker` reicht nur durch und liefert bitgleich die Arena-Ergebnisse: In beiden
  Golden-Fixtures (Spec 004 und Drawdown) laufen die `decide`-, `mark`- und `buyhold`-Ops über den `PaperBroker`,
  `trade`-Ops (kein Broker-Port) weiter direkt; alle Digests und benannten Fälle gleich. Jeder Parameter
  (`costs`, `now`, `qs`, `status`, `stops`, `partial`, `when`) kommt unverändert bei `paper.execution` an.
  — Nachweis: `tests/broker/test_paper_broker_golden.py` (`BrokerRunner(BotRunner)` überschreibt nur `apply`;
  `tests/golden_execution_runner.py` bleibt unverändert), `tests/broker/test_paper_broker.py` (Spy je Parameter)
- [x] AK3: Am laufenden Dienst ändert sich nichts: `git diff --exit-code main -- src
  ':(exclude)src/llm_trading_bot/broker' deploy scripts pyproject.toml uv.lock tests/paper/fixtures
  tests/golden_*.py tools/golden/generate_execution_golden.py` ist leer. In `tools/golden/mutation_check.py` bleiben
  alle bestehenden Mutationen und `EQUIVALENT`-Einträge gleich; neu sind nur die Einträge für `broker/`,
  `core.py`/`cli.py`, ein `broker noop control`, das Kopieren von `uv.lock`, `deploy/` und `scripts/` in die
  Temp-Kopie und die Zuordnung der Dateien `core.py`/`cli.py` zu ihren Tests in `group_of`
  (`git diff main -- tools/golden/mutation_check.py` zeigt nur diese Änderungen). Kein Deploy nötig.
  [manuell, optional] `generate_execution_golden.py <arena>/apps/arena --check` meldet beide Fixtures gleich.
- [x] AK4: Live ist gesperrt – eine Laufzeitsperre und zwei Vorab-Sperren für M2: `parse_mode` wirft
  `NotImplementedError` für `live`, ` LIVE `, `Live\n` und `ValueError` für `real`; mit `TRADING_MODE=live`
  bricht `main([...])` für `run`, `paper-cycle`, `market` und `web` ab, bevor ein `BitvavoClient` oder eine
  Datenbank entsteht; `make_broker(TradingMode.LIVE)` wirft `LiveTradingLocked` (Unterklasse von
  `NotImplementedError`), auch wenn jemand `parse_mode` umgeht; `live.py` definiert keine Klasse mit Methoden und
  importiert nur `typing`. — Nachweis: `tests/test_core.py`, `tests/test_cli.py -k live`,
  `tests/broker/test_live_locked.py`
- [x] AK5: Es gibt keinen Order-Pfad, und ein eingeschleuster würde auffallen. Statische Prüfung (AST, Docstrings
  ausgenommen) über `src/`, `tests/` und `tools/` (ohne die Prüfdatei selbst) sowie Textprüfung über `scripts/` und
  `deploy/`:
  - keine Literale `/order`, `/orders`, `/ordersOpen`, `/trades`, `/balance`, `/account`, `/cancelOrdersAfter`,
    `Bitvavo-Access`, `privateCreateOrder`, `privateCancelOrder`; in Skripten kein `bitvavo.com` zusammen mit
    diesen Pfaden, `-X POST` oder `--data`;
  - `market/bitvavo.py`: das erste Argument jedes `self._get(...)` ist `"/ticker/book"` oder ein f-String der Form
    `/{…}/candles?{…}`; `bitvavo.com` steht in `src/` nur dort;
  - `urllib.request.Request(` nur in `market/http.py`, ohne `data=`, `method` höchstens als Literal `"GET"`;
    `urlopen(` nur dort und nur mit diesem `Request`; keine `HTTPConnection`/`HTTPSConnection` in `src/`;
  - kein Import von `requests`, `httpx`, `httpx2`, `aiohttp`, `urllib3`, `websocket`, `websockets`, `subprocess`
    und kein `os.system`/`os.popen` in `src/` (Liste als Konstante; Ausnahme nur per neuer Spec);
  - `hmac` nur in `web/auth.py`; kein Lesen von `BITVAVO_*`/`LIVE_*` aus der Umgebung in `src/`;
  - keine Klasse außer dem Protocol `OrderExchange` hat zwei oder mehr der Methoden `market_rules`, `balances`,
    `submit`, `lookup`, `cancel_all`; keine Funktion heißt `place_order`, `create_order`, `cancel_order`;
  - kein Paket in `pyproject.toml`/`uv.lock`, dessen Name `bitvavo` oder `ccxt` enthält.
  Jede Regel hat einen Negativtest (verbotenes Muster in einer temporären Kopie → Prüfung schlägt an).
  — Nachweis: `tests/broker/test_no_order_path.py`
- [x] AK6: Modus und Börsen-Keys lassen sich weder über die Weboberfläche noch über `secrets.env` noch über die
  Server-Dateien setzen: `TRADING_MODE`, `BITVAVO_*`, `LIVE_*` stehen nicht in `web/credentials.FIELDS`,
  `parse("TRADING_MODE=live")` ist leer; Units und Skripte in `deploy/` und `scripts/` setzen weder diese
  Variablen noch `EnvironmentFile=`. — Nachweis: `tests/broker/test_live_locked.py -k config`
- [x] AK7: Die Live-Typen lehnen Ungültiges ab:
  - `OrderIntent`: genau eine Menge > 0 als `Decimal` (`float`/`bool` abgelehnt); BUY trägt ein EUR-Budget
    (`amount_quote`), SELL/RISK_SELL eine Basismenge (`amount_base`) – die Umrechnung in eine Bitvavo-Order
    (Market mit `amountQuote` oder IOC-Limit mit `amount` und `price`) gehört zum Live-Broker in M2;
    `operator_id` ist `int` > 0 (kein `bool`/`str`); `client_order_id` ist eine UUID4.
  - `OrderOutcome`: der Zustand folgt der ausgeführten Menge, nicht dem Bitvavo-Status (alle Fills = FILLED;
    teilweise = PARTIAL; keine Fills und `expired`/`canceled` = EXPIRED; Ablehnung ohne Order = REJECTED);
    abgelehnt werden REJECTED/EXPIRED/UNKNOWN mit Fills, PARTIAL/FILLED ohne Fill; `exchange_status` und
    `restatement_reason` werden unverändert gehalten.
  - `ExecutedFill`: `settled=True` verlangt Gebühr und Gebührenwährung.
  - `MarketRules` aus dem öffentlichen `GET /markets` (Beträge, Mengen, `tickSize` als Strings → `Decimal`;
    `quantityDecimals`/`notionalDecimals` als Ganzzahlen; `float`/`bool` abgelehnt): Mengen werden nur abgerundet;
    Limitpreis auf ein Vielfaches von `tickSize` – bei BUY abgerundet (nie über der Preisgrenze), bei
    SELL/RISK_SELL aufgerundet (nie darunter); Mindestbetrag, Mindestmenge und Marktstatus ≠ `trading` werden als
    Verstoß gemeldet.
  — Nachweis: `tests/broker/test_broker_contract.py` mit der unveränderten öffentlichen Antwort
  `tests/broker/fixtures/markets_2026-11.json`
- [x] AK8: Die Zustandsmaschine (`ALLOWED`) erlaubt genau: PLANNED → SENT, UNKNOWN, ABORTED; SENT → FILLED,
  PARTIAL, EXPIRED, REJECTED, UNKNOWN; UNKNOWN → FILLED, PARTIAL, EXPIRED (nur nach Nachfragen). Endzustände
  sind endgültig; nichts führt zurück zu PLANNED oder SENT (kein erneutes Senden). ABORTED heißt „nie gesendet“
  (nur vor dem Versand in derselben Runde); beim Start gilt jeder PLANNED-Vorgang als UNKNOWN. `may_plan` ist
  falsch, solange für (Depot, Markt) ein Vorgang in PLANNED, SENT oder UNKNOWN steht. — Nachweis:
  `tests/broker/test_intents.py` (alle Paare `OrderState × OrderState`)
- [x] AK9: Eine fremde Datenbank (etwa eine spätere Live-DB mit `user_version` ≥ 100, auch im WAL-Modus mit nicht
  zurückgeschriebenen Einträgen) wird von `open_db` und der Weboberfläche abgelehnt; ihr Inhalt (`iterdump` über
  eine lesende Verbindung) und `user_version` sind vorher und nachher gleich. — Nachweis:
  `tests/broker/test_live_locked.py -k foreign_database`
- [x] AK10: Der Mutationstest erfasst die neuen Sperren: alle Mutationen der Gruppen `broker` und `cli` KILLED, der
  `broker noop control` überlebt (die Prüfung meldet Überlebende). Mindestens: `raise LiveTradingLocked` →
  `return None`; `make_broker` gibt bei LIVE einen `PaperBroker`; je eine Prüfung in `OrderIntent`/`OrderOutcome`
  entfernt; ein Übergang zu viel in `ALLOWED`; `may_plan` ignoriert UNKNOWN; `PaperBroker` reicht `partial` bzw.
  `stops` nicht weiter; Limitpreis bei BUY auf- bzw. bei SELL abgerundet; Menge aufgerundet; `parse_mode` gibt bei
  `live` PAPER zurück; der `parse_mode`-Aufruf in `cli.py` fehlt. — Nachweis: `uv run python
  tools/golden/mutation_check.py --only broker` und `--only cli`
- [x] AK11: Doku: ADR-0022 angenommen, ADR-0023 vorgeschlagen (mit Quellen je Fakt), INDEX, ARCHITEKTUR
  (Broker-Schicht; die Zyklen rufen in M1 weiter `paper.execution` direkt), ROADMAP Nr. 8 → Spec 012,
  `docs/recherche/bitvavo-trading-api-2026-11.md`; Lint, Format, `mypy src tests tools`, Shellcheck, CI grün.

## Abgrenzung
- Nicht Teil dieser Spec: Order-Endpunkte, Signatur, Bitvavo-Keys, ein Börsen-Client, das Umstellen der Zyklen auf
  den `Broker`, das Herauslösen der Größenberechnung aus `paper.execution` (beides M2, golden-geprüft), Live-DB,
  Live-Limits als Code, Startsperre bei vorhandenen Bitvavo-Variablen, Startkapital.
- **Die Freigabe dieser Spec nimmt nur ADR-0022 an.** ADR-0023 bleibt ausdrücklich „vorgeschlagen“ (ROADMAP Nr. 8
  verlangt einen ADR-Vorschlag) und wird erst mit der M2-Spec angenommen, geändert oder verworfen – auch wenn
  diese Spec verworfen würde.
- Kein zweiter Planer für die Ordergröße neben der Arena-Arithmetik (verworfen, ADR-0022).

## Relevante Entscheidungen & Learnings
- ADR-0004: keine Order-Endpunkte, keine Keys mit Handelsrecht, `live` gesperrt bis M2 – bleibt unverändert und
  wird nicht abgelöst. Ihr Punkt „Ausführung hinter einer Broker-Schnittstelle“ wird in M1 nur **vorbereitet**:
  Die Schnittstelle existiert und der Paper-Broker erfüllt sie geprüft; die Zyklen laufen erst ab M2 darüber
  (Zustimmung des Nutzers mit der Freigabe, festgehalten in ADR-0022).
- ADR-0007: Paper-Ausführung bitgleich zur Arena – deshalb nur Delegation, Beweis über beide Fixtures.
- ADR-0016/0017/0020: ein Schreiber, ein Prozess, Rollback ohne Schemaänderung – keine Laufzeitänderung in M1.
- ADR-0021: Pause und Risikostopp – die Bedeutung des Not-Aus für Live bleibt in ADR-0023 offen.
- LEARNINGS: „Golden-Fixtures beweisen Gleichheit nur mit Grenzfällen“; „random.gauss rechnet je Plattform anders“
  (Fixtures nur aus festen Werten); „Ersetzungen per Python-Heredoc greifen bei Nicht-ASCII nicht“.
- Recherche `docs/recherche/bitvavo-trading-api-2026-11.md` (Stand 2026-11-07, Quellen je Fakt): `clientOrderId`
  ist kein Idempotenzschlüssel; `operatorId` (Ganzzahl) ist Pflicht; Limitpreis auf `tickSize`; Mindestorder laut
  `/markets` (am 2026-11-07: 5 €); Preisschutz führt zu Teilausführungen; Gebühr erst mit `settled`; Orders und
  Trades lesen braucht das Trade-Recht; keine Sandbox.

## Ansatz
- Neues Paket `broker/` ohne I/O. `Broker` auf Entscheidungsebene mit den Signaturen von `paper.execution` plus
  `unresolved`/`dispatch`; `PaperBroker` ist reine Delegation. **Ehrlich:** Größenberechnung, Gewichtsgrenzen,
  Mindestorder, Halte-/Kaufregeln und Risikostopp liegen heute *unterhalb* dieser Naht in `paper.execution`. Ein
  Live-Broker braucht sie; M2 löst sie deshalb zuerst golden-geprüft heraus (eigene Spec, ADR-0007) und erzeugt
  daraus `OrderIntent`s. Bis dahin ist die Naht nur für Paper vollständig.
- `OrderExchange` (nur Protocol) beschreibt die Börsenseite für M2: `market_rules`, `balances`, `submit` (genau ein
  Versand; Zeitüberschreitung → UNKNOWN), `lookup`, `cancel_all`. Keine Implementierung.
- `intents.py`: reine Zustandsmaschine (Übergangstabelle + `may_plan`), damit „nie blind erneut senden“ schon jetzt
  geprüft feststeht.
- Sperren als Tests statt als Laufzeitcode (AK4–AK6, AK9); neuer Code ist nur `broker/`.
- Verworfen (Workflow mit drei Entwürfen, drei Gutachtern und drei Kritikern, 2026-11-07): Order-Absichten mit
  eigenem Planer schon in M1 (Arena-Arithmetik doppelt, `float` für Echtgeld ungeeignet); Startsperre und
  Live-Limits in M1 (Laufzeitänderung ohne Nutzen vor M2); stille Umdeutung von ADR-0004.

## Plan
- [x] 1. `broker/contract.py` (Protocol `Broker`), `paper.py`, `live.py`, `make_broker`; Tests Vertrag, Spy,
  Goldens über den Broker (AK1, AK2, AK4 teilweise)
- [x] 2. Live-Typen mit Invarianten; öffentliche `/markets`-Antwort (ohne Key) einmal abrufen und unverändert als
  Fixture speichern; `MarketRules`; `intents.py` (AK7, AK8)
- [x] 3. Sperr-Tests: Order-Pfad-Prüfung mit Negativtests, Konfiguration, fremde DB, `parse_mode`-Varianten,
  CLI-Abbruch (AK4, AK5, AK6, AK9)
- [x] 4. Mutationstest: Kopierliste, `group_of`, Einträge `broker`/`cli`, `broker noop control` (AK10); Nachweis AK3
- [x] 5. ARCHITEKTUR, Ergebnis; Prüfung (Workflow), PR (AK11)

## Risiken & offene Fragen
- **Naht ohne Aufrufer:** Bis M2 ruft niemand den `Broker` auf. Gegenmittel: AK2 lässt beide Fixtures über ihn
  laufen; mypy prüft die Signaturen gegen `paper.execution`.
- **Naht nur für Paper vollständig:** Die Arena-Regeln liegen unterhalb (siehe Ansatz). M2 muss sie herauslösen,
  bevor ein Live-Broker sinnvoll ist – das ist der größte Brocken von M2.
- **Bitvavo ändert die API:** Typen und ADR-0023 bilden den Stand 2026-11-07 ab; ADR-0023 listet, was vor M2 mit
  echtem Key zu prüfen ist.
- **Prüfungen zu streng oder zu lax:** Ausnahmen (`web/auth.py` für `hmac`, `market/` für öffentliche Pfade,
  `ThreadPoolExecutor.submit` im GPT-Zyklus) sind benannt; jede Regel hat einen Negativtest.

---

## Verlauf
- 2026-11-07: Entwurf aus Workflows: Recherche der Bitvavo-API und der Code-Nähte, drei Entwürfe, drei Gutachter
  (Grundgerüst „minimale Naht“ plus prüfbare Sicherungen); danach drei Kritiker (37 Punkte, u. a.: Arena-Regeln
  liegen unterhalb der Naht; Absicht nicht auf einen Ordertyp festlegen; Zustände nach ausgeführter Menge;
  PLANNED nach Neustart = UNKNOWN; Prüfregeln, die am heutigen Code scheitern würden; Mutationstest braucht
  `uv.lock`/`deploy/` in der Kopie) – eingearbeitet.
- 2026-11-07: Prüf-Workflow (4 Blickwinkel, je Befund 3 Gegenprüfer): 23 Befunde, 17 widerlegt, 6 bestätigt und behoben – Skriptregel erkennt nun sendendes `curl` auch mit Host in einer Variablen und die verbotenen Literale in jeder Zeile; Bytes-Literale werden geprüft; börsenartige Klassen auch in `tests/`/`tools/`; Test, dass Bitvavo-Status und `restatementReason` unverändert bleiben; ARCHITEKTUR nennt den Umfang je Regel genau; Code-Anker verweisen auf das angenommene ADR-0022 statt auf den Vorschlag ADR-0023.

## Ergebnis
- **Erreicht:** Paket `broker/` mit Vertrag (`Broker`, `OrderExchange`), Paper-Delegation, Live-Sperre,
  Live-Typen mit Echtgeld-Regeln und Zustandsmaschine; Sperren als Tests mit Negativtests; Mutationen für Naht und
  Sperren. Laufzeitcode unverändert, kein Deploy (PR #19, `b7279cb`).
- **Abweichungen vom Plan:** Schritt 5 ohne neue ADR-Dateien (schon mit der Freigabe angelegt). Tests von
  `paper.risk` nicht betroffen. `http.client` in `market/http.py` als benannte Ausnahme (nur für den
  Ausnahmetyp; Verbindungen bleiben verboten).
- **Nachweise:** Testsuite lokal grün (1755 + neue Broker-Tests), CI grün auf PR #19; AK3: `git diff --exit-code`
  über Laufzeitcode, Server-Dateien, Paketliste und Fixtures leer; Mutationstest `broker` 25/25, `cli` 2/2 wie
  erwartet, die Kontrollmutation überlebt; Prüf-Workflow: 6 bestätigte Befunde behoben (siehe Verlauf).
- **Offen / Folgearbeit:** M2: Arena-Regeln golden-geprüft aus `paper.execution` herauslösen, Zyklen auf den
  `Broker` umstellen, ADR-0023 annehmen oder ändern, ADR für die Ablösung von ADR-0004.
- **Neue ADRs / Learnings:** ADR-0022 (angenommen), ADR-0023 (vorgeschlagen bis M2);
  `docs/recherche/bitvavo-trading-api-2026-11.md`.
