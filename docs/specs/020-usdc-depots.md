# Spec 020: USDC-Depots – BTC über BTC/USDC ausführen, Geld bleibt in Euro

- **Status:** fertig
- **Issue:** – · **Branch:** `feat/s020-usdc-depots` · **Meilenstein:** M1 (Nachtrag, Paper)
- **Erstellt:** 2026-11-08

## Ziel
BTC/USDC ist das günstigste BTC-Paar auf Bitvavo (Gebühr 0,05 % statt 0,25 % je Seite, Recherche 2026-11-08). Neue
Paper-Depots handeln BTC über BTC/USDC und rechnen die geringeren Kosten in die Bewertung jedes Trades ein. Das Geld
bleibt in Euro: Jeder BTC-Kauf tauscht Euro → USDC → BTC, jeder Verkauf zurück (Nutzer 2026-11-08). Die bestehenden
Depots laufen unverändert weiter – so zeigt der Vergleich, was die günstigere Ausführung bringt.

## Akzeptanzkriterien
- [x] AK1: **Kurse:** Dieselbe Abfrage `/ticker/book` liefert zusätzlich BTC-USDC und USDC-EUR (kein weiterer
  Request); gleiche Prüfungen und Fehlercodes wie heute je Markt; gespeichert in `market` und aufgezeichnet in der
  Beobachtungs-DB (Spec 015). Kerzen bleiben BTC-EUR und ETH-EUR. — Nachweis: `tests/market/test_usdc_quotes.py`
- [x] AK2: **Ausführung über USDC:** Kauf von BTC für E € = E → USDC zum USDC-EUR-Brief (+ 0,01 % Slippage, 0,10 %
  Gebühr) → BTC zum BTC-USDC-Brief (+ 0,05 % Slippage, 0,05 % Gebühr); Verkauf umgekehrt zum Geld. Gebühren beider Schritte in € im
  Trade gespeichert, Markt `BTC-USDC`; Mindestorder und Grenzen wie heute in €. ETH bleibt ETH-EUR mit heutigen
  Kosten. — Nachweis: `tests/paper/test_usdc_execution.py` (Handrechnung je Schritt)
- [x] AK3: **Kosten in der Bewertung jedes Trades:** Die Strategien der USDC-Depots rechnen mit den Kosten ihres
  Wegs: `2 · (0,05 % + 0,05 % + 0,10 % + 0,01 %) = 0,42 %` plus die Spreads beider Bücher statt
  `2 · (0,25 % + 0,05 %) = 0,60 %` plus Spread. Regeln sonst identisch zu v0.3.1, Version `v0.3.1-usdc` in jeder Entscheidung. — Nachweis:
  `tests/strategy/test_usdc_costs.py`
- [x] AK4: **Depots:** `trend-usdc`, `breakout-usdc`, `meanrev-usdc`, `ensemble-usdc` und `buyhold-usdc` mit je
  10.000 € Start; in den Einstellungen wählbar, standardmäßig an. Die v0.3.1-Depots und ihre Historie bleiben
  byte-gleich (bestehende Golden-Tests unverändert grün). — Nachweis: `tests/paper/test_usdc_depots.py`,
  Golden-Tests v0.3.1. *Umsetzung „standardmäßig an“:* eigenes Feld `BOT_USDC_AGENTS` – fehlt es, laufen alle vier
  (auch neben dem ausdrücklichen `BOT_AGENTS` auf dem Server, also gleich nach dem Deploy); leer = keins, abgewählt
  wird in den Einstellungen. Nie in `BOT_AGENTS`, damit ein Rollback auf ein Release vor 020 mit derselben
  `secrets.env` startet. — Nachweis: `tests/paper/test_usdc_depots.py::test_usdc_depots_are_on_by_default_also_beside_an_explicit_depot_list`,
  `::test_usdc_choice_never_reaches_bot_agents_so_a_rollback_still_starts`.
- [x] AK5: **Bewertung und Risiko in Euro:** BTC eines USDC-Depots = Menge · BTC-USDC-Mitte · USDC-EUR-Mitte;
  Drawdown-Stopp 10 % wie ADR-0021. Fehlt oder veraltet ein nötiger Kurs (BTC-USDC oder USDC-EUR), entscheidet und
  bewertet das Depot nicht (fail closed); andere Depots sind nicht betroffen. — Nachweis:
  `tests/paper/test_usdc_depots.py::test_missing_usdc_quote_blocks_only_usdc_depots`
- [x] AK6: **Gleichheit und Mutation:** Mutationsgruppe `usdc` (Gebühr vertauscht, Umrechnung vergessen, Brief/Geld
  vertauscht, Kosten der Signale wie EUR) wird vollständig erkannt. — Nachweis:
  `uv run python tools/golden/mutation_check.py usdc`
- [x] AK7: **Weboberfläche:** USDC-Depots mit Kennzeichen „USDC“, Trades mit Markt BTC-USDC, Kurse im Laufband;
  öffentliche Ansicht wie ADR-0028. — Nachweis: `tests/web/test_usdc_overview.py`
- [ ] AK8 (nach Spec 016 verschoben): **Labor:** Spec 016 bekommt die Variante `usdc` (USDC-Kostenweg auf BTC-EUR-Kursen, da BTC-USDC erst
  ab 2024-07-01 existiert); im Bericht als „Ausführung über USDC“. — Nachweis: `tests/lab/test_backtest.py::test_usdc_variant`
- [x] AK9: Doku: ADR-0029 (löst ADR-0026 ab: Ausführungsvarianten ohne Regeländerung brauchen keine Hürden),
  ADR-0030 (USDC-Depots), PROJEKT (Märkte), ARCHITEKTUR, `docs/betrieb/server.md`. — Nachweis: `scripts/docs-check.sh`
- [x] AK10: [manuell] Mit Zustimmung: Deploy; die neuen Depots erscheinen, Buy & Hold USDC kauft einmal, Kurse
  BTC-USDC/USDC-EUR frisch in der Übersicht.

## Abgrenzung
- Keine GPT-Depots über USDC (Prompt nennt EUR-Märkte; eigene Spec mit neuer Prompt-Version, falls gewünscht).
- ETH bleibt ETH-EUR (ETH-USDC zu dünn, Nutzer nannte nur BTC). Kein Bargeld in USDC (kein Wechselkursrisiko).
- Keine Änderung an den v0.3.1-Depots, ihrer Historie oder dem Schema `bot.db` v2 (Bargeld bleibt Euro, BTC-Menge in
  der vorhandenen Spalte).
- Kein Echtgeld (ADR-0004).

## Relevante Entscheidungen & Learnings
- ADR-0007 (Strategien identisch zu v0.3.1, Änderung nur als neue Version), ADR-0026 (Hürden für neue Versionen –
  wird für reine Ausführungsvarianten durch ADR-0029 abgelöst), ADR-0021 (Kurse je Markt, fail closed), ADR-0016
  (ein Schreiber), ADR-0027 (Beobachtungs-DB), ADR-0028 (öffentliche Ansicht).
- CLAUDE.md: Gebühren nur mit Tests und neuer Strategieversion; Historie nie still migrieren.
- LEARNINGS #golden (Grenzfälle), #bitvavo (Lücken in dünnen Märkten).

## Ansatz
- Recherche 2026-11-08: BTC-USDC 0,05 %/0,05 % (Kategorie B), USDC-EUR 0,10 % (Kategorie C), BTC-EUR 0,25 % taker;
  BTC-USDC ~3 % des Volumens von BTC-EUR, 83 % der Minuten ohne Handel, Kerzen erst ab 2024-07-01 → Signale aus
  BTC-EUR-Kerzen, Ausführung und Bewertung über die Bücher von BTC-USDC und USDC-EUR (immer aktuell, auch ohne Handel).
- Kostenvergleich je Kauf + Verkauf (≈ 5.000 €, gemessen): BTC-EUR ≈ 0,50 %; USDC-Weg mit Euro-Umrechnung ≈ 0,37 %.
  Modell (vorsichtig wie die Arena): BTC-EUR 0,60 %, USDC-Weg 0,42 % plus Spreads. Slippage 0,05 % auf BTC-USDC wie
  heute auf BTC-EUR; 0,01 % auf USDC-EUR (Stablecoin-Paar, gemessen 0,006 % für 5.000 €).
- Module: `market/models.py` (Kursmärkte), `market/bitvavo.py` (Buch), `paper/execution.py` (Ausführungsweg je Depot),
  `paper/cycle.py` (Kosten je Depot), `strategy/payload.py` (Kostenweg), `llm/agents.py` (Depotliste),
  `web/overview*.py`, `lab/backtest.py`.
- Verworfen: bestehende Depots umstellen (mischt Historie, bricht v0.3.1); Signale aus BTC-USDC-Kerzen (fast immer
  fail closed); Bargeld in USDC (Wechselkursrisiko kann den Drawdown-Stopp auslösen).

## Plan
- [x] 1. ADRs und Spec freigeben, PROJEKT anpassen
- [x] 2. Kurse BTC-USDC/USDC-EUR aus `/ticker/book` (AK1)
- [x] 3. Ausführungsweg und Kosten je Depot, Golden der v0.3.1 unverändert (AK2, AK3, AK6)
- [x] 4. Depots, Bewertung, Risiko, fail closed (AK4, AK5)
- [x] 5. Weboberfläche (AK7)
- [ ] 6. Laborvariante (AK8) – nach Spec 016 verschoben (das Labor liegt dort)
- [x] 7. Doku, Prüfung (Workflow), PR (AK9)
- [x] 8. Mit Zustimmung: Deploy (AK10)

## Risiken & offene Fragen
- **Gebührenwährung** auf BTC-USDC (vermutlich USDC) nicht bestätigt – im Paper-Modell in € umgerechnet.
- **Dünnes Buch:** bei 10.000-€-Depots und 45 % Ziel ≈ 4.500 € je Trade; Buchtiefe ±0,1 % ≈ 75 k € – unkritisch,
  Slippage bleibt 0,05 % je Schritt (vorsichtig).
- **M1-Uhr:** der Deploy startet die 7 Tage neu.

---

## Verlauf
- 2026-11-08: Nutzer: BTC/USDC nutzen und dessen Gebühren berücksichtigen; Recherche (Workflow, 4 Agenten, Gebühren
  gegen Bitvavo-Seiten geprüft); Nutzer wählt neue USDC-Depots und Euro mit Umrechnung je Trade.
- 2026-11-08: Umsetzung (Workflow, Branch `feat/s020-usdc-depots-impl`): Kurse, Weg in `paper/usdc.py`,
  Depots, Weboberfläche, Doku. Mutationsgruppe `usdc` (`mutation_check.py usdc`): alle erkannt außer zwei
  dokumentierten äquivalenten – „Gebühren der Schritte vertauscht“ (beide Gebühren liegen auf demselben
  Euro-Betrag, nur die Summe ist beobachtbar; jede falsche Einzelgebühr wird erkannt) und „Buy & Hold USDC kauft
  nach Neustart nur einmal“ (die Entscheidung mit Bar −1 ist eindeutig). Muster der Mutationen dürfen in der
  Gruppe nur einmal vorkommen (ein Docstring hatte drei Mutanten verschluckt). AK8 als Folgearbeit zurückgestellt.
- 2026-11-08: Prüfung (Workflow): Die erste Umsetzung machte die Depots opt-in (Ankreuzen nach dem Deploy) und
  schrieb sie in `BOT_AGENTS` – gegen AK4 „standardmäßig an“, AK10 wäre nach dem Deploy ausgeblieben, und ein
  Rollback auf ein Release vor 020 hätte `BOT_AGENTS` als unbekannt abgelehnt und alle Depots gestoppt. Jetzt:
  eigenes Feld `BOT_USDC_AGENTS` (fehlt = alle an, leer = keins), AK4-Text wieder wie freigegeben. Außerdem zeigt
  die Übersicht BTC-USDC/USDC-EUR nach dem Abschalten nicht mehr als stehende Kurse: nur solange ein USDC-Depot in
  der neuesten Runde bewertet wurde oder der Kurs jünger als 1 h ist (`tests/web/test_usdc_overview.py`).

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** Fünf USDC-Depots (`v0.3.1-usdc`) handeln BTC über BTC/USDC mit Geld in Euro und Umrechnung je
  Trade; Signale aus BTC/EUR; die v0.3.1-, GPT- und Buy-&-Hold-Depots sind unverändert. Live seit 2026-11-08
  16:56 UTC (Release a44ef78). AK1–AK7, AK9, AK10 erfüllt.
- **Abweichungen vom Plan:** Auswahl der USDC-Depots im eigenen Feld `BOT_USDC_AGENTS` (fehlt = alle an), damit ein
  Rollback auf einen älteren Release weiter startet; USDC-Kurse erscheinen in Lebenszeichen und Laufband nur, solange
  USDC-Depots sie nutzen. AK8 (Laborvariante) nach Spec 016 verschoben, weil das Labor dort liegt.
- **Nachweise:** PR #24 (CI grün, auch auf `main`); lokal 2154 Tests, Mutationsprüfung 280/280 (Gruppe `usdc` 72
  Mutanten), Golden-Fixtures v0.3.1 unverändert. Auf dem Server nach dem Deploy (nur lesend, 16:59 UTC): 12 Depots,
  davon 5 USDC; Kurse BTCUSDC und USDCEUR in derselben Runde wie BTCEUR/ETHEUR gespeichert; `buyhold-usdc` kaufte
  einmal (BTC über BTC-USDC, ETH über ETH-EUR); jedes USDC-Depot mit Entscheidung und Equity-Punkten; Dienst
  `degraded` nur wegen fehlender ETH-EUR-Minutenkerzen bei Bitvavo (alle Depots gleich, fail closed).
- **Offen / Folgearbeit:** Laborvariante `usdc` in Spec 016; Gebührenwährung auf BTC/USDC (vermutlich USDC) erst
  mit Echtgeld (M2) bestätigbar.
- **Neue ADRs / Learnings:** ADR-0029, ADR-0030.
