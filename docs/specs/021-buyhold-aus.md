# Spec 021: Euro-Buy-&-Hold abschalten – Referenz ist Buy & Hold USDC

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s021-buyhold-aus` · **Meilenstein:** M1 (Nachtrag, Paper)
- **Erstellt:** 2026-11-08

## Ziel
Das Euro-Depot `buyhold` läuft im Paper-Bot nicht mehr: Es entscheidet, handelt und bewertet nicht mehr, und die
Übersicht zeigt es nicht mehr. Referenz für alle Depots ist `buyhold-usdc` (Spec 020, ADR-0030). Seine Zeilen in
`bot.db` (Depot, Entscheidungen, Trades, Equity) bleiben für immer unverändert (Nutzer 2026-11-08: „Ganz abschalten“).

## Akzeptanzkriterien
- [x] AK1: **Schalter:** `BotConfig.buyhold` (Standard aus) kommt aus der Umgebungsvariable `BOT_BUYHOLD`: fehlt sie,
  ist Buy & Hold aus; genau `1` = an, genau `0` = aus, jeder andere Wert ist ein Konfigurationsfehler (der Dienst
  startet nicht bzw. behält die letzte gültige Konfiguration). `buyhold-usdc` hängt weiter nur an
  `BOT_USDC_AGENTS`. Der Code, der Buy & Hold betreibt, bleibt (Labor, Rückweg). — Nachweis:
  `tests/paper/test_buyhold_off.py::test_buyhold_switch_is_off_by_default_and_strict`,
  `::test_benchmarks_follow_both_switches`
- [x] AK2: **Aus heißt unberührt:** Mit `BOT_BUYHOLD` aus legt eine Runde für `buyhold` keine Entscheidung, keinen
  Trade und keinen Equity-Punkt an, prüft keinen Drawdown und legt auf einer neuen DB kein Depot an; vorhandene
  Zeilen werden nie geändert oder gelöscht. `paper-cycle` meldet `buy & hold: off (BOT_BUYHOLD)`. — Nachweis:
  `tests/paper/test_buyhold_off.py::test_fresh_database_never_gets_a_buyhold_depot`,
  `::test_existing_buyhold_rows_stay_untouched_while_off`, `tests/test_cli.py::test_paper_cycle_says_buy_and_hold_is_off`
- [x] AK3: **Alle anderen Depots bitgleich:** v0.3.1-, GPT- und USDC-Depots schreiben mit Buy & Hold aus dieselben
  Zeilen (ohne `id`) wie mit Buy & Hold an. Bestehende Tests, die Buy & Hold einschließen, laufen mit
  `buyhold=True` bzw. `BOT_BUYHOLD=1`; eingefrorene Golden-Fixtures bleiben unverändert und grün. — Nachweis:
  `tests/paper/test_buyhold_off.py::test_other_depots_are_identical_with_buyhold_on_or_off`, Golden-Tests v0.3.1
- [x] AK4: **Neustart und Rückweg:** Eine `bot.db` mit `buyhold`-Historie öffnet mit Buy & Hold aus ohne Fehler
  (Schemaprüfung unverändert). Wieder an (`BOT_BUYHOLD=1` oder Rollback auf ein Release vor 021, das Buy & Hold
  immer betreibt) bewertet `buyhold` einfach weiter, ohne erneut zu kaufen. — Nachweis:
  `tests/paper/test_buyhold_off.py::test_old_database_with_buyhold_opens_with_buyhold_off`,
  `::test_switching_back_on_continues_valuing_without_buying_again`
- [x] AK5: **Übersicht zeigt nur aktive Depots:** Ein Depot ist aktiv, wenn sein neuester Equity-Punkt höchstens
  1 h (`DELAYED_SECONDS`) älter ist als der neueste Equity-Punkt der DB – oder als der letzte gespeicherte Kurs
  (Tabelle `market`) eines Markts, den es hält (bei USDC-Depots: dessen Route), wenn der älter ist; vor der ersten
  Runde (kein Equity-Punkt) und bei einem gehaltenen Markt ganz ohne Kurs ist es aktiv. Ein Kursausfall eines Markts
  blendet so kein Depot aus, egal wie lange. Die Regel braucht nur `bot.db` (ADR-0016), nicht die Umgebung des Bots. Ein inaktives
  Depot fehlt in Depottabelle, Equity-Verlauf, Laufband, letzten Signalen und letzten Trades – in der Ansicht mit
  Login und in der öffentlichen (ADR-0028); seine Historie bleibt in der DB. Die Freigabeliste der Einstellungen
  zeigt weiter jedes gestoppte Depot (Spec 011). — Nachweis:
  `tests/web/test_reference.py::test_stopped_depot_leaves_every_list`,
  `::test_a_depot_stays_shown_for_an_hour_after_its_last_valuation`,
  `::test_a_long_quote_outage_of_one_market_hides_no_depot`,
  `::test_the_active_window_ends_at_the_last_quote_of_a_held_market`,
  `::test_before_the_first_round_every_depot_is_shown`, `::test_public_view_hides_the_stopped_depot`
- [x] AK6: **Spalte „vs. Buy & Hold USDC“:** Referenz ist `buyhold-usdc`, gemessen über seine Laufzeit:
  `vs = (E_jetzt / E_start − 1) − (R_jetzt / R_start − 1)` in Prozentpunkten; `R_start` ist der erste Equity-Punkt
  der Referenz, `E_start` der Equity-Punkt des Depots zu oder direkt vor diesem Zeitpunkt (fehlt er: „–“). Die
  Referenzzeile zeigt „–“. Kopfzeile und Fußnote nennen Buy & Hold USDC und seit wann gemessen wird. — Nachweis:
  `tests/web/test_reference.py::test_vs_is_measured_against_buyhold_usdc_since_its_start`,
  `::test_depot_younger_than_the_reference_shows_a_dash`,
  `::test_header_and_footnote_name_the_reference_and_its_start`
- [x] AK7: **Rückfall:** Ist `buyhold-usdc` nicht aktiv (USDC-Depots aus), ist `buyhold` die Referenz, falls aktiv,
  sonst gibt es keine („–“ in der Spalte). — Nachweis:
  `tests/web/test_reference.py::test_reference_falls_back_to_buyhold_then_to_none`
- [x] AK8: **Einstellungen:** Kein Bedienelement bietet `buyhold` an (Depotkacheln, Freigabe); der Hinweistext sagt,
  dass Buy & Hold USDC die Referenz ist. — Nachweis: `tests/web/test_reference.py::test_settings_never_offer_buyhold`
- [x] AK9: **Mutation:** Gruppe `buyhold` (Standard des Schalters, Überspringen in der Runde, Sichtbarkeitsregel,
  Wahl der Referenz, Formel) wird vollständig erkannt. — Nachweis:
  `uv run python tools/golden/mutation_check.py buyhold`
- [x] AK10: Doku: ADR-0031 (löst ADR-0012 ab), ARCHITEKTUR, `docs/betrieb/server.md` (Schalter, Rückweg), ROADMAP.
  — Nachweis: `scripts/docs-check.sh`
- [ ] AK11: [manuell] Mit Zustimmung: Deploy; im Journal kein `trade buyhold bought`, die Runden nennen
  `buyhold` nicht mehr; spätestens 1 h danach fehlt `buyhold` in der Übersicht; die Spalte heißt
  „vs. Buy & Hold USDC“. Die M1-Uhr startet neu.

## Abgrenzung
- Keine Änderung an Schema, Historie oder Zeilen von `buyhold` (CLAUDE.md: Historie nie still migrieren).
- Kein Schalter in der Weboberfläche: `secrets.env` speichert nur bekannte Felder, `BOT_BUYHOLD` gehört in die
  Umgebung (Drop-in), wie `TAKER_FEE` oder `LOG_LEVEL`.
- Datenschutzerklärung unverändert: es werden keine anderen Daten verarbeitet.
- Kein Echtgeld (ADR-0004); keine Änderung an Strategien, Gebühren oder Prompts.

## Relevante Entscheidungen & Learnings
- ADR-0012 (zwei GPT-Depots „plus Buy & Hold“) – wird durch ADR-0031 abgelöst.
- ADR-0030 (`buyhold-usdc`), ADR-0007 (v0.3.1 unverändert), ADR-0016 (Web liest nur), ADR-0017/0020 (Dienst, Deploy),
  ADR-0021 (Referenzen ohne Drawdown-Stopp), ADR-0022 (Broker-Naht unverändert), ADR-0028 (öffentliche Übersicht).
- LEARNINGS #web/#performance: Abfragen der Übersicht mit festen Grenzen (`LIMIT`, Index).
- LEARNINGS #golden: Gleichheit nur mit Grenzfällen belegt – deshalb Vergleich der vollständigen Zeilen.

## Ansatz
- `llm/agents.py`: Feld `buyhold` in `BotConfig`, `benchmarks` nimmt `buyhold` nur, wenn an. Weil `open_db`, die
  Bewertung und das Anlegen neuer Depots `config.depots` nutzen, berührt die Runde `buyhold` dann nirgends.
- `paper/cycle.py`: Erstkauf nur mit `config.buyhold` (sonst würde er auf einer alten DB in das ruhende Depot kaufen).
- `web/overview.py`: Aktivregel aus den ohnehin gelesenen neuesten Equity-Punkten je Depot, begrenzt durch den
  letzten Kurs der gehaltenen Märkte (eine Abfrage auf `market`, höchstens vier Zeilen); Referenz und
  Startpunkte mit je einer Index-Abfrage mit `LIMIT`; `load` filtert alle Listen auf aktive Depots.
- Verworfen: Sichtbarkeit „Equity-Punkt in der neuesten Runde“ oder „höchstens 1 h hinter dem neuesten Punkt der
  DB“ ohne Blick auf die Kurse (ein Depot mit Bestand in einem Markt ohne frischen Kurs verschwände genau dann, wenn
  etwas nicht stimmt – bei der zweiten nach 1 h, samt Referenz); die Liste der Depots aus der Umgebung des Bots (die
  Weboberfläche kennt sie nicht, ADR-0016); Zeilen löschen oder umbenennen (Historie).

## Plan
- [x] 1. Spec, ADR-0031, INDEX, ROADMAP
- [x] 2. Schalter und Runde (AK1–AK4)
- [x] 3. Übersicht und Einstellungen (AK5–AK8)
- [x] 4. Mutationsgruppe `buyhold` (AK9)
- [x] 5. Doku ARCHITEKTUR, `docs/betrieb/server.md` (AK10)
- [ ] 6. Mit Zustimmung: Deploy (AK11)

## Risiken & offene Fragen
- **Euro-Depots ohne passende Referenz:** v0.3.1- und GPT-Depots handeln BTC über BTC-EUR, die Referenz über
  BTC-USDC; der Unterschied der Ausführungskosten steckt mit in „vs.“ (Nutzer informiert 2026-11-08).
- **Depots jünger als die Referenz** zeigen „–“, solange es keinen Equity-Punkt zu Beginn der Referenz gibt.
- **M1-Uhr:** der Deploy startet die 7 Tage neu.
- **Labor (Spec 016) beim Zusammenführen:** `lab/backtest.py` baut `BotConfig(classic=CLASSIC, llm=None)` und
  erwartet ein Depot `buyhold` (`lab/model.py`: `DEPOTS = (*CLASSIC, BUYHOLD)`). Nach dem Merge mit 021 ist
  Buy & Hold dort aus, das Labor-Buy-&-Hold kauft und bewertet dann nichts (Git meldet keinen Konflikt in `src/`).
  Beim Merge `buyhold=True` im Labor setzen; der Stolperdraht
  `tests/paper/test_buyhold_off.py::test_strategy_lab_asks_for_buyhold_where_it_expects_it` schlägt sonst fehl
  (bis dahin übersprungen, weil `lab` auf diesem Branch fehlt).

---

## Verlauf
- 2026-11-08: Nutzer: „den buy-hold euro kurs kannste raus nehmen... den brauchen wir ja nicht mehr“; auf Rückfrage
  „Ganz abschalten“ (nicht nur ausblenden), informiert über fehlende passende Referenz der Euro-Depots, neues ADR
  und Neustart der M1-Uhr.
- 2026-11-08: Umsetzung (Workflow, Branch `feat/s021-buyhold-aus`): Schalter `BOT_BUYHOLD` in `BotConfig`,
  Erstkauf nur mit Schalter, Aktivregel und Referenz in `web/overview.py`, Mutationsgruppe `buyhold` (24/24
  erkannt). Tests von vor Spec 021, die Buy & Hold einschließen, schalten es ausdrücklich an (`buyhold=True`,
  `BOT_BUYHOLD=1`, Harness-Standard); eingefrorene Fixtures unverändert. Geändert wurden nur zwei Erwartungen
  zur Referenz: in `tests/web/test_usdc_overview.py` ist jetzt `buyhold-usdc` die Referenz (vorher `buyhold`),
  in `tests/web/test_overview.py` misst „vs.“ seit dem ersten Punkt der Referenz statt ab Startkapital.
  Nebenwirkung der Aktivregel: auch ein abgewähltes klassisches Depot verschwindet nach 1 h aus der Übersicht
  (bisher blieb es mit altem Wert stehen); die Freigabeliste zeigt gestoppte Depots weiter alle. README nennt die
  Standard-Depots neu.
- 2026-11-08: Prüfung (Workflow) fand zwei Mängel, behoben: (1) Aktivregel blendete bei mehr als 1 h Kursausfall
  eines Markts jedes Depot mit Bestand darin aus, auch Referenz und `gpt-luna` – jetzt begrenzt der letzte Kurs der
  gehaltenen Märkte das Fenster (AK5 präzisiert, ADR-0031 auf dem Branch vor dem Merge angepasst), 5 neue Mutanten;
  (2) Docstring behauptete, das Labor schalte Buy & Hold ein – korrigiert, Risiko oben, Stolperdraht-Test.
  `CycleReport.buyhold` hat jetzt denselben Standard (aus) wie `BotConfig.buyhold`.

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** ADR-0031
