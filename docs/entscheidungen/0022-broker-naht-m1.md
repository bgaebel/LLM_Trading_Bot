# ADR-0022: Broker-Naht in M1 – Protokoll plus Paper-Delegation, Sperren als Tests, keine Laufzeitänderung

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-07
- **Bereich:** #ausfuehrung #sicherheit #echtgeld

## Kontext
ADR-0004 verlangt, dass die Ausführung hinter einer Broker-Schnittstelle liegt, damit ein Live-Broker später ohne
Änderung an Strategie und Risiko ergänzt werden kann – und verbietet bis M2 jeden Order-Code und jeden Key mit
Handelsrecht. Roadmap M1 Nr. 8 fordert die Schnittstelle als Entwurf. Die Paper-Ausführung ist durch Golden-Fixtures
und den Mutationstest bitgleich zur Arena festgenagelt (ADR-0007); der Dienst läuft auf dem Server, jede
Laufzeitänderung braucht einen Deploy (ADR-0020). Workflows (Recherche der Bitvavo-Trading-API, drei Entwürfe, drei
Gutachter, drei Kritiker; 2026-11-07) haben den Schnitt vorbereitet.

Wichtig für die Einordnung: Größenberechnung, Gewichtsgrenzen, Mindestorder, Halte-/Kaufregeln und Drawdown-Stopp
stecken heute in `paper.execution` (`record_decision`, `trade_to_target`, `mark`) – also in genau den Funktionen,
die eine Schnittstelle auf Entscheidungsebene kapselt.

## Entscheidung
- **Die Naht liegt auf Entscheidungsebene:** `broker.Broker` mit den Signaturen von `paper.execution`
  (`record_decision`, `mark`, `initialize_buyhold`) plus `unresolved(con)` und `dispatch(con)`. Ein Live-Broker legt
  innerhalb einer Transaktion nur PLANNED-Vorgänge an und sendet ausschließlich in `dispatch` außerhalb jeder
  Transaktion (festgehalten im Protocol).
- **`PaperBroker` delegiert nur** an das unveränderte `paper.execution`; beide Golden-Fixtures laufen zusätzlich über
  den Broker.
- **ADR-0004 Punkt „Ausführung hinter einer Broker-Schnittstelle“ ist in M1 nur vorbereitet:** Die Schnittstelle
  existiert und der Paper-Broker erfüllt sie geprüft; die Zyklen rufen weiter `paper.execution` direkt auf. Das
  Umstellen ist der erste Schritt von M2 – zusammen mit dem golden-geprüften Herauslösen der Arena-Regeln aus
  `paper.execution`, ohne das ein Live-Broker sie neu schreiben müsste. ADR-0004 selbst bleibt unverändert gültig.
  (Zustimmung des Nutzers mit der Freigabe von Spec 012 am 2026-11-07.)
- **Live-Typen schon jetzt mit Echtgeld-Regeln:** `Decimal` für Beträge; `client_order_id` als zufällige UUID4;
  `operator_id: int`; die Absicht trägt bei BUY ein EUR-Budget, bei SELL eine Basismenge und legt keinen Bitvavo-
  Ordertyp fest; der Zustand folgt der ausgeführten Menge; `MarketRules` nur aus dem öffentlichen `/markets` (Mengen
  abrunden, Limitpreis zur sicheren Seite runden); Zustandsmaschine ohne Weg zurück zum Senden, PLANNED nach einem
  Neustart = UNKNOWN. `OrderExchange` bleibt ein Protocol ohne Implementierung.
- **Sperren als Tests, nicht als Laufzeitcode:** `parse_mode` und sein Aufruf in `cli.py` bleiben die einzige
  Laufzeitsperre; `make_broker(LIVE)` wirft `LiveTradingLocked`; statische Prüfungen verbieten Order-Pfade,
  HTTP-Bibliotheken außer `urllib` in `market/http.py`, Signatur-Code außerhalb von `web/auth.py`, Börsen-SDKs und
  Modus-/Key-Felder in Weboberfläche, Units und Skripten – jede Regel mit Negativtest, Mutationen für die Sperren.
- **Keine Laufzeitänderung in M1:** außer dem neuen Paket `broker/` bleiben `src/`, `deploy/`, `scripts/`,
  `pyproject.toml`, `uv.lock` und die Fixtures byte-gleich; kein Deploy.

## Verworfene Alternativen
- **Order-Absichten mit eigenem Planer in M1:** rechnet die Arena-Arithmetik ein zweites Mal (Zwilling auf
  Vorrat), `float` ist für Echtgeld ungeeignet; die Größen-Absicht kommt in M2 einmalig mit dem Herauslösen.
- **Zyklen schon jetzt über den Broker leiten:** ändert golden-geprüften Laufzeitcode und Mutationsmuster ohne
  Nutzen vor M2.
- **Nur die Börsenseite (ohne `Broker` auf Entscheidungsebene):** ließe ADR-0004 ganz ohne Schnittstelle; die
  Delegation kostet wenig und hält die Signaturen geprüft.
- **Startsperre bei vorhandenen `BITVAVO_*`-Variablen, Live-Limits als Code:** Laufzeitänderung mit Deploy; gehört zu
  M2 (ADR-0023).
- **ADR-0004 umschreiben** („Paper muss nicht über die Schnittstelle laufen“): ADR-0004 bleibt, wie es ist; die
  Teilerfüllung in M1 steht offen in diesem ADR.

## Konsequenzen
- Positiv: M2 beginnt mit einem geprüften Vertrag, einer geprüften Zustandsmaschine und geprüften Sperren; der
  laufende Bot ist unberührt.
- Negativ / Preis: Bis M2 hat die Naht keinen Aufrufer (abgefedert durch die Goldens über den Broker und mypy); die
  eigentliche Arbeit – Arena-Regeln herauslösen – bleibt für M2.
- Neu bewerten, wenn: M2 geplant wird (mit ADR-0023 und einem ADR, das ADR-0004 ablöst).

## Code-Anker
- `src/llm_trading_bot/broker/` (`contract.py`, `paper.py`, `live.py`, `intents.py`) – geplant, Spec 012
- `tests/broker/test_no_order_path.py`, `tests/broker/test_live_locked.py` – geplant, Spec 012
