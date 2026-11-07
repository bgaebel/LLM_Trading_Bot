# Spec 011: Risiko-Grenzen und Fail-closed

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s011-risiko` · **Meilenstein:** M1 (Nr. 4)
- **Erstellt:** 2026-11-07

## Ziel
Kein Depot kann unbegrenzt verlieren: Fällt ein Depot 10 % unter seinen Höchststand, verkauft der Bot seine Bestände
und stoppt es – wie die Arena. Der Nutzer sieht in der Übersicht, wie nah jedes Depot am Stopp ist, gibt ein
gestopptes Depot mit einem Klick wieder frei und kann den Handel mit einem Schalter anhalten. Ein Kursproblem bei
ETH blockiert BTC nicht mehr (und umgekehrt), und nichts handelt oder bewertet mit veralteten Kursen (ADR-0021).

## Festgelegte Grenzen (vom Nutzer übertragen; Begründung je Zeile)
| Grenze | Wert | Warum |
|---|---|---|
| Drawdown-Stopp (nicht Buy & Hold) | 10 % unter dem Allzeithoch der Equity → Zwangsverkauf, Stopp | Arena-Regel (Vergleichbarkeit); erwartbar gelegentlich bei ETH-lastigen Depots (Rückgang ≈ 11 % vom Hoch bei 90 % Investition) |
| Wiederfreigabe | Knopf in der Weboberfläche; Höchststand beginnt neu beim Bargeld | ein gestopptes Depot handelt nur nach bewusster Freigabe weiter |
| Handelspause | Schalter in der Weboberfläche (per Tunnel), ab der nächsten Runde, im Zweifel Pause | Not-Aus ohne Befehl und ohne Neustart |
| Je Markt / gesamt / Mindestorder / Spread / Kursalter | 45 % / 90 % / max(50 €, 2 %) / 50 bp / 20 s | unverändert aus der Arena (Spec 004) |
| Tagesverlust, Stop-Loss je Position, Cooldown | keine | nicht in der Arena; würden den Vergleich verzerren; Kandidaten für M2 |
| Prüftakt | jede Runde (60 s) und nach jedem Trade | ein Prozess (ADR-0017); schneller Worker erst mit Echtgeld |

## Akzeptanzkriterien
- [ ] AK1: Drawdown-Stopp bitgleich zur Arena: `mark(con, agent, qs, now, costs)` stoppt, wenn `agent != buyhold`,
  `not halted`, `peak > 0` und `eq < peak * (1 - DRAWDOWN_KILL)` mit `DRAWDOWN_KILL = 0.10` (Bewertung zum Mittelkurs);
  setzt `halted = 1`, verkauft jeden Markt mit Bestand > 0 vollständig zu `bid × (1 − Slippage)` mit Gebühr (auch
  unter 50 €) als `RISK_SELL` ohne `decision_id`, schreibt danach den Equity-Punkt und meldet den Stopp (Depot,
  Equity vor dem Verkauf, Höchststand). Ein schon gestopptes Depot löst nichts mehr aus (kein Trade, keine Meldung).
  — Nachweis: neue Golden-Fixtures aus Arena-Code in eigener Datei (`execution_drawdown_golden_v0.3.1.json`): genau
  90 %, 1 ulp darunter/darüber, schon gestoppt, Buy & Hold unter 90 %, `peak = 0`, `RISK_SELL` unter 50 €, nur ein
  Markt mit Bestand, Stopp direkt nach einem Trade in `record_decision`, Kostenvarianten, Fuzz mit weiten Kursläufen;
  `tests/paper/test_risk.py`
- [ ] AK2: Bestehende Goldens unverändert: Die bisherige Fixture-Datei wird vom Generator bytegleich erzeugt; alle
  Strategie- und Ausführungs-Goldens bleiben grün; der Mutationstest erkennt Änderungen an 0,10, `<` gegen `<=`,
  `not halted`, `peak > 0`, Ausnahme Buy & Hold und Verkauf zu `bid`. — Nachweis: `tests/paper/test_golden_execution.py`,
  Regenerieren + Vergleich (`tools/golden/generate_execution_golden.py --check`), `tools/golden/mutation_check.py`
- [ ] AK3: Gestoppt ist kein Fehler: Ein gestopptes klassisches Depot liefert je Markt den Status `HALTED` ohne
  Fehler, ein GPT-Depot `HALTED` ohne Anfrage und ohne Kosten; eine Runde mit gestopptem Depot und sonst guten Daten
  hat Ergebnis `ok`, der Dienst bleibt `running`, `failures` bleibt 0. Der Dienst loggt den Stopp genau einmal als
  WARNING. Stoppt ein Depot mitten in einer GPT-Antwort (BTC-Trade löst aus), wird die ETH-Entscheidung gespeichert,
  aber nicht gehandelt (wie Arena). — Nachweis: `tests/paper/test_cycle.py::test_halted_*`,
  `tests/llm/test_llm_cycle.py::test_halted_*`, `tests/service/test_run.py::test_risk_stop_is_logged_once`
- [ ] AK4: Übersicht: je Depot der Abstand zum Höchststand in Prozent („−4,2 % vom Hoch“, Stopp bei −10 %);
  gestoppte Depots mit „Risikostopp“ und Zeitpunkt des letzten `RISK_SELL` (ohne solchen: ohne Zeit). — Nachweis:
  `tests/web/test_overview.py::test_drawdown_*`, `test_risk_stop_*`
- [ ] AK5: Wiederfreigabe: Auf der Einstellungsseite steht neben jedem gestoppten Depot „Wieder freigeben“ (CSRF wie
  alle Formulare); das speichert `BOT_RESUME` mit Depot und Zeitpunkt in `secrets.env`. Der Dienst setzt in der
  nächsten Runde `halted = 0` und `peak = cash`, wenn das Depot gestoppt ist, keine Bestände hat und die Anforderung
  jünger als sein letzter `RISK_SELL` (falls vorhanden) und höchstens 24 h alt ist; sonst nichts (mit Warnung). Er
  loggt die Freigabe. Eine alte Anforderung gibt ein später erneut gestopptes Depot nicht frei. — Nachweis:
  `tests/paper/test_risk.py::test_resume_*`, `tests/web/test_server.py::test_resume_*`,
  `tests/web/test_credentials.py::test_resume_*`
- [ ] AK6: Handelspause fail-closed: Schalter „Handel pausieren“ auf der Einstellungsseite schreibt `BOT_PAUSED=1`
  (Ausschalten löscht ihn). Der Dienst liest den Wert jede Runde getrennt von der übrigen Konfiguration: unlesbare
  Datei oder ungültiger Wert → Pause mit Warnung; Umgebung gewinnt (die Seite zeigt dann „durch die Umgebung
  festgelegt“). Während der Pause: Entscheidungen klassischer Depots mit Status `PAUSED` ohne Trade, GPT `PAUSED`
  ohne Anfrage, Buy & Hold kauft nicht (keine Entscheidungszeile), Bewertung und `RISK_SELL` laufen weiter. Der
  angewandte Zustand steht in der Statusdatei; die Übersicht zeigt „Handel pausiert“ bzw. „Pause angefordert,
  greift in der nächsten Runde“. Ältere Statusdateien ohne dieses Feld bleiben lesbar. — Nachweis:
  `tests/paper/test_cycle.py::test_pause_*`, `tests/service/test_config_reload.py::test_pause_*`,
  `tests/web/test_credentials.py::test_pause_*`, `tests/web/test_server.py::test_pause_*`,
  `tests/service/test_status.py::test_older_status_*`
- [ ] AK7: Kurse je Markt: `bitvavo.quotes()` liefert die gültigen Märkte und je ungültigem Markt einen Fehler statt
  den ganzen Abruf zu verwerfen; `record_quotes` speichert gültige Märkte einzeln. Im Zyklus: Ein Markt ohne frischen
  Kurs bekommt keine Entscheidung (Kerze bleibt offen); hat ein Depot dort Bestand > 0, entscheidet es auch im anderen
  Markt nicht. GPT fragt nur, wenn alle Märkte der Anfrage und mit Bestand frische Kurse haben (sonst `NO_QUOTES`, keine
  Reservierung); fehlt nach der Antwort ein Kurs, ist nur dieser Markt `STALE_QUOTE`, die Abrechnung bleibt.
  Buy & Hold kauft nur mit beiden Kursen. `record_decision` mit unvollständigen Kursen bleibt `STALE_QUOTE` (Arena).
  `trading-bot market` zeigt einen fehlenden Kurs als Fehler statt abzustürzen. — Nachweis:
  `tests/market/test_bitvavo_quotes.py`, `tests/paper/test_cycle.py::test_bad_eth_quote_does_not_block_btc`,
  `test_holding_the_stale_market_blocks_both`, `tests/llm/test_llm_cycle.py::test_quote_lost_after_answer_*`,
  `tests/paper/test_execution.py::test_value_without_a_zero_holding_market_is_bit_identical`, `tests/test_cli.py::test_market_*`
- [ ] AK8: Kein Handeln und Bewerten mit alten Kursen: Vor den klassischen Entscheidungen prüft der Zyklus einmal das
  Kursalter und holt bei > 20 s neue Kurse (alle klassischen Depots der Runde nutzen denselben Satz). Vor der
  Schlussbewertung ebenso; ein Depot, dessen Märkte mit Bestand keinen frischen Kurs haben, bekommt in dieser Runde
  keinen Equity-Punkt (und keinen Stopp) – die Runde ist dann `incomplete`, nicht `no_quotes`. — Nachweis:
  `tests/paper/test_cycle.py::test_slow_round_refreshes_quotes_*`, `test_final_valuation_never_uses_stale_quotes`
- [ ] AK9: [manuell] Auf dem Server (mit Zustimmung): Vor dem Deploy Abstand jedes Depots zum Hoch lesend prüfen (ein
  Depot unter −10 % würde sofort gestoppt); Deploy gesund; Übersicht zeigt die Abstände; Schalter „Handel pausieren“
  an → nächste Runde ohne Trades, Übersicht „Handel pausiert“; aus → normal.
- [ ] AK10: Lint, Format, `mypy src tests tools`, CI grün; Doku (ARCHITEKTUR, `docs/betrieb/server.md`: Risikostopp,
  Freigabe, Pause; README).

## Abgrenzung
- Nicht Teil dieser Spec: Tagesverlustgrenze, Stop-Loss je Position, Trade-Cooldown, schneller Kurs-/Risiko-Worker,
  Echtgeld-Limits (M2), Schemaänderungen, Änderungen an Strategien oder GPT-Prompt.
- GPT bleibt eine gemeinsame Anfrage: Eine ungültige Antwort verwirft beide Märkte (Prüfregel wie Arena).
- Die Übersicht vergleicht weiter die jeweils letzten Equity-Punkte; fehlt einem Depot in einer Runde der Punkt
  (alte Kurse), zeigt die Spalte „Stand“ dessen Zeit.

## Relevante Entscheidungen & Learnings
- ADR-0021 (angenommen mit dieser Spec).
- ADR-0007: Arena-Gleichheit – der Stopp ist der Port von `mark_and_risk`; bestehende Fixtures werden nie geändert,
  neue kommen in eine eigene Datei.
- ADR-0017: Config je Runde (Pause, Freigabe), ein Prozess; ADR-0016: Weboberfläche schreibt nie in die DB;
  ADR-0015: Feldliste von `secrets.env` wird erweitert; ADR-0020: keine Schemaänderung, Gesundheitscheck (gestoppte
  Depots dürfen eine Runde nicht `incomplete` machen).
- Spec 004: „ohne frische Kurse keine Entscheidung, Kerze bleibt offen“ – gilt jetzt je Markt.
- LEARNINGS „Golden-Fixtures beweisen Gleichheit nur mit Grenzfällen“ (Grenzwerte, ulp); #marktdaten (ETH-Lücken).
- CLAUDE.md: Fail closed; BTC-Fehler blockiert ETH nicht; Regeln, Gebühren, Slippage nur mit Tests.

## Ansatz
- `paper/execution.py`: `mark(…, costs)` mit Arena-Stopp, Rückgabe der Stopp-Meldung; `value`/`current_weight` dürfen
  einen Markt mit Bestand 0.0 ohne Kurs auslassen (bitgleich, Property-Test), sonst Fehler; Herkunftsvermerk anpassen.
- `market/bitvavo.py` `quotes()` je Markt mit Fehlerliste; Aufrufer: `paper/cycle.py`, `llm/cycle.py` (Refresh,
  `_prepare`, `_finish`), `cli.run_market`, `initialize_buyhold` (nur mit beiden).
- `paper/cycle.py`: Kursprüfung vor dem klassischen Block und vor der Schlussbewertung, Status `HALTED`/`PAUSED`,
  Stopp-Meldungen in `CycleReport.risk_stops`, Freigaben anwenden. `llm/cycle.py`: `PAUSED`, Kurse je Markt.
- `service/run.py`: Pause und Freigaben fail-closed je Runde lesen, WARNING je Stopp, angewandte Pause in der
  Statusdatei (`service/status.py`, tolerant gegenüber alten Dateien).
- `web/credentials.py` (Felder, Schalter, Freigabe), `web/pages.py` (Einstellungsseite), `web/app.py` (Route
  „freigeben“), `web/overview.py`/`overview_page.py` (Abstand zum Hoch, Risikostopp, Pause).
- `tools/golden/generate_execution_golden.py`: zusätzliche Drawdown-Fälle in eigene Datei, `--check` für die
  bestehende; `golden_execution_runner.py` gibt `mark` die Kosten mit; `mutation_check.py` im selben Schritt anpassen.
- Verworfen: siehe ADR-0021.

## Plan
- [x] 1. ADR-0021 annehmen; `mark` mit Stopp, Drawdown-Goldens aus Arena-Code, Grenzfälle, Mutationen (AK1, AK2)
- [x] 2. Gestoppt ohne Fehler, Meldung und Log, Übersicht mit Abstand zum Hoch und Risikostopp (AK3, AK4)
- [x] 3. Kurse je Markt und Kursalter (AK7, AK8); Goldens und Mutationstest erneut
- [x] 4. Handelspause und Wiederfreigabe: Konfiguration, Dienst, Statusdatei, Weboberfläche (AK5, AK6)
- [ ] 5. Doku, Prüfung (Workflow), PR (AK10)
- [ ] 6. Mit Zustimmung: Abstände lesend prüfen, Deploy, Schalter auf dem Server prüfen (AK9)

## Risiken & offene Fragen
- **Sofortiger Stopp nach dem Deploy:** Liegt ein Depot schon > 10 % unter seinem Hoch, wird es in der ersten Runde
  verkauft und gestoppt → vor dem Deploy lesend prüfen (AK9).
- **Verlust über 10 %** beim Stopp (Mittel- gegen Geldkurs, Prüfung je Minute) – wie Arena.
- **Restmengen:** Ein vollständiger Verkauf kann winzige Restmengen hinterlassen (Gleitkomma); sie zählen als Bestand
  (Arena-treu) und blockieren bei fehlendem Kurs den anderen Markt.
- **Zwei neue Felder in `secrets.env`:** ältere Weboberflächen kennen sie nicht – Weboberfläche und Dienst kommen
  immer aus demselben Release.

---

## Verlauf
- 2026-11-07: Entwurf gegengeprüft (Workflow, Risiko/Arena-Treue und Umsetzbarkeit): u. a. Arena-Bedingung
  vollständig (`not halted`, `peak > 0`), gestoppt darf kein Rundenfehler sein (sonst dauerhaft `degraded` und
  Deploy-Rollback), Handelspause fail-closed, Kurse je Markt ohne Änderung der Arena-Pfade, Drawdown-Goldens in
  eigener Datei, Wiederfreigabe über die Weboberfläche statt per Befehl, Abstand zum Hoch in der Übersicht.
- 2026-11-07: Schritte 1 und 2 in einem Commit (`paper/cycle.py` gehört zu beiden). Drawdown-Goldens:
  16 benannte Fälle, 36 Fuzz-Blöcke (Stopp/kein Stopp je Modus), 24 Läufe (22 mit Stopp); `--check`: alte
  Fixture bytegleich. Mutation `peak > 0` ist äquivalent (Peak ≥ Equity ≥ 0, Cash nie negativ) und als solche
  vermerkt; alle übrigen 12 Stopp-Mutationen werden erkannt. Der Zwangsverkauf setzt den Bestand wie die Arena
  auf 0 – Restmengen entstehen nur bei normalen Verkäufen.
- 2026-11-07: Schritte 3 und 4 in einem Commit (Zyklus, Bericht und Dienst gehören zu beiden). Umsetzung von AK7:
  `record_decision` bekommt `partial=True` nur aus den Zyklen; Arena-Aufrufe mit Teil-Kursen bleiben
  `STALE_QUOTE`. Mit `partial` genügen Kurse des entschiedenen und aller gehaltenen Märkte – bitgleich zu beiden
  Kursen, weil ein Markt ohne Bestand genau 0,0 beiträgt (Property-Test über 5000 Fälle, 40 Entscheidungen
  DB-Zustand gleich). `bitvavo.quotes()` liefert ein `QuoteBook` (dict + `errors`). Ein kaputter Kurs lässt
  meist schon den Snapshot seines Marktes scheitern (der Snapshot trägt den Kurs) – die Fehlermeldung kommt dann
  von dort. Rundenergebnis `incomplete` auch dann, wenn kein Depot bewertet werden konnte, aber welche wegen
  fehlender Kurse ausgelassen wurden (`unvalued`). Hinweistext der Übersicht für Kursfehler angepasst (es
  werden keine gespeicherten Kurse mehr genutzt). Tests von `paper/risk.py` liegen unter `tests/paper/`
  (`test_controls.py` statt `tests/service/test_config_reload.py::test_pause_*` für das Lesen; der Dienst-Teil
  steht in `test_config_reload.py`), damit die Paper-Mutationen sie sehen. `paper-cycle` (CLI) beachtet Pause und
  Freigaben ebenfalls.

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
