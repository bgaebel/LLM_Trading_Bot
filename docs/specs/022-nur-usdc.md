# Spec 022: Nur noch USDC-Depots – GPT über USDC, Euro-Depots aus und gelöscht

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s022-nur-usdc` · **Meilenstein:** M1 (Nachtrag, Paper)
- **Erstellt:** 2026-11-09

## Ziel
Der Paper-Bot berücksichtigt nur noch die USDC-Paare: Es laufen nur `trend-usdc`, `breakout-usdc`, `meanrev-usdc`,
`ensemble-usdc`, die neuen GPT-Depots `gpt-luna-usdc` und `gpt-sol-usdc` (BTC über BTC-USDC, mit den Kosten des
Wegs) und die Referenz `buyhold-usdc`. Die Euro-Depots laufen nicht mehr, verschwinden nach wenigen Minuten aus der
Übersicht und ihre Historie in `bot.db` lässt sich mit einem Wartungsbefehl einmalig löschen (Nutzer 2026-11-09:
„wir berücksichtigen nur noch USDC-Paare! und alles was vor dem Wechsel auf USDC war kann im grunde auch weg“,
„GPT auf USDC umstellen“, „Wirklich löschen“; ADR-0032).

## Akzeptanzkriterien
- [ ] AK1: **Standard-Depotsatz:** Ohne `BOT_EURO_DEPOTS` laufen nur die klassischen USDC-Depots aus
  `BOT_USDC_AGENTS` (fehlt = alle vier), die GPT-USDC-Depots zu den GPT-Namen in `BOT_AGENTS` (`gpt-luna` →
  `gpt-luna-usdc`, `gpt-sol` → `gpt-sol-usdc`; fehlt `BOT_AGENTS`: beide) und `buyhold-usdc`, sobald eines davon
  läuft. Klassische Euro-Namen in `BOT_AGENTS` werden ignoriert. Auch mit der `secrets.env` des Servers von vor
  Spec 022 (explizites `BOT_AGENTS` mit Euro-Namen, `BOT_USDC_AGENTS` gesetzt oder nicht) startet der Deploy allein
  genau diese Depots. — Nachweis: `tests/llm/test_nur_usdc_config.py::test_default_set_runs_only_usdc_depots`,
  `::test_server_secrets_from_before_022_start_only_usdc_depots`, `::test_gpt_usdc_follow_the_gpt_choice_in_bot_agents`
- [ ] AK2: **Schalter `BOT_EURO_DEPOTS`:** fehlt oder genau `0` = aus, genau `1` = Depotsatz wie vor Spec 022
  (Euro-Depots aus `BOT_AGENTS`, GPT in Euro, keine GPT-USDC-Depots, USDC-Depots wie Spec 020), jeder andere Wert
  ist ein Konfigurationsfehler. Der Schalter gehört in die Umgebung (Drop-in), nicht in `secrets.env`.
  Von Hand gebaute `BotConfig` (Labor, Spec 016) betreibt genau die übergebenen Depots. — Nachweis:
  `tests/llm/test_nur_usdc_config.py::test_euro_switch_is_off_by_default_and_strict`,
  `::test_euro_switch_restores_the_depot_set_of_021`, `::test_config_built_by_hand_runs_exactly_its_depots`
- [ ] AK3: **Fehlerfälle:** GPT gewählt ohne `OPENAI_API_KEY` → Konfigurationsfehler, der die GPT-USDC-Depots nennt;
  nur klassische USDC-Depots brauchen keinen Key; `BOT_AGENTS` leer → Fehler (ein Release vor 022 bräuchte es);
  läuft gar kein Depot → Fehler; `gpt-*-usdc` in `BOT_AGENTS` oder `BOT_USDC_AGENTS` ist unbekannt. — Nachweis:
  `tests/llm/test_nur_usdc_config.py::test_missing_key_refuses_the_gpt_usdc_depots`,
  `::test_classic_usdc_only_needs_no_key`, `::test_unclear_depot_choice_is_refused`
- [ ] AK4: **GPT über USDC:** `gpt-luna-usdc` / `gpt-sol-usdc` haben Modell, Effort, Takt, Preise und Budgetanteil
  von `gpt-luna` / `gpt-sol` (dieselben Umgebungsfelder) und stellen eine Anfrage je Depot für BTC und ETH. Der
  BTC-Kontext trägt die Kosten des Wegs in den vorhandenen Feldern (`taker_fee_per_side_pct` 0,15,
  `slippage_per_side_pct` 0,06, `roundtrip_cost_pct` = `usdc.roundtrip_cost`), das Gewicht ist über den Weg bewertet;
  der BTC-Trade läuft über den Weg (Markt `BTCUSDC`), die Bewertung über BTC-USDC · USDC-EUR; ETH wie bisher.
  Systemprompt, Schema, `PROMPT_VERSION` `llm-v1` und Hash bleiben; die Entscheidungen dieser Depots tragen die
  Version `llm-v1-usdc`. Die Euro-GPT-Depots (mit `BOT_EURO_DEPOTS=1`) bleiben bitgleich. — Nachweis:
  `tests/llm/test_gpt_usdc.py::test_gpt_usdc_payload_carries_the_route_costs`,
  `::test_gpt_usdc_trades_btc_over_the_route_and_eth_on_eth_eur`, `::test_prompt_stays_llm_v1_and_version_marks_usdc`,
  `::test_gpt_usdc_shares_model_and_budget_with_its_euro_twin`
- [ ] AK5: **Fail closed:** Fehlt vor der Anfrage ein Kurs des Wegs, bleibt BTC aus der Anfrage (nur ETH wird
  gefragt); hält das Depot BTC, wird gar nicht gefragt (`NO_QUOTES`, kein Budget). Fehlt er nach der Antwort, wird
  BTC als `STALE_QUOTE` ohne Trade gespeichert, ETH gehandelt. — Nachweis:
  `tests/llm/test_gpt_usdc.py::test_missing_route_quote_keeps_btc_out_of_the_request`,
  `::test_held_btc_without_route_quotes_asks_nothing`, `::test_route_quote_lost_after_the_answer_costs_only_btc`
- [ ] AK6: **Einstellungen:** Die Depotkacheln bieten nur die sechs wählbaren USDC-Depots. Speichern schreibt
  `BOT_USDC_AGENTS` (klassische USDC-Depots, auch leer) und als `BOT_AGENTS` die Euro-Zwillinge aller gewählten
  Depots (nur Namen, die ein Release vor 022 kennt; leere Wahl wird abgewiesen). Die GPT-Budgetfelder gelten für die
  GPT-USDC-Depots. Die Freigabe gestoppter Depots nimmt jedes bekannte Depot an. — Nachweis:
  `tests/web/test_nur_usdc_settings.py::test_settings_offer_only_usdc_depots`,
  `::test_saving_keeps_bot_agents_rollback_safe`, `::test_empty_choice_is_refused`,
  `::test_release_is_accepted_for_every_known_depot`
- [ ] AK7: **Übersicht:** `ACTIVE_SECONDS` = 300 s: ein abgeschaltetes Depot fehlt spätestens 5 min nach seinem
  letzten Equity-Punkt in Depottabelle, Equity-Verlauf, Laufband, Signalen, Trades und GPT-Kosten; ein Kursausfall
  blendet weiter kein Depot aus (Regel aus Spec 021). Referenz bleibt `buyhold-usdc`. — Nachweis:
  `tests/web/test_reference.py::test_a_depot_stays_shown_for_the_active_window_after_its_last_valuation`,
  `tests/web/test_nur_usdc_overview.py::test_switched_off_euro_depots_leave_within_five_minutes`,
  `::test_gpt_costs_list_only_active_depots`, `::test_a_quote_outage_longer_than_the_window_hides_no_depot`
- [ ] AK8: **Löschbefehl `trading-bot purge-euro-depots --db <pfad> [--execute]`:** Ohne `--execute` Probelauf:
  öffnet nur lesend, nennt je Tabelle die Zeilen der sieben Euro-Depots und ändert nichts (Datei bytegleich). Mit
  `--execute`: nur mit WriterLock (Dienst gestoppt), sonst Abbruch; löscht in einer Transaktion genau die Zeilen
  von `trend`, `breakout`, `meanrev`, `ensemble`, `gpt-luna`, `gpt-sol`, `buyhold` aus `trades`, `decisions`,
  `equity`, `llm_calls`, `portfolios`; prüft danach `PRAGMA integrity_check`, `foreign_key_check`, die Schemaprüfung
  und dass alle übrigen Zeilen unverändert sind, sonst Rücknahme; nennt die gelöschten Zahlen. Verweigert bei
  falscher Schemaversion, fehlender Datei oder wenn ein Trade eines anderen Depots auf eine Euro-Entscheidung zeigt.
  Zweiter Lauf löscht 0. — Nachweis: `tests/ops/test_purge.py::test_dry_run_changes_nothing`,
  `::test_execute_removes_exactly_the_euro_rows`, `::test_execute_is_refused_while_the_lock_is_held`,
  `::test_second_run_deletes_nothing`, `::test_wrong_schema_is_refused`,
  `::test_foreign_reference_to_a_euro_decision_is_refused`, `::test_purge_scope_is_exactly_the_euro_depots`,
  `tests/test_cli.py::test_purge_euro_depots_command`
- [ ] AK9: **Bitgleich:** Die klassischen USDC-Depots und `buyhold-usdc` schreiben im selben Szenario dieselben
  Zeilen wie Release 9946852 (eingefrorener Digest) und dieselben Zeilen (ohne `id`) mit den GPT-USDC-Depots daneben;
  Golden-Fixtures v0.3.1 unverändert grün. — Nachweis:
  `tests/paper/test_nur_usdc_golden.py::test_usdc_depots_match_release_9946852`,
  `::test_gpt_usdc_depots_leave_the_other_usdc_depots_unchanged`
- [ ] AK10: **Mutation:** Gruppe `nurusdc` (Standardsatz, Euro-Schalter, Routenkosten der GPT-Depots,
  Löschumfang, Sperre, Probelauf, Aktivfenster) wird vollständig erkannt. — Nachweis:
  `uv run python tools/golden/mutation_check.py nurusdc`
- [ ] AK11: Doku: ADR-0032 (löst ADR-0031 ab), INDEX, ROADMAP, ARCHITEKTUR, PROJEKT, README,
  `docs/betrieb/server.md` (Schalter, Löschablauf). — Nachweis: `scripts/docs-check.sh`
- [ ] AK12: [manuell] Mit Zustimmung: Deploy; im Journal laufen nur die USDC-Depots, `gpt-luna-usdc` und
  `gpt-sol-usdc` fragen GPT; nach 5 min zeigt die Übersicht nur sie. Danach, wieder nur mit Zustimmung: Dienst
  stoppen, Backup, Probelauf, `--execute`, Dienst starten (Ablauf `docs/betrieb/server.md`). Die M1-Uhr startet neu.

## Abgrenzung
- Keine Schemaänderung (`bot.db` bleibt v2), keine Änderung an `observations.db` (ADR-0027).
- Keine Änderung an Strategien, Gebühren, Slippage, Prompt oder Schema der GPT-Antwort; ETH bleibt ETH/EUR.
- Kein automatisches Löschen: der Befehl läuft nur von Hand, nach Backup, mit Zustimmung.
- Kein Echtgeld (ADR-0004).

## Relevante Entscheidungen & Learnings
- ADR-0031 (Euro-Buy-&-Hold aus, Historie bleibt) – wird durch ADR-0032 abgelöst.
- ADR-0030 (Weg über USDC) bleibt; ADR-0029 (Ausführungsvarianten ohne Regeländerung).
- ADR-0007 (v0.3.1 unverändert), ADR-0011/0012/0013 (eine Anfrage für BTC+ETH, Modelle, Takt, Budget),
  ADR-0016/0017 (ein Schreiber, WriterLock), ADR-0020 (Deploy), ADR-0021 (fail closed), ADR-0027 (Beobachtungs-DB).
- LEARNINGS #golden: Gleichheit mit vollständigen Zeilen belegen; #paper: Teilkurse (Spec 011 AK7); #sqlite:
  nur lesend mit `mode=ro`.

## Ansatz
- `llm/agents.py`: `EURO_FIELD` (`BOT_EURO_DEPOTS`, strenger Schalter wie `BOT_BUYHOLD`); `GPT_USDC` ordnet
  `gpt-luna-usdc`/`gpt-sol-usdc` ihren Euro-Zwillingen zu, deren Umgebungsfelder sie lesen. `BotConfig.routed`:
  läuft ein USDC-Depot (klassisch oder GPT), dann `buyhold-usdc` und die Kurse der USDC-Märkte.
- `llm/cycle.py`: je Depot die Kurse der eigenen Märkte (`usdc.route_markets`); für das BTC eines GPT-USDC-Depots
  Rundreisekosten, Gebühr und Slippage des Wegs in Indikatoren, Kontext und Auslöser; nach der Antwort Kurse aller
  gespeicherten Märkte, für Euro-Depots auf BTC-EUR/ETH-EUR gefiltert (bitgleich). Ausführung und Bewertung laufen
  schon über den Namen (`usdc.is_usdc`).
- `ops/purge.py` + `cli.py`: Probelauf über `web.readonly.reading`, Ausführung unter `WriterLock` in einer
  `write_tx`, Vergleich eines Digests aller übrigen Zeilen vor dem Commit.
- `web/credentials.py`, `web/pages.py`, `web/app.py`: Kacheln und Anteile der USDC-Depots; `web/overview.py`:
  `ACTIVE_SECONDS = 300`, GPT-Kosten nur aktiver Depots.
- Verworfen: GPT-USDC in `BOT_USDC_AGENTS` (Rückweg, Server-Auswahl), Löschen beim Start, neue Prompt-Version
  (ADR-0032).

## Plan
- [x] 1. Spec, ADR-0032, INDEX, ROADMAP
- [ ] 2. Depotsatz und Schalter (AK1–AK3)
- [ ] 3. GPT über USDC (AK4, AK5)
- [ ] 4. Einstellungen und Übersicht (AK6, AK7)
- [ ] 5. Löschbefehl und Server-Ablauf (AK8)
- [ ] 6. Bitgleichheit (AK9), Mutationsgruppe `nurusdc` (AK10)
- [ ] 7. Doku ARCHITEKTUR, PROJEKT, README, `docs/betrieb/server.md` (AK11)
- [ ] 8. Mit Zustimmung: Deploy, danach Löschlauf auf dem Server (AK12)

## Risiken & offene Fragen
- **Unumkehrbar:** nach `--execute` ist die Historie der Euro-Depots nur noch im Backup; der Treue-Test (Spec 016)
  verliert die Live-Daten der Euro-Depots (Nutzer informiert 2026-11-09).
- **Rückweg nach dem Löschen:** ein Release vor 022 (oder `BOT_EURO_DEPOTS=1`) legt die Euro-Depots neu mit
  10 000 € an; ihre alte Historie kommt nur mit dem Backup zurück (`trading-bot restore`).
- **GPT-Budget am Deploy-Tag:** die neuen Depots haben eigene Tagesanteile; die Gesamtgrenze von 3 € zählt alle
  Anfragen des Tages, auch die der Euro-GPT-Depots vor dem Deploy.
- **Labor (Spec 016):** baut `BotConfig` von Hand und ist nicht betroffen; nach dem Löschen fehlen dem Treue-Test
  die Euro-Depots in `bot.db`.
- **M1-Uhr:** der Deploy startet die 7 Tage neu.

---

## Verlauf
- 2026-11-09: Nutzer: „ich habe doch gesagt, dass buy-hold (ohne usdc) raus kann! wir berücksichtigen nur noch
  USDC-Paare! und alles was vor dem Wechsel auf USDC war kann im grunde auch weg“; auf Rückfrage „GPT auf USDC
  umstellen“ und „Wirklich löschen“, informiert über den Verlust der Live-Daten für den Treue-Test.
- 2026-11-09: Umsetzung (Workflow, Branch `feat/s022-nur-usdc`). `BOT_EURO_DEPOTS=1` stellt den Depotsatz von vor
  022 her statt Euro-Depots zusätzlich zu starten: so laufen die Euro-GPT-Depots nie neben ihren USDC-Zwillingen
  (doppelte Anteile im selben Gesamtbudget) und die Tests von vor 022 prüfen mit dem Schalter genau das bisherige
  Verhalten. Tests von vor Spec 022, die Euro-Depots über `load_config` betreiben, setzen `BOT_EURO_DEPOTS=1`
  (Harness-Standard); eingefrorene Fixtures unverändert. Geändert wurde die Erwartung „ein Depot bleibt eine Stunde
  sichtbar“ (`tests/web/test_reference.py`) zu „bleibt `ACTIVE_SECONDS` = 300 s sichtbar“, wie AK7 verlangt.

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** ADR-0032
