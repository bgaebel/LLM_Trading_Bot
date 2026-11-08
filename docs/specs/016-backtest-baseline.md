# Spec 016: Strategie-Labor II – Kerzenarchiv, Backtest mit dem Live-Code und Basisbericht v0.3.1

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s016-backtest` · **Meilenstein:** M3 (vorgezogen: Strategie-Labor)
- **Erstellt:** 2026-11-07

## Ziel
Die öffentliche Bitvavo-Historie liegt auf dem PC. Die klassischen Strategien v0.3.1 und Buy & Hold laufen darüber durch
genau den Code, der live läuft. Ein Basisbericht zeigt je Quartal von **Q2 2021 bis Q3 2025**, was jede Strategie nach
Kosten gebracht hätte – mit Unsicherheitsbändern und Vergleich mit Buy & Hold gleicher Investitionsquote. Alles ab
**2025-10-01** bleibt versiegelt: Es ist der unberührte Prüfzeitraum für spätere Verbesserungen (Spec 018).

## Akzeptanzkriterien
- [ ] AK1: **Kerzenarchiv** (`lab/archive.py`, CLI `trading-bot lab fetch`, Daten in `candles.db` im Labor-Ordner außerhalb
  von Repo und Dropbox, ADR-0026):
  - lädt `1m`, `15m`, `1h`, `4h` für BTC-EUR und ETH-EUR über `GET /candles` ohne Schlüssel, in festen Fenstern
    `[start, end)` mit höchstens 1440 Intervallen, `end` = Öffnungszeit;
  - nur abgeschlossene Fenster gelten als fertig; Zeilen mit `open + step > Abrufzeit` werden nie geschrieben, ein
    Fenster bis in die Gegenwart bleibt „offen“ und wird später vervollständigt;
  - jede Zeile mit dem Parser des Live-Clients geprüft; Kerzen unverändert gespeichert (Lücken bleiben Lücken), mit
    Herkunft `bitvavo-api` und Abrufzeit; fertige Fenster werden nie überschrieben; Fortsetzen nach Abbruch;
  - Rate-Limit: wertet `bitvavo-ratelimit-remaining` aus, bremst unter 200, beendet sich bei 429 mit Angabe, ab wann
    es weitergeht;
  - Abgleich, wo 1m und höhere Intervalle zugleich vorliegen (Summe gleich?), als Bericht.

  — Nachweis: `tests/lab/test_archive.py::test_pages_by_open_time`, `::test_forming_bar_never_written`,
  `::test_open_window_completed_later`, `::test_resume_writes_each_window_once`, `::test_429_stops_and_reports_retry_at`,
  `::test_rows_checked_by_live_parser`
- [ ] AK2: **Historischer Client** (`lab/history.py`) erfüllt `MarketClient`:
  - `closed_candles(symbol, interval, now_epoch)`: die letzten 500 Zeilen bis zur Uhr, die laufende Kerze zählt mit,
    wenn das Archiv für ihr Intervall eine Zeile hat (ausdrücklich eine Näherung), danach der Live-Filter
    (`open + step <= now`);
  - `quotes()`: Mitte = Schlusskurs der letzten 1m-Zeile mit `ts + 60 <= now` (letzter Handel vor der Uhr, nie
    später), Bid/Ask = Mitte ± halber Spread aus dem Spread-Modell (AK6); eine fehlende Minute liefert trotzdem eine
    Quote;
  - nie ein Wert aus der Zukunft.

  — Nachweis: `tests/lab/test_history.py::test_never_returns_future_rows`, `::test_quote_uses_last_trade_before_clock`,
  `::test_missing_minute_still_quotes`, `::test_window_499_and_500_within_tolerance`
- [ ] AK3: **Gleichheit mit dem Live-Client:** Echte Rohantworten von `/candles` (500 Zeilen, neueste zuerst, laufende
  Kerze dabei) und `/ticker/book` zu mehreren Rundenzeiten liegen eingefroren in `tests/lab/fixtures/` (öffentliche
  Daten). Der echte `BitvavoClient` mit Fake-Transport und der `HistoricalClient` aus denselben Zeilen liefern gleiche
  `closed_candles`; ein kurzer Lauf ergibt gleiche Prüfsummen von `decisions`, `trades`, `equity`. Mutationsgruppe
  `lab` (500 → 499, `<=` → `<`, laufende Kerze nicht gezählt, Quote aus der nächsten Minute) wird vollständig
  erkannt. — Nachweis: `tests/lab/test_history_golden.py`, `uv run python tools/golden/mutation_check.py lab`
- [ ] AK4: **Lauf** (`lab/backtest.py`, CLI `trading-bot lab backtest`): simulierte Uhr `k·60 + 5` s, je Runde der
  unveränderte `paper_cycle` mit `trend, breakout, meanrev, ensemble` und Buy & Hold, ohne GPT, `Controls()` ohne
  Pause, Speicher-DB.
  - **Getrennte Abschnitte je Quartal** mit frischem Kapital (10.000 €), parallel in Prozessen; Arbeiter liefern nur
    Ergebnisse, der Hauptprozess schreibt.
  - **Datenvorlauf statt simulierter Einlaufzeit:** Der Abschnitt beginnt am Quartalsanfang; vorher müssen je Markt und
    Intervall ≥ 500 Zeilen im Archiv liegen.
  - **Zulassung:** Ein Quartal wird gewertet, wenn je Markt ≥ 90 % der fälligen Entscheidungen fielen; sonst „nicht
    gewertet“ mit Anteil. BTC-15m gibt es erst ab 2021-02-20 – gewertet wird ab Q2 2021.
  - **Quartalsende:** Variante „verkauft mit vollen Kosten“ und Variante „zum Mittelkurs bewertet“, beide im Bericht.
  - Ein Drawdown-Stopp beendet das Depot nur im laufenden Quartal (Datum im Bericht, Tage danach als Bargeld).
  - **Versiegelt:** Läufe über Daten ab 2025-10-01 verweigert das Labor, außer als registrierter Prüflauf eines
    Kandidaten (Spec 018).

  — Nachweis: `tests/lab/test_backtest.py::test_quarter_needs_lead_rows`, `::test_eligibility_90_percent`,
  `::test_quarter_end_variants`, `::test_halt_only_for_its_quarter`, `::test_sealed_period_refused`,
  `::test_workers_do_not_write`
- [ ] AK5: **Treue-Test gegen live** (`trading-bot lab fidelity --bot <kopie> [--obs <kopie>]`): Für jeden aufgezeichneten
  Snapshot (klassische und GPT-Entscheidungen; Referenzsignale in `inputs` und `trigger_signals`) wird zur Zeit
  `inputs.utc` nachgebaut, mit Kosten aus der gespeicherten Quote und Gewicht aus `current_weight`:
  - Aktion und Ziel exakt gleich; Indikatoren `round(neu, 5)` = gespeichert ± eine Stelle; Prozentfelder als „nur grob
    prüfbar“ getrennt;
  - mit Beobachtungen (Spec 015) zusätzlich Gleichheit der Fenster-Prüfsummen und ungerundeten Indikatoren (1e-12);
  - unter 300 Vergleichen je Markt: „Treue nicht belegt“, nie „100 %“; jede Abweichung mit Ursache.

  — Nachweis: `tests/lab/test_fidelity.py::test_too_few_is_not_established`, `::test_rounding_step_allowed`,
  `::test_action_mismatch_listed`, [manuell] Lauf gegen eine Live-Kopie (Spec 017 `lab-copy`, sonst eine mit
  Zustimmung gezogene Sicherung)
- [ ] AK6: **Kostenmodell und Kosten-Empfindlichkeit:**
  - Spread je Zeitraum: vor eigener Aufzeichnung ein vorsichtiger Wert je Quartal aus Stichproben öffentlicher Trades
    (`/trades`), danach der aufgezeichnete Median; im Bericht als Annahme;
  - **Kosten-Gleichstand bei festen Entscheidungen:** aus der Tradeliste des Laufs mit Modellkosten,
    `netto(k) = brutto − k · Round-Trip-Volumen` (analytisch, kein neuer Lauf);
  - **Kostenraster** 0,3 / 0,6 / 0,9 / 1,2 % als vollständige neue Läufe, ausgewiesen als „Entscheidungen reagieren
    auf Kosten“ und im Register als Varianten gezählt.

  — Nachweis: `tests/lab/test_metrics.py::test_break_even_fixed_decisions`, `tests/lab/test_spread_model.py`
- [ ] AK7: **Kennzahlen** (`lab/metrics.py`, aus Arena `research.py`/`reporting.py` mit Herkunftsvermerk), nur aus
  der Standardbibliothek:
  - je Depot und Quartal: Nettorendite, maximaler Drawdown, Round-Trips, Umschlag, Gebühren, mittlere
    Investitionsquote, brutto und netto;
  - Vergleich: Buy & Hold gleicher Quote als Formel `r_bench,t = Σ_m ē_m · r_BH,m,t` (ē_m = mittlere Quote der Strategie
    je Markt im Quartal, täglich umgeschichtet, ohne und mit Kosten) und Bargeld;
  - Unsicherheit: Sharpe-Quote und Übertrendite über alle gewerteten Tage gepoolt mit stationärem Block-Bootstrap
    (≈ 7 Tage, 5.000 Ziehungen, `random.Random` mit festem Seed), 95-%-Band und Satz „mit Zufall vereinbar: ja/nein“;
    je Quartal nur die einfache Tabelle und ihre Streuung;
  - Marktphasen: mittlere Nettorendite je Trade mit Band und Anzahl je Zelle (Regime × Volatilitäts-Drittel aus
    rückblickendem Fenster × Richtung); Zellen unter 30 Trades markiert;
  - Datenqualität: Anteil Runden ohne Entscheidung je Markt und Code, Sperrzeiten durch Lücken in höheren Intervallen.

  — Nachweis: `tests/lab/test_metrics.py::test_fifo_cases_from_arena`, `::test_benchmark_formula`,
  `::test_bootstrap_reference_values`, `::test_cells_use_past_only`
- [ ] AK8: **Prüfregister** (`lab/register.py`, `runs.db` plus fortlaufende JSONL-Kette mit Prüfsummen, die jedem Bericht
  beiliegt): Versuch = Konfigurations-Hash × Datenfenster × Variante; gleiche Wiederholungen werden zusammengefasst;
  Code-Commit und sauberer Arbeitsbaum, Datensatz-Prüfsumme, Tagesrenditen. Nie löschen oder überschreiben; der Bericht
  nennt die Zahl der Versuche. — Nachweis: `tests/lab/test_register.py::test_rerun_deduplicated`,
  `::test_chain_detects_loss`, `::test_aborted_quarter_recorded`
- [ ] AK9: **Diagnose-Lauf „Was kosten die Sperrzeiten?“:** ein registrierter Lauf mit markierter Abweichung
  (Lücken-Toleranz in höheren Intervallen), nur als Diagnose im Bericht, ausdrücklich keine Strategieversion.
  — Nachweis: `tests/lab/test_backtest.py::test_diagnostic_flagged_in_register`
- [ ] AK10: **Basisbericht** v0.3.1 (Markdown + eigenständiges HTML, nie überschreiben): Gesamt, Quartale (gewertet und
  nicht gewertet), Kosten, Marktphasen, Unsicherheit, Datenqualität, Diagnose, Treue-Ergebnis, Annahmen, Zahl der
  Versuche, Laufzeit. Lesbar für Laien: jede Zahl mit Band und Fallzahl; unter 100 Round-Trips „kein Urteil“, 100–300
  „indikativ“. — Nachweis: `tests/lab/test_report.py::test_labels_by_round_trips`, `::test_never_overwrites`
- [ ] AK11: **Laufzeit und Isolation:** Benchmark `tests/lab/test_backtest_speed.py` (Marke `slow`, eine simulierte Woche
  gegen ein SQLite-Archiv); Ziele für Quartal und Gesamtlauf werden nach der ersten Messung an einem echten Quartal
  (2021 und 2025) im Verlauf festgelegt. `trading-bot --help` und der Dienst importieren `llm_trading_bot.lab` nie
  (lazy im Unterbefehl). — Nachweis: `tests/lab/test_isolation.py::test_cli_and_service_do_not_import_lab`
- [ ] AK12: [manuell] Archiv vollständig geladen; Basisbericht erzeugt und dem Nutzer als privates Artifact gezeigt.
- [ ] AK13: Doku: ARCHITEKTUR (Modul `lab`), `docs/labor.md` (Ordner, Bedienung, Lesart, Grenzen, versiegelter Zeitraum).
  — Nachweis: `scripts/docs-check.sh`, Review
- [ ] AK14 (aus Spec 020 AK8): **Ausführung über USDC** – Backtest-Variante `usdc` (ADR-0029/0030):
  - rechnet `trend-usdc`, `breakout-usdc`, `meanrev-usdc`, `ensemble-usdc` und `buyhold-usdc` statt v0.3.1 (Buy & Hold
    läuft wie live mit) über dasselbe BTC-EUR-/ETH-EUR-Archiv, mit ausdrücklichem `BotConfig`; `base`, Kostenraster und
    Diagnose rechnen weiter genau die v0.3.1-Depots, unabhängig von `BOT_USDC_AGENTS`;
  - BTC-USDC gibt es erst ab 2024-07-01: beide Kurse des Wegs aus der BTC-EUR-Mitte, USDC-EUR = 1,0 mit 1,1 bp
    Spread (gemessen), BTC-USDC = BTC-EUR-Mitte / 1,0 mit 3 bp (gemessen Median 2 bp, vorsichtig) – als Annahme im
    Bericht; Kosten des Wegs aus `paper/usdc.py`;
  - eigene Variante im Prüfregister (Version `v0.3.1-usdc`, eigener Depot-Satz); frühere Konfigurations-Hashes bleiben
    gültig;
  - Bericht: Abschnitt „Ausführung über USDC“ neben dem Basislauf, nur über Quartale, die in beiden gewertet sind;
  - Treue-Test: Entscheidungen der USDC-Depots wie ihr Zwilling nachgebaut, BTC mit den Wegkosten aus den
    aufgezeichneten Büchern (`observations.db`); fehlen sie, mit Ursache übersprungen, nie still weggelassen.

  — Nachweis: `tests/lab/test_backtest.py::test_usdc_variant`, `tests/lab/test_report.py::test_usdc_variant_shown_next_to_base`,
  `tests/lab/test_fidelity.py::test_usdc_decisions_rebuilt_on_the_route_costs`,
  `::test_usdc_btc_without_observations_listed_not_compared`,
  `tests/lab/test_register.py::test_variant_field_added_later_keeps_earlier_hashes`

## Abgrenzung
- Keine Parametersuche, keine neue Strategieversion, keine GPT-Backtests (ADR-0026), kein Echtgeld (ADR-0004).
- Keine Zahlen aus dem versiegelten Zeitraum ab 2025-10-01.
- Quartale vor Q2 2021 (BTC ohne 15m-Kerzen): als „nicht gewertet“ gelistet; ein aus 1m nachgebautes 15m wäre eine
  Datenannahme und gehört nicht in den Basisbericht.

## Relevante Entscheidungen & Learnings
- ADR-0026 (Live-Pfad, Treue-Test, Register, versiegelter Zeitraum), ADR-0007 (bitgleich, Golden nie anpassen),
  ADR-0017 (Runde je Minute), ADR-0021 (Drawdown-Stopp), ADR-0003 (Arena-Code mit Herkunftsvermerk), ADR-0004.
- LEARNINGS #golden „Golden-Fixtures beweisen Gleichheit nur mit Grenzfällen“ (Mutationsgruppe mit benannten
  Mutanten), #test „random.gauss rechnet je Plattform anders“ (Bootstrap nur mit `randrange`), #ci „Tests grün unter
  Windows, rot in der Linux-CI“, #bitvavo „Nachts fehlen ETH-EUR-Minutenkerzen“.

## Ansatz
- **Recherche 2026-11-07:** unveränderter `paper_cycle` lief 320 h über echte Kerzen (2,8 ms/Runde in-memory);
  Fensterlänge beeinflusst EMA/ADX (500 Zeilen); `end` ist exklusiv nach Öffnungszeit; Bitvavo hat 1h/4h seit
  2019-03, BTC-15m seit 2021-02-20; ETH-1m heute ~20 % Lücken, 2020 bis 41 %; eine fehlende 1h-Kerze sperrt beide
  Märkte ~60 h.
- **Module:** `lab/archive.py`, `lab/history.py`, `lab/backtest.py`, `lab/fidelity.py`, `lab/metrics.py`,
  `lab/spread.py`, `lab/register.py`, `lab/report.py`; CLI `trading-bot lab …` lazy geladen.
- **Verworfen:** eigener Vektor-Backtester; ein langer Lauf ohne Abschnitte; 1m-Tiefs für den Stopp; numpy (unnötig,
  bricht Release und CI).

## Plan
- [x] 1. Kerzenarchiv (AK1)
- [x] 2. Historischer Client mit Zukunfts-Tests (AK2)
- [x] 3. Live-Rohantworten einfrieren, Gleichheit, Mutationsgruppe (AK3)
- [x] 4. Lauf: Abschnitte, Zulassung, Versiegelung, Isolation (AK4, AK11 Teil)
- [x] 5. Spread-Modell und Kennzahlen (AK6, AK7)
- [x] 6. Prüfregister und Diagnose-Lauf (AK8, AK9)
- [x] 7. Treue-Test (AK5)
- [x] 8. Bericht und Laufzeitmessung (AK10, AK11)
- [ ] 9. Doku, Prüfung (Workflow), PR (AK13) – Doku geschrieben (`docs/labor.md`, ARCHITEKTUR); Prüfung und PR offen
- [ ] 10. Archiv laden, Basisbericht an den Nutzer (AK12) – Archiv geladen; Backtest, Bericht und Artifact offen
- [x] 11. Variante `usdc` nach dem Merge von Spec 020 (AK14)

## Risiken & offene Fragen
- **Ergebnis kann ernüchtern:** Wahrscheinlich schlägt keine Strategie Buy & Hold verlässlich – ein gültiges Ergebnis.
- **Spread-Annahme vor der Aufzeichnung:** modelliert; die Kosten-Empfindlichkeit macht sie sichtbar.
- **Laufzeit:** erst gemessen, dann festgelegt (AK11).

---

## Verlauf
- 2026-11-07: Entwurf von vier Prüfern gegengelesen (44 Punkte, 7 Blocker): versiegelter Prüfzeitraum ab 2025-10-01,
  gewertet ab Q2 2021 mit Zulassungsregel, Quote ohne Vorgriff, Gleichheit gegen eingefrorene Live-Rohantworten,
  Treue-Test mit Mindestzahl, Kosten-Gleichstand bei festen Entscheidungen, Benchmark als Formel, Register-Einheit,
  Diagnose-Lauf, nur Standardbibliothek.
- 2026-11-08: Umsetzung in Wellen mit parallelen Arbeitszweigen (1: Paket, Kennzahlen, Prüfregister, Kerzenarchiv und
  Spread-Modell; 2: historischer Client mit eingefrorenen Live-Antworten und Mutationsgruppe `lab`, CLI, Bericht;
  3: Backtest je Quartal, Treue-Test). Danach mehrere Prüfrunden (Register, Bericht, Backtest, Treue-Test,
  Order-Pfad-Wächter) mit Korrekturen, u. a.: versiegelte Einträge ohne jede Zahl, Spreads nur aus ganzen Quartalen,
  gesperrte Stunden auch aus veralteten höheren Intervallen, Register-Reihenfolge fest, Beobachtungs-Abfragen über
  Indizes. Archiv geladen: 6,6 Mio. Kerzen, Spread-Stichproben 991 Stunden.
- 2026-11-08: main mit Spec 020 (USDC-Depots) hereingeholt. Ursachen der roten Labor-Tests: (1) der Treue-Test-Lauf
  las `BOT_USDC_AGENTS` als Standard (alle an) und legte `buyhold-usdc` nicht an; der Treue-Test kannte `*-usdc` nicht
  (BTC hätte er mit BTC-EUR-Kosten nachgebaut); (2) der Live-Client liest seit 020 auch BTC-USDC und USDC-EUR aus dem
  eingefrorenen `/ticker/book`, und `Symbol` hat jetzt vier Märkte – die Gleichheitstests liefen über `Symbol` statt
  über die Kerzenmärkte und fragten Kerzen von BTC-USDC ab. Behoben ohne Änderung der Fixtures und des Live-Codes:
  ausdrückliche Konfiguration ohne USDC-Depots, Gleichheit über `SYMBOLS`, zusätzlich ein Gleichheitslauf mit
  USDC-Depots auf den echten Büchern; Treue-Test mit Wegkosten aus `observations.db`; Variante `usdc` (AK14) und Doku
  (AK13).

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
