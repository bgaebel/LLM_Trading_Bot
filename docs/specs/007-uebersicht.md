# Spec 007: Startseite der Weboberfläche mit Depot-Übersicht

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s007-uebersicht` · **Meilenstein:** M1 (Nr. 7)
- **Erstellt:** 2026-11-06

## Ziel
Nach dem Login landet der Betreiber auf einer Übersicht statt direkt in den Einstellungen. Ohne Quellcode und ohne
SSH-Befehle sieht er dort, ob der Bot läuft, wie alt der letzte Kurs ist, wie jedes Depot gegen Buy & Hold steht,
was zuletzt entschieden wurde und warum, was GPT heute gekostet hat und welche virtuellen Trades es gab
(Erfolgskriterium aus `docs/PROJEKT.md`). Die Seite liest die Bot-DB nur lesend (ADR-0016).

## Akzeptanzkriterien
- [ ] AK1: Navigation: `/` zeigt nach dem Login die Übersicht, ohne Sitzung geht es zu `/login` bzw. `/setup`.
  Im Kopf stehen die Reiter „Übersicht“ und „Einstellungen“ (aktiver Reiter markiert), das Logo führt zu `/`.
  Nach Login und Ersteinrichtung geht es zu `/`. — Nachweis: `tests/web/test_server.py::test_overview_*`,
  `test_navigation_*`
- [ ] AK2: Nur lesend (ADR-0016): Die DB wird mit `mode=ro` und `query_only` geöffnet; ein Schreibversuch über diese
  Verbindung scheitert; Inhalt und Größe der DB-Datei sind nach beliebig vielen Aufrufen unverändert; Lesen klappt,
  während ein Schreiber den `WriterLock` hält und eine Schreibtransaktion offen ist (der Leser sieht den letzten
  abgeschlossenen Stand). Fehlt die DB, zeigt die Seite „noch keine Daten“ und legt keine Datei an; eine DB mit
  anderer Schemaversion wird mit Meldung angezeigt, nicht verändert. — Nachweis: `tests/web/test_readonly.py`
- [ ] AK3: Depots: je Depot (alle in `portfolios`, Buy & Hold als Referenz markiert) Equity, Cash, BTC, ETH aus dem
  letzten Equity-Stand, Ergebnis seit Start (10.000 €) in € und %, Abstand zu Buy & Hold in Prozentpunkten,
  gestoppt ja/nein. Werte sind nach Gebühren und Slippage (so wie gespeichert). — Nachweis:
  `tests/web/test_overview.py::test_depots_*` mit einer Fixture-DB aus echten `paper_cycle`-Läufen
  (`ScenarioClient`, `FakeModel`)
- [ ] AK4: Equity-Verlauf: ein SVG-Linienchart mit einer Linie je Depot (Farblegende), Linie für das Startkapital,
  Achsenbeschriftung (Zeitraum, Min/Max). Höchstens 240 Punkte je Linie (Zeitraum in gleich große Abschnitte geteilt,
  letzter Wert je Abschnitt, erster und letzter Punkt bleiben). Kein oder ein Datenpunkt → Hinweis statt Chart.
  Beschriftet als echte Paper-Daten, deutlich getrennt vom „DEMO“-Chart im Hintergrund. — Nachweis:
  `tests/web/test_overview.py::test_equity_*`, `tests/web/test_pages.py::test_equity_chart_*`
- [ ] AK5: Lebenszeichen: Zeitpunkt und Alter des letzten Zyklus (jüngster Equity-Stand) und des letzten Kurses je
  Markt (`market.ts`); Ampel „läuft“ (≤ 15 min), „verzögert“ (≤ 60 min), „steht“ (> 60 min oder nie). — Nachweis:
  `tests/web/test_overview.py::test_liveness_*` inkl. Grenzwerte 15:00/15:01 und 60:00/60:01 min
- [ ] AK6: Entscheidungen: je Depot und Markt die jüngste Entscheidung mit Zeit, Aktion, Ziel, Konfidenz, Status
  (z. B. `OK`, `STALE_QUOTE`) und Begründung. Begründungen stammen auch vom LLM und werden immer escaped. — Nachweis:
  `tests/web/test_overview.py::test_decisions_*`, `tests/web/test_pages.py::test_reason_html_is_escaped`
- [ ] AK7: GPT heute (UTC-Tag wie die Budgetsperre): je GPT-Depot Anzahl Anfragen, belastete Kosten inkl. offener
  Reservierungen (dieselbe Abfrage wie `charged_today`) gegen den Tagesanteil, Tokens (Input, Output, Reasoning),
  Status und Fehlertyp der letzten Anfrage. Anteile aus `secrets.env` bzw. den Standardwerten. — Nachweis:
  `tests/web/test_overview.py::test_llm_today_*`
- [ ] AK8: Trades: die letzten 20 virtuellen Trades, neueste zuerst (Zeit, Depot, Markt, Seite, Menge, Kurs, Betrag,
  Gebühr). — Nachweis: `tests/web/test_overview.py::test_trades_*`
- [ ] AK9: Die Übersicht lädt sich alle 60 s selbst neu (`<meta http-equiv="refresh">`, kein Skript); die
  Einstellungsseite nicht (sonst gingen Eingaben verloren). CSP und Sicherheits-Header unverändert. — Nachweis:
  `tests/web/test_server.py::test_overview_refreshes_*`, bestehende Header-Tests
- [ ] AK10: `trading-bot web --data-dir <dir> [--db <pfad>]`, Standard `<dir>/bot.db` (wie `paper-cycle` die
  `secrets.env` neben der DB erwartet). — Nachweis: `tests/test_cli.py::test_web_db_*`
- [x] AK11: [manuell] Übersicht im Browser mit einer echten Paper-DB (z. B. aus `paper-cycle`): Desktop und 375 px
  ohne seitliches Scrollen; während eines laufenden `paper-cycle` bleibt die Seite benutzbar.
- [ ] AK12: Lint, Format, `mypy src tests tools`, CI grün; keine echten Kontostände, Trades oder Server-Daten in
  Code, Tests oder Doku (öffentliches Repo). — Nachweis: Befehle aus `CLAUDE.md`, CI

## Abgrenzung
- Nicht Teil dieser Spec: Bot über die Seite starten/stoppen oder Not-Aus (schreibender Zugriff, eigenes ADR),
  Zeitraumwahl oder Zoom im Chart, Export, Kennzahlen wie Sharpe/Drawdown-Statistik (M3), Benachrichtigungen,
  Anzeige der Umgebungsvariablen des laufenden Bots (nur `secrets.env`/Standardwerte), Dienst/Deploy (Nr. 5/6).

## Relevante Entscheidungen & Learnings
- ADR-0016 (vorgeschlagen, mit dieser Spec): nur lesender Zugriff, genau ein Schreiber.
- ADR-0015: 127.0.0.1, Login, Sicherheits-Header, Standardbibliothek; CSP ohne Skript und Inline-Style → Chart als
  SVG mit Attributen, Refresh per Meta-Tag.
- ADR-0012 / ADR-0013 / ADR-0004: Depots luna/sol + Buy & Hold, Budget je Depot und UTC-Tag, kein Echtgeld.
- ADR-0006: Web-Prozess braucht Rechte auf das DB-Verzeichnis (WAL-`-shm`), also unter dem Dienstbenutzer.
- LEARNINGS #web „Formular-POST hängt …“: `ThreadingHTTPServer` bleibt; jede Anfrage öffnet ihre eigene
  DB-Verbindung (sqlite-Verbindungen nicht zwischen Threads teilen).
- `CLAUDE.md`: Handelshistorie nie still verändern; öffentliches Repo → Fixtures statt echter Daten.

## Ansatz
- `web/readonly.py`: `open_readonly(path)` → Verbindung oder `None` (fehlt) bzw. Fehlermeldung (Schema), mit
  `mode=ro`, `query_only`, `busy_timeout` 2 s, `check_schema` aus `paper/db.py`.
- `web/overview.py`: reine Abfragefunktionen auf einer Verbindung, die kleine Dataclasses liefern (Depots,
  Equity-Reihen mit Ausdünnung, Lebenszeichen, Entscheidungen, GPT heute, Trades); Uhr als Parameter (testbar).
- `pages.py`: `overview_page(...)` mit Chart-SVG (Attribute statt Style), Navigation im Kopf für alle
  angemeldeten Seiten; `app.py`: Route `/`, Redirects nach Login/Einrichtung auf `/`; `cli.py`: `--db`.
- Verworfen: Status-Datei vom Bot (ADR-0016), clientseitiges Chart-Skript (CSP), Caching der Abfragen (unnötig bei
  kleinen Tabellen und einem Betrachter).

## Plan
- [x] 1. ADR-0016 + `web/readonly.py` mit Tests (Schreibversuch, Datei unverändert, paralleler Schreiber, fehlende
  DB, falsche Version) (AK2)
- [x] 2. `web/overview.py` mit Fixture-DB aus echten `paper_cycle`-Läufen: Depots, Lebenszeichen, Entscheidungen,
  GPT heute, Trades, Equity-Reihen + Ausdünnung (AK3–AK8 Daten)
- [x] 3. `overview_page` + Equity-Chart + Navigation + Meta-Refresh in `pages.py`/`style.py` (AK1, AK4, AK6, AK9 Darstellung)
- [x] 4. Route `/`, Redirects, `web --db` (AK1, AK9, AK10), Server- und CLI-Tests
- [x] 5. Browser-Prüfung (AK11), Doku: ARCHITEKTUR, README, `CLAUDE.md`-Regel präzisieren (ADR-0016), STAND

## Risiken & offene Fragen
- WAL und `mode=ro`: Der Leser braucht Schreibrecht auf `-shm`; auf dem Server muss der Web-Prozess als
  Dienstbenutzer laufen (Deploy, Nr. 5/6). Lokal vorab geprüft (SQLite 3.49): `mode=ro` liest eine WAL-DB auch ohne
  laufenden Schreiber und legt dabei `-shm`/`-wal` an; während einer offenen Schreibtransaktion sieht der Leser den
  letzten abgeschlossenen Stand; ein Schreibversuch scheitert; eine fehlende DB wird nicht angelegt.
- Die Seite zeigt Budgetanteile aus `secrets.env`/Standardwerten; setzt der laufende Bot andere Werte per Umgebung,
  weicht die Anzeige ab – im Hinweistext benennen.
- Equity-Reihen wachsen (ein Eintrag je Zyklus und Depot); die Ausdünnung hält den Chart klein, die Abfrage liest
  aber alle Zeilen – bei Bedarf später Zeitraum begrenzen.
- Echter Chart und Deko-Chart im Hintergrund dürfen nicht verwechselt werden: klare Beschriftung, Panel deckt den
  Hintergrund ab.

---

## Verlauf
- 2026-11-06: Schritte 1–5 umgesetzt. `budget._day_bounds` öffentlich als `day_bounds` (Übersicht zählt Anfragen über
  dieselben UTC-Grenzen wie die Budgetsperre); `pages._euro` als `budget_value` wiederverwendet. Nach Login und
  Ersteinrichtung geht es jetzt zu `/` statt `/settings` (bestehende Tests angepasst).
- 2026-11-06: Browser-Prüfung mit der Paper-DB aus dem Live-Test von Spec 005 (2 Zyklen): Lebenszeichen, GPT heute,
  Depots mit Abstand zu Buy & Hold, Equity-Chart, Entscheidungen mit LLM-Begründungen, Trades; 1280 px und 375 px
  ohne seitliches Scrollen (breite Tabellen scrollen in ihrem Panel). Lesen während eines laufenden Schreibers ist
  per Test belegt (`test_reader_sees_the_last_committed_state_while_the_bot_writes`); der Nutzer prüft AK11 noch selbst.
- 2026-11-06: AK11 vom Nutzer abgenommen („Passt“); PR #9 gemergt.
- 2026-11-06: Nachträglich mit Spec 008 geändert: Die Übersicht lädt sich alle 15 s neu statt alle 60 s (AK9) – im
  Gleichtakt mit den 60-s-Runden des Dienstes zeigte sonst jedes Laden dasselbe Alter. Der Banner zeigt Kurse und
  Depots statt fester Hinweise; „Letzter Zyklus“ in den Kennzahlen wird per Beschriftung gewählt.

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
