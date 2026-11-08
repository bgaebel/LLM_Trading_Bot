# Spec 021: Euro-Buy-&-Hold abschalten – Referenz ist Buy & Hold USDC

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s021-buyhold-aus` · **Meilenstein:** M1 (Nachtrag, Paper)
- **Erstellt:** 2026-11-08

## Ziel
Das Euro-Depot `buyhold` läuft im Paper-Bot nicht mehr: Es entscheidet, handelt und bewertet nicht mehr, und die
Übersicht zeigt es nicht mehr. Referenz für alle Depots ist `buyhold-usdc` (Spec 020, ADR-0030). Seine Zeilen in
`bot.db` (Depot, Entscheidungen, Trades, Equity) bleiben für immer unverändert (Nutzer 2026-11-08: „Ganz abschalten“).

## Akzeptanzkriterien
- [ ] AK1: **Schalter:** `BotConfig.buyhold` (Standard aus) kommt aus der Umgebungsvariable `BOT_BUYHOLD`: fehlt sie,
  ist Buy & Hold aus; genau `1` = an, genau `0` = aus, jeder andere Wert ist ein Konfigurationsfehler (der Dienst
  startet nicht bzw. behält die letzte gültige Konfiguration). `buyhold-usdc` hängt weiter nur an
  `BOT_USDC_AGENTS`. Der Code, der Buy & Hold betreibt, bleibt (Labor, Rückweg). — Nachweis:
  `tests/paper/test_buyhold_off.py::test_buyhold_switch_is_off_by_default_and_strict`,
  `::test_benchmarks_follow_both_switches`
- [ ] AK2: **Aus heißt unberührt:** Mit `BOT_BUYHOLD` aus legt eine Runde für `buyhold` keine Entscheidung, keinen
  Trade und keinen Equity-Punkt an, prüft keinen Drawdown und legt auf einer neuen DB kein Depot an; vorhandene
  Zeilen werden nie geändert oder gelöscht. `paper-cycle` meldet `buy & hold: off (BOT_BUYHOLD)`. — Nachweis:
  `tests/paper/test_buyhold_off.py::test_fresh_database_never_gets_a_buyhold_depot`,
  `::test_existing_buyhold_rows_stay_untouched_while_off`, `tests/test_cli.py::test_paper_cycle_says_buy_and_hold_is_off`
- [ ] AK3: **Alle anderen Depots bitgleich:** v0.3.1-, GPT- und USDC-Depots schreiben mit Buy & Hold aus dieselben
  Zeilen (ohne `id`) wie mit Buy & Hold an. Bestehende Tests, die Buy & Hold einschließen, laufen mit
  `buyhold=True` bzw. `BOT_BUYHOLD=1`; eingefrorene Golden-Fixtures bleiben unverändert und grün. — Nachweis:
  `tests/paper/test_buyhold_off.py::test_other_depots_are_identical_with_buyhold_on_or_off`, Golden-Tests v0.3.1
- [ ] AK4: **Neustart und Rückweg:** Eine `bot.db` mit `buyhold`-Historie öffnet mit Buy & Hold aus ohne Fehler
  (Schemaprüfung unverändert). Wieder an (`BOT_BUYHOLD=1` oder Rollback auf ein Release vor 021, das Buy & Hold
  immer betreibt) bewertet `buyhold` einfach weiter, ohne erneut zu kaufen. — Nachweis:
  `tests/paper/test_buyhold_off.py::test_old_database_with_buyhold_opens_with_buyhold_off`,
  `::test_switching_back_on_continues_valuing_without_buying_again`
- [ ] AK5: **Übersicht zeigt nur aktive Depots:** Ein Depot ist aktiv, wenn sein neuester Equity-Punkt höchstens
  1 h (`DELAYED_SECONDS`) älter ist als der neueste Equity-Punkt der DB; vor der ersten Runde (kein Equity-Punkt)
  sind alle Depots aktiv. Die Regel braucht nur `bot.db` (ADR-0016), nicht die Umgebung des Bots. Ein inaktives
  Depot fehlt in Depottabelle, Equity-Verlauf, Laufband, letzten Signalen und letzten Trades – in der Ansicht mit
  Login und in der öffentlichen (ADR-0028); seine Historie bleibt in der DB. Die Freigabeliste der Einstellungen
  zeigt weiter jedes gestoppte Depot (Spec 011). — Nachweis:
  `tests/web/test_reference.py::test_stopped_depot_leaves_every_list`,
  `::test_a_depot_stays_shown_for_an_hour_after_its_last_valuation`,
  `::test_before_the_first_round_every_depot_is_shown`, `::test_public_view_hides_the_stopped_depot`
- [ ] AK6: **Spalte „vs. Buy & Hold USDC“:** Referenz ist `buyhold-usdc`, gemessen über seine Laufzeit:
  `vs = (E_jetzt / E_start − 1) − (R_jetzt / R_start − 1)` in Prozentpunkten; `R_start` ist der erste Equity-Punkt
  der Referenz, `E_start` der Equity-Punkt des Depots zu oder direkt vor diesem Zeitpunkt (fehlt er: „–“). Die
  Referenzzeile zeigt „–“. Kopfzeile und Fußnote nennen Buy & Hold USDC und seit wann gemessen wird. — Nachweis:
  `tests/web/test_reference.py::test_vs_is_measured_against_buyhold_usdc_since_its_start`,
  `::test_depot_younger_than_the_reference_shows_a_dash`,
  `::test_header_and_footnote_name_the_reference_and_its_start`
- [ ] AK7: **Rückfall:** Ist `buyhold-usdc` nicht aktiv (USDC-Depots aus), ist `buyhold` die Referenz, falls aktiv,
  sonst gibt es keine („–“ in der Spalte). — Nachweis:
  `tests/web/test_reference.py::test_reference_falls_back_to_buyhold_then_to_none`
- [ ] AK8: **Einstellungen:** Kein Bedienelement bietet `buyhold` an (Depotkacheln, Freigabe); der Hinweistext sagt,
  dass Buy & Hold USDC die Referenz ist. — Nachweis: `tests/web/test_reference.py::test_settings_never_offer_buyhold`
- [ ] AK9: **Mutation:** Gruppe `buyhold` (Standard des Schalters, Überspringen in der Runde, Sichtbarkeitsregel,
  Wahl der Referenz, Formel) wird vollständig erkannt. — Nachweis:
  `uv run python tools/golden/mutation_check.py buyhold`
- [ ] AK10: Doku: ADR-0031 (löst ADR-0012 ab), ARCHITEKTUR, `docs/betrieb/server.md` (Schalter, Rückweg), ROADMAP.
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
- `web/overview.py`: Aktivregel aus den ohnehin gelesenen neuesten Equity-Punkten je Depot; Referenz und
  Startpunkte mit je einer Index-Abfrage mit `LIMIT`; `load` filtert alle Listen auf aktive Depots.
- Verworfen: Sichtbarkeit „Equity-Punkt in der neuesten Runde“ (ein Depot mit Bestand in einem Markt ohne frischen
  Kurs verschwände genau dann, wenn etwas nicht stimmt); die Liste der Depots aus der Umgebung des Bots (die
  Weboberfläche kennt sie nicht, ADR-0016); Zeilen löschen oder umbenennen (Historie).

## Plan
- [x] 1. Spec, ADR-0031, INDEX, ROADMAP
- [ ] 2. Schalter und Runde (AK1–AK4)
- [ ] 3. Übersicht und Einstellungen (AK5–AK8)
- [ ] 4. Mutationsgruppe `buyhold` (AK9)
- [ ] 5. Doku ARCHITEKTUR, `docs/betrieb/server.md` (AK10)
- [ ] 6. Mit Zustimmung: Deploy (AK11)

## Risiken & offene Fragen
- **Euro-Depots ohne passende Referenz:** v0.3.1- und GPT-Depots handeln BTC über BTC-EUR, die Referenz über
  BTC-USDC; der Unterschied der Ausführungskosten steckt mit in „vs.“ (Nutzer informiert 2026-11-08).
- **Depots jünger als die Referenz** zeigen „–“, solange es keinen Equity-Punkt zu Beginn der Referenz gibt.
- **M1-Uhr:** der Deploy startet die 7 Tage neu.

---

## Verlauf
- 2026-11-08: Nutzer: „den buy-hold euro kurs kannste raus nehmen... den brauchen wir ja nicht mehr“; auf Rückfrage
  „Ganz abschalten“ (nicht nur ausblenden), informiert über fehlende passende Referenz der Euro-Depots, neues ADR
  und Neustart der M1-Uhr.

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** ADR-0031
