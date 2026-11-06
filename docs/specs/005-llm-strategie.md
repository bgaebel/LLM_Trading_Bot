# Spec 005: LLM-Strategie (GPT) mit Modellvergleich, Budgetsperre und Provenienz

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s005-llm-strategie` · **Meilenstein:** M1 (Nr. 3a)
- **Erstellt:** 2026-11-06

## Ziel
Der Bot führt zwei GPT-Depots nebeneinander – `gpt-luna` (`gpt-6-luna`, Haupt-Trader) und `gpt-sol`
(`gpt-6.1-sol`, Gegenprobe) – plus Buy & Hold. Beide bekommen im selben Zyklus denselben Zustand beider Märkte in
**einer** Anfrage, antworten je Markt mit einem Signal und handeln virtuell über die Paper-Ausführung (Spec 004).
Gefragt wird nur bei Bedarf: stündlich, bei starker Bewegung oder wenn eine klassische Strategie ihr Signal
wechselt, höchstens viermal pro Stunde. Kosten sind hart begrenzt (3 € pro UTC-Tag gesamt, mit Anteilen je Depot); jede Entscheidung ist mit
Eingaben, Modell, Token-Nutzung und Kosten gespeichert. Ungültige Antworten, Fehler oder veraltete Kurse führen
nie zu einem Trade. So lässt sich messen, ob das teurere Reasoning sein Geld wert ist.

## Akzeptanzkriterien
- [ ] AK1: Prompt v1 (`llm-v1`) ist versioniert: Systemprompt und Antwortschema sind festgeschrieben (SHA-256 im
  Test). Die Benutzer-Nachricht ist kompaktes JSON `{"markets": {"BTC-EUR": …, "ETH-EUR": …}}`; der Inhalt je
  Markt ist der Arena-Kontext `feature_payload` (Spec 003, inkl. Referenzsignale der klassischen Strategien,
  Depotgewicht, Kosten). Ein Markt ohne gültigen Snapshot fehlt in Anfrage und Schema. — Nachweis:
  `uv run pytest tests/llm/test_contract.py`
- [ ] AK2: Strenge Prüfung je Markt nach Arena-Regeln: genau die Felder `action`, `target_fraction` ∈ {0, 0.25,
  0.45}, `confidence` ∈ [0, 1], `market_regime` ∈ {UPTREND, DOWNTREND, SIDEWAYS}, `reason` (nicht leer); BUY nur
  0.25/0.45, SELL nur 0; genau die angefragten Märkte. Ist ein Teil ungültig, wird die ganze Antwort verworfen.
  `incomplete`, Ablehnung (`refusal`), fehlender Text oder ungültiges JSON → Versuch `ERROR`, keine Entscheidung.
  — Nachweis: `uv run pytest tests/llm/test_response.py` (Grenzfälle an jeder Regel)
- [ ] AK3: Takt je Depot (ADR-0013): regulär alle 60 min (`LLM_MIN_INTERVAL_SECONDS`, min. 900); Ereignis
  frühestens 10 min nach dem letzten Versuch (`LLM_EVENT_COOLDOWN_SECONDS`, min. 300), wenn sich ein Markt seit dem
  letzten Versuch um ≥ 1,8 % bewegt hat (`LLM_EVENT_MOVE_THRESHOLD`, min. 0,5 %) oder ein Referenzsignal
  (`trend`/`breakout`/`meanrev`) eines Marktes gewechselt hat; höchstens 4 Versuche je rollierender Stunde
  (`LLM_MAX_CALLS_PER_HOUR`). — Nachweis: `uv run pytest tests/llm/test_due.py` (Grenzfälle an jeder Schwelle)
- [ ] AK4: Budgetsperre (ADR-0013): Vor jedem Aufruf wird eine Reservierung committet = schlimmster Fall aus den
  Preisen (Eingabezeichen als Tokens plus `max_output_tokens`). Nicht gesendet wird (`BUDGET_BLOCKED`), wenn danach
  die Tagesgrenze des Depots (ADR-0012: `gpt-luna` 0,50 €, `gpt-sol` 2,50 €) oder die Gesamtgrenze
  (`LLM_DAILY_BUDGET_EUR`, 3 €) überschritten wäre. Unbekannte Kosten zählen mit ihrer Reservierung; Kosten über
  der Reservierung sperren das Depot für den Rest des Tages; ein Modell ohne Preise wird nie aufgerufen.
  Rechnen in `Decimal`. — Nachweis: `uv run pytest tests/llm/test_budget.py`
- [ ] AK5: Provenienz: Jeder Versuch hat eine Zeile in `llm_calls` (Zeit, Depot, Märkte, Referenzkurse, Modell,
  Prompt-Version und -SHA-256, `max_output_tokens`, Effort, Kontext, Antwort-ID, Input-/Output-/Reasoning-/
  Cache-Tokens, Kosten, Reservierung, Dauer, Status, Fehlertyp). Jede Entscheidung verweist auf ihren Versuch und
  speichert `market_regime`. Der API-Key steht nie in DB, Ausgabe oder Fehlermeldung. — Nachweis:
  `uv run pytest tests/llm/test_provenance.py` (sucht den Test-Key in allen Zeilen und der CLI-Ausgabe)
- [ ] AK6: Fail closed wie Arena-`llm_cycle`: kein Aufruf ohne frische Quotes beider Märkte oder mit einem
  Snapshot älter als 120 s. Nach der Antwort je Markt: Quotes nicht frisch (> 20 s) oder Mittelkurs > 2 % vom
  Snapshot entfernt → Entscheidung `STALE_QUOTE`, kein Trade. — Nachweis: `uv run pytest tests/llm/test_llm_cycle.py`
- [ ] AK7: Isolation: Fehler, Timeout oder Budgetsperre eines Depots blockieren weder die anderen Depots noch
  Buy & Hold; fehlt der Snapshot eines Marktes, wird nur der andere angefragt. Ein Aufruf dauert höchstens 60 s
  (SDK-Timeout) mit höchstens 2 Wiederholungen bei 429/5xx. — Nachweis: `uv run pytest tests/llm/test_llm_cycle.py`
- [ ] AK8: Schema v2 (`PRAGMA user_version = 2`) mit Tabelle `llm_calls`; eine v1-DB wird mit klarer Meldung
  abgelehnt, nicht migriert (ADR-0012). — Nachweis: `uv run pytest tests/paper/test_db.py`
- [ ] AK9: Konfiguration: `BOT_AGENTS` (Standard `gpt-luna,gpt-sol`; klassische Agenten wählbar) ersetzt
  `BOT_AGENT`. Ein GPT-Depot ohne `OPENAI_API_KEY` ist ein Konfigurationsfehler ohne DB-Änderung. `LLM_EFFORT`
  Standard `low`. Ein abweichendes Modell (`OPENAI_MODEL_<DEPOT>`) braucht eigene Preise. — Nachweis:
  `uv run pytest tests/llm/test_config.py`
- [ ] AK10: [manuell] Mit echtem Key zweimal `uv run trading-bot paper-cycle --db <pfad>`: Lauf 1 fragt jedes Depot
  einmal und zeigt je Depot Signale, Tokens (inkl. Reasoning) und Kosten; Lauf 2 kurz danach fragt nicht.
  Gemessene Tokens und Kosten je Modell kommen in den „Verlauf" und prüfen die Schätzung.
- [ ] AK11: Lint, Format, `mypy src tests tools`, CI grün; kein Key im Repo (`git grep -n "sk-"` ohne Treffer
  außer Test-Platzhaltern). — Nachweis: Befehle aus `CLAUDE.md`, CI im PR

## Abgrenzung
- Nicht Teil dieser Spec: Claude/Anthropic (vom Nutzer verworfen), `gpt-6-astra`, Worker-Threads, Kursüberwachung
  alle 5 s/WebSocket und Dauerbetrieb (Nr. 5), Stop-Loss/Drawdown-Stopp (Nr. 4), Deployment und Key auf dem Server
  (Nr. 6), Dashboard und Auswertung des Modellvergleichs (Nr. 7), Batch-/Flex-Tarife.
- Keine Prompt-Optimierung: Prompt v1 bleibt fest; jede Änderung ist eine neue Version mit neuem Hash.

## Relevante Entscheidungen & Learnings
- ADR-0004: Das LLM ist nur Ratgeber; die Ausführung prüft jeden Trade (Positionsgrenzen, Kosten, frische Kurse);
  Orders bleiben virtuell, `live` gesperrt.
- ADR-0007: klassische Strategien, LLM-Kontext je Markt und Ausführung bleiben identisch zur Arena; der LLM-Prompt
  ist eine eigene Version (`llm-v1`).
- ADR-0012 (löst ADR-0010 und damit ADR-0008 ab): `gpt-luna` Haupt-Trader, `gpt-sol` Gegenprobe, + Buy & Hold;
  Anteile 0,50 / 2,50 € von 3 €; Schema versioniert, keine Migration.
- ADR-0013 (löst ADR-0011 und damit ADR-0009 ab): gemeinsame Anfrage, Prompt v1, Abfragen nur bei Bedarf
  (stündlich, Bewegung ≥ 1,8 % oder Signalwechsel, max. 4/h), Effort `low`, `openai`-SDK, Budget je Depot und
  gesamt in der DB, Provenienz.
- ADR-0002: Fremdpaket erlaubt, wenn per `uv add` gelockt.
- CLAUDE.md: Fail closed bei ungültigen LLM-Antworten; BTC-Fehler blockiert ETH nicht; Secrets nie loggen.
- LEARNINGS: „Golden-Fixtures beweisen Gleichheit nur mit Grenzfällen" (Grenzfälle je Regel und Schwelle);
  #windows #encoding (Ersetzungen mit Nicht-ASCII per Edit); Lock-Freigabe unter Windows verzögert.

## Ansatz
Paket `src/llm_trading_bot/llm/`:
- `contract.py`: `PROMPT_VERSION = "llm-v1"`, `SYSTEM`, `response_schema(markets)`, `user_message(payloads)`,
  `parse_answer(text, markets) -> dict[Symbol, LlmSignal]` (Arena-Regeln je Markt + `market_regime`).
- `due.py`: `Cadence` (Umgebung, Untergrenzen), `llm_due(con, agent, mids, signals, now)` (Intervall, Bewegung,
  Wechsel der Referenzsignale gegenüber dem Kontext des letzten Versuchs), Versuche je Stunde.
- `budget.py` (vorhanden, erweitern): Grenze je Depot und gesamt, Reservierung aus Preisen, `settle`.
- `agents.py`: Depot → Modell, Preise, Tagesanteil; Konfiguration aus der Umgebung.
- `openai_client.py`: `SignalModel`-Protocol, `OpenAIResponses` (Responses API, `text.format` json_schema strict,
  `reasoning.effort`, `max_output_tokens` 8000, Timeout 60 s, `max_retries` 2); liefert Text, Antwort-ID, Status,
  Ablehnung, `usage`. Tests nutzen ein Fake, ein `net`-Test den echten Dienst.
- `cycle.py`: `llm_cycle` je Depot: fällig? → Stundenlimit → Reservierung → Aufruf → Abrechnung → Prüfung →
  Quote-Recheck je Markt → `record_decision` je Markt (Schlüssel `int(now)*10000 + attempt` wie Arena).
- `paper/db.py`: `llm_calls` je Anfrage (Märkte, Referenzkurse BTC/ETH, Prompt-Version); `paper/cycle.py`/CLI:
  mehrere Depots, Buy & Hold, Equity je Depot.

Kosten (gemessen: ~2.900 Zeichen Kontext für beide Märkte ≈ 1.500 Eingabe-Token; Ausgabe ~400 sichtbar +
~300 Reasoning bei `low`, geschätzt, in AK10 nachgemessen): ~0,01 $ je Anfrage bei sol, ~0,0005 $ bei luna. Bei
erwarteten ~30–50 Anfragen pro Tag und Depot (24 regulär + Ereignisse; Obergrenze 96): sol ~0,30–0,50 $ (max.
~0,96 $), luna ~0,02 $ (max. ~0,05 $). Schlimmster Fall je Anfrage (8.000 Ausgabe-Token): sol ~0,089 $, luna
~0,004 $.

Verworfen: Arena-Prompt je Markt und Arena-Takt, 4/h + Ereignisse (Nutzer: so wenig wie möglich, ADR-0013),
Antwort ohne `target_fraction`, Teilannahme halb gültiger Antworten, feste Reservierung, `gpt-terra` und
Claude Haiku 5.5 (gleicher Preis wie luna; ADR-0012), Raspberry Pi (ADR-0005 bleibt, Nutzer).

## Plan
- [x] 1. Schema v2 mit `llm_calls`, v1 abgelehnt (AK8) – Spalten werden in Schritt 3 angepasst
- [x] 2. Budget-Grundlage: Reservierung und Abrechnung in der DB – wird in Schritt 3 erweitert
- [x] 3. Prompt v1, Schema und Prüfung je Markt, Takt je Depot; Arena-Vertrag je Markt entfernen; `llm_calls` und
  Budget auf Depots, Gesamtgrenze und berechnete Reservierung umstellen (AK1–AK4, AK8)
- [x] 4. `uv add openai`, `openai_client.py` mit Fake-Tests und `net`-Test (AK2, AK5, AK7)
- [ ] 5. `agents.py`, `llm_cycle`, mehrere Depots in `paper-cycle`, Konfiguration (AK5–AK7, AK9)
- [ ] 6. Mutationstest erweitern, Doku (ARCHITEKTUR, README, `docs/betrieb/`), manueller Live-Test (AK10)

## Risiken & offene Fragen
- Reasoning-Tokens bei `low` sind geschätzt; AK10 misst nach. Weicht es stark ab, Anteile neu festlegen.
- Ob strikte Structured Outputs die Zahlen-Enum `[0, 0.25, 0.45]` und Schlüssel mit Bindestrich (`BTC-EUR`)
  annehmen, sagt die OpenAI-Doku nicht ausdrücklich. Prüfung im `net`-Test/AK10; falls abgelehnt: neue Prompt-
  Version mit neuem ADR statt stiller Änderung.
- Wenige Wochen Paper-Daten reichen nicht für einen belastbaren Modellvergleich; die Spec liefert die Messung,
  nicht das Urteil.
- Zwei Anfragen nacheinander verlängern einen Zyklus (je bis zu 60 s); bis Nr. 5 läuft alles in einem Prozess.
- Wie oft die Referenzsignale wechseln, ist geschätzt; feuert der Auslöser deutlich öfter, begrenzt ihn das
  Stundenlimit, und ADR-0013 nennt die Neubewertung.
- EUR/USD: Preise sind Schätzwerte (USD 1:1 als EUR, eher zu hoch). Zusätzlich Limit im OpenAI-Konto setzen.
- Für AK10 braucht es einen OpenAI-Key lokal (nur in der Shell-Umgebung, nie in Dateien im Repo).

---

## Verlauf
- 2026-11-06: Erste Fassung (Arena-Prompt je Markt, Arena-Takt, ein Agent `gpt`, ADR-0009) freigegeben; Schritte
  „Arena-Vertrag + Takt" (381f617), „Schema v2" (f4e4204) und „Budget" (98a8b34) umgesetzt.
- 2026-11-06: Der Nutzer hat danach umgesteuert: gemeinsame Anfrage mit eigenem Format, Takt 4/h + ≤ 4 Ereignis/h,
  Effort `low`, Vergleichsdepots `gpt-terra` und `gpt-luna` jetzt, Hetzner bleibt. Neue ADR-0010/0011
  (vorgeschlagen), Spec überarbeitet. Der Arena-Vertrag je Markt (Prompt, Antwort-Golden, Takt-Golden) entfällt;
  übernommen werden seine Prüfregeln je Markt. Schema v2 und Budget bleiben und werden erweitert.
- 2026-11-06: Unterwegs gefunden: Der Lock-Test aus Spec 004 war unter Windows flaky (Sperre eines beendeten
  Prozesses wird verzögert frei); Test wartet jetzt bis 10 s, Learning angelegt, Fix auch auf `main` (f6021d4).
- 2026-11-06: Spec freigegeben (ADR-0010/0011 angenommen); Schritte 3 (Prompt v1, Prüfung, Takt, Budget je Depot)
  und 4 (OpenAI-Client) umgesetzt.
- 2026-11-06: Nutzer stellt auf `gpt-6-luna` als Haupt-Trader um, `gpt-6.1-sol` als Gegenprobe, `gpt-terra`
  entfällt (ADR-0012). Claude Haiku 5.5 geprüft: gleicher Listenpreis wie luna, daher kein Wechsel. Budgetanteile
  ohne Nutzerangabe auf 0,50 / 2,50 € von 3 € gesetzt (Gesamtgrenze unverändert).
- 2026-11-06: Nutzer: Abfragen „so gering wie möglich, so viel wie nötig". Takt auf stündlich + Ereignis
  (≥ 1,8 % oder Wechsel eines Referenzsignals, frühestens nach 10 min), max. 4/h umgestellt (ADR-0013).

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
