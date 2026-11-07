# Spec 015: Strategie-Labor I – Live-Aufzeichnung in der Beobachtungs-DB

- **Status:** freigegeben
- **Issue:** – · **Branch:** `feat/s015-live-aufzeichnung` · **Meilenstein:** M3 (vorgezogen: Strategie-Labor)
- **Erstellt:** 2026-11-07

## Ziel
Der Bot zeichnet ab dem nächsten Deploy auf, was sich später nicht mehr beschaffen lässt: Bid/Ask mit Tiefe, was
Bitvavo je Runde wirklich geliefert hat, Runden ohne Entscheidung mit Grund, Strategieversion und Release je
Entscheidung, GPT-Rohantwort, ungekapptes Ziel und Auslöser. Am Handeln ändert sich nichts. Das ist die Datengrundlage
für Treue-Test (Spec 016) und Signal-Auswertung (Spec 017).

## Akzeptanzkriterien
- [ ] AK1: **Quote-Tiefe ohne Rückwirkung:** Der Marktdaten-Client liest `bidSize`/`askSize` aus `/ticker/book` getrennt
  von der Quote (eigene Zuordnung `QuoteBook.depth`, nach bestem Bemühen außerhalb der Gültigkeitsprüfung). `Quote`,
  ihr Vergleich, die Duplikatprüfung, Snapshot, `feature_payload`, LLM-Kontext, Entscheidungen und Orders bleiben
  unverändert; alle Golden-Fixtures grün, `PROMPT_VERSION` gleich. — Nachweis:
  `tests/market/test_quote_depth.py::test_missing_size_keeps_quote`, `::test_garbage_size_keeps_quote`,
  `::test_size_not_in_duplicate_check`, `::test_quote_positional_construction_unchanged`, bestehende Golden-Tests
- [ ] AK2: **Gründe und Auslöser als Codes, gleiches Verhalten:**
  - `MarketDataError` bekommt einen stabilen Code (`gap_1m` … `gap_240m`, `stale`, `spread`, `quote_missing`,
    `conflict`, `http`, `parse`), Meldetext unverändert;
  - `llm_due` liefert einen Grund (`first` | `interval` | `move` | `signal_change` | `new_market` | `None`), dessen
    Wahrheitswert dem bisherigen `bool` entspricht.

  — Nachweis: `tests/market/test_error_codes.py`, `tests/llm/test_due_reason.py::test_reason_truth_equals_old_bool`
  (Tabellentest über Zeit/Bewegung/Signalwechsel), Golden-LLM-Fälle unverändert
- [ ] AK3: **Beobachtungs-DB** `observations.db` (ADR-0027) im Datenverzeichnis, eigene Schemaversion, nur anhängend:
  - **Wer schreibt:** nur der Inhaber der `bot.db`-Schreibsperre, also der Dienst; keine zweite Sperre.
    `paper-cycle` schreibt keine Beobachtungen.
  - **Wann:** Während `paper_cycle` landen Beobachtungen nur in einem Puffer im Speicher (keine Ein-/Ausgabe, kann
    nicht scheitern). `Service._work` schreibt den Puffer nach der Runde in einer eigenen Transaktion, außerhalb
    jeder `bot.db`-Transaktion, mit `busy_timeout` ≤ 1 s.
  - **Fehler:** Sperre belegt, andere Schemaversion, beschädigte oder nicht zu öffnende Datei schalten nur die
    Aufzeichnung ab (Zähler und letzter Fehler). Exit-Code, `last_result`, `state`, Fehlerzähler und Gesundheitsprüfung
    ändern sich nie dadurch.
  - **Datei-Lebenszyklus:** angelegt nur, wenn sie fehlt; eine andere Version wird verweigert, nie migriert; bei
    Beschädigung wird nie neu angelegt; jeder Dienststart schreibt eine Startmarke (Lücken sichtbar).

  — Nachweis: `tests/paper/test_observations.py`, `tests/service/test_observations_wired.py::test_golden_rounds_same_with_raising_recorder`,
  `::test_busy_lock_keeps_trading`, `::test_v99_file_keeps_trading`, `::test_corrupt_file_not_recreated`
- [ ] AK4: **Was aufgezeichnet wird** (Bezug über den natürlichen Schlüssel `agent, symbol, bar_ts` bzw. `llm_call_id`):
  - je Runde und Markt: jede Buch-Abfrage mit Zweck (Start, Nachprüfung, nach GPT), Zeit, Bid, Ask, Größen;
    Snapshot-Ergebnis (`ok` oder Code) und ob eine Entscheidung fiel;
  - je Runde, Markt und Intervall: erhaltene Zeilen, erste und letzte Öffnungszeit, ob die laufende Kerze dabei war,
    Volumen der letzten geschlossenen Kerze, SHA-256 der verwendeten Zeilen (für den Treue-Test, Spec 016);
  - je Entscheidung: Strategie, `STRATEGY_VERSION`, Release-SHA (aus dem eigenen Installationspfad
    `releases/<sha>`, sonst `unknown`), ungerundete Indikatoren;
  - je GPT-Versuch: Auslöser-Code mit Werten, Takt-Einstellungen, Rohantwort (Text, auch bei ungültiger Antwort),
    ungekappte Ziele je Markt.

  Größe gemessen mit einem synthetischen Tag in echter Zeilenrate; Aufbewahrung danach festgelegt und im Verlauf
  vermerkt. — Nachweis: `tests/paper/test_observations.py::test_records_*`, `::test_size_of_one_day`
- [ ] AK5: **Statusdatei:** neue Felder `observations_written`, `observations_failed`, `observations_last_error` mit
  Standardwerten in `ADDED` (ältere Leser vertragen die neue Datei, neue Leser die alte); `release.sh status` zeigt
  sie. — Nachweis: `tests/service/test_status.py::test_observation_fields_*`, `tests/deploy/test_release.py::test_status_observations`
- [ ] AK6: **Sicherung:** `observations.db` wird in einem eigenen Aufruf (eigenes Profil: Schema, Namensmuster
  `observations-…`, Unterordner, 14 tägliche Kopien) gesichert; ein Fehler dabei lässt die `bot.db`-Sicherung, die
  Sicherung vor dem Update und jeden Deploy unberührt. Wiederherstellen nur in ein neues Ziel. — Nachweis:
  `tests/ops/test_backup.py::test_observations_profile_*`, `::test_corrupt_observations_keeps_bot_backup`,
  `tests/deploy/test_release.py::test_observations_backup_*`
- [ ] AK7: **Rückfall und Isolation:** Ein Release vor 015 ignoriert `observations.db`; ein Rollback von einer späteren
  Beobachtungs-Version stellt nur die Aufzeichnung ab. — Nachweis: `tests/deploy/test_release.py::test_rollback_before_015_*`
- [ ] AK8: [manuell] Mit Zustimmung: Deploy; danach zeigt `release.sh status` steigende `observations_written` und
  `observations_failed = 0`; eine Stichprobe (Zeilenzahlen je Tabelle, keine Inhalte) bestätigt Quotes je Runde,
  Snapshot-Codes und eine GPT-Rohantwort.
- [ ] AK9: Doku: ADR-0026, ADR-0027, ARCHITEKTUR (Beobachtungs-DB, Puffer), `docs/betrieb/server.md` (Sicherung,
  Status), Datenschutz unverändert (keine Besucherdaten). — Nachweis: `scripts/docs-check.sh`, Review

## Abgrenzung
- Kein Archiv, kein Backtest, keine Auswertung (Spec 016, 017). Kein `lab-copy` (Spec 017).
- Keine Änderung an Strategien, Kosten, Prompt, Takt, Risiko oder Handelsablauf.

## Relevante Entscheidungen & Learnings
- ADR-0027 (Beobachtungs-DB), ADR-0026 (Labor).
- ADR-0012 (Schema von `bot.db`, nie still migrieren), ADR-0016 (Leser nur `mode=ro`, `paper-cycle` als
  zweiter möglicher Schreiber), ADR-0017 (Runde je Minute, Config je Runde), ADR-0020 (Deploy, Gesundheit, Sicherung
  nur auf dem Server), ADR-0013 (GPT-Auslöser), ADR-0007 (Golden nie anpassen).
- LEARNINGS #golden „Golden-Fixtures beweisen Gleichheit nur mit Grenzfällen“ (eigene Tests für Duplikat und
  fehlende Größen), #zeit „Gespeicherte Zeitstempel sind ganze Sekunden“, #bitvavo „Nachts fehlen
  ETH-EUR-Minutenkerzen“, #signal „Signal-Handler setzt Event“ (Puffer erst nach der Runde schreiben).

## Ansatz
- **Recherche 2026-11-07:** `decisions.inputs` enthält den gerundeten `feature_payload`, aber keine Tiefe, keine
  Rohantwort, keinen Auslöser; Runden ohne Entscheidung stehen nur im Journal (15 Tage). Klassische Depots laufen live
  (Abfrage am 2026-11-07: trend, breakout, meanrev, ensemble mit Entscheidungen).
- **Module:** `paper/observations.py` (Puffer, Schreiber, Schema), Anpassungen in `market/bitvavo.py` (Tiefe),
  `market/models.py` (Codes), `market/snapshot.py` (Codes, Fensterdaten in den Puffer), `llm/due.py` (Grund),
  `llm/cycle.py` (Rohantwort vor dem Parsen, Ziel vor dem Kappen), `service/run.py` (Schreiben nach der Runde),
  `service/status.py`, `ops/backup.py` (Profile), `deploy/*` (Backup-Aufruf).
- **Verworfen:** Schreiben innerhalb der Entscheidungs-Transaktion (hält `bot.db` offen, Fehler werden zu
  Marktfehlern); eigene Sperre (Exit 3 beendet den Dienst dauerhaft); Release-SHA per Umgebungsvariable (`public`
  rendert Units mit `current`).

## Plan
- [ ] 1. Quote-Tiefe getrennt, Tests (AK1)
- [ ] 2. Fehlercodes und Auslöser-Grund mit Gleichheitstests (AK2)
- [ ] 3. Beobachtungs-DB: Schema, Puffer, Schreiber, Lebenszyklus (AK3)
- [ ] 4. Aufzeichnungspunkte in Snapshot, Zyklus, GPT; Größenmessung (AK4)
- [ ] 5. Statusfelder (AK5)
- [ ] 6. Sicherungsprofil und Deploy-Anpassung (AK6, AK7)
- [ ] 7. Doku, ADR, Prüfung (Workflow), PR (AK9)
- [ ] 8. Mit Zustimmung: Deploy, Stichprobe (AK8)

## Risiken & offene Fragen
- **M1 „7 Tage ununterbrochen“:** Der Nutzer hat entschieden (2026-11-08): Die 7 Tage zählen ab dem letzten Deploy.
  Der Deploy von 015 startet die M1-Uhr neu.
- **Speicher:** grob 10.000+ Zeilen am Tag plus GPT-Texte; AK4 misst und legt die Aufbewahrung fest.
- **Lizenz:** Aus der GPL-3.0-Arena übernommener Code liegt im öffentlichen Repo ohne Lizenzangabe – Entscheidung des
  Nutzers, außerhalb dieser Spec.

---

## Verlauf
- 2026-11-07: Erster Entwurf (Archiv + Aufzeichnung + Bericht) von vier Prüfern gegengelesen (44 Punkte, 7 Blocker);
  neu geschnitten: Aufzeichnung allein und zuerst (geht täglich verloren), Archiv und Backtest in Spec 016,
  Signal-Bericht in Spec 017, Kandidaten in Spec 018; Schreiben nach der Runde aus einem Puffer, keine zweite Sperre,
  Tiefe außerhalb der Quote, Gründe als Codes, Release-SHA aus dem Pfad, eigenes Sicherungsprofil.

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
