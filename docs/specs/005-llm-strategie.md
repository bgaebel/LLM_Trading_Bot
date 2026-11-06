# Spec 005: LLM-Strategie (GPT) mit Budgetsperre und Provenienz

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s005-llm-strategie` · **Meilenstein:** M1 (Nr. 3a)
- **Erstellt:** 2026-11-06

## Ziel
Der Bot kann als aktiven Agenten `gpt` fahren: Er fragt `gpt-6.1-sol` über die OpenAI-API im Arena-Takt nach
einem Signal und setzt es über die Paper-Ausführung (Spec 004) um. Prompt, Schema, Kontext und Takt sind identisch
zur Arena v0.3.1. Kosten sind hart auf 3 € pro UTC-Tag begrenzt, und jede Entscheidung ist mit ihren Eingaben,
dem Modell, der Token-Nutzung und den Kosten gespeichert. Ungültige Antworten, Fehler oder veraltete Kurse führen
nie zu einem Trade.

## Akzeptanzkriterien
- [ ] AK1: `SYSTEM`-Prompt, `SIGNAL_SCHEMA` und die gesendete Benutzer-Nachricht (kompaktes JSON von
  `feature_payload`) sind zeichengenau gleich zur Arena@7563724. — Nachweis:
  `uv run pytest tests/llm/test_golden_contract.py` (Erwartung vom Generator aus dem Arena-Code)
- [ ] AK2: Die Antwort wird streng geprüft wie in der Arena (`strict_response` + `validate_signal`): genau die
  vier Felder, `action` ∈ BUY/SELL/HOLD, `target_fraction` ∈ {0, 0.25, 0.45}, BUY nur mit 0.25/0.45, SELL nur mit 0,
  `confidence` in [0, 1], `reason` nicht leer. Status `incomplete`, eine Ablehnung (`refusal`), fehlender Text oder
  ungültiges JSON ergeben einen Versuch mit Status `ERROR` und keine Entscheidung. — Nachweis:
  `uv run pytest tests/llm/test_response.py`
- [ ] AK3: Takt identisch zu Arena-`llm_due`: je Markt frühestens alle 30 min (`LLM_MIN_INTERVAL_SECONDS`, min.
  600), zusätzlich nach ≥ 1,8 % Bewegung seit dem letzten Versuch (min. 0,5 %), frühestens nach 10 min (min. 300);
  höchstens 60 Versuche pro UTC-Tag (`MAX_DAILY_LLM_CALLS`). — Nachweis: `uv run pytest tests/llm/test_due.py`
  (Grenzfälle an jeder Schwelle, Erwartung aus der Arena)
- [ ] AK4: Budgetsperre (ADR-0009): Vor jedem Aufruf wird eine Reservierung (0,10 €) committet. Ist
  „heutige Kosten + Reservierungen + neue Reservierung" > `LLM_DAILY_BUDGET_EUR` (3 €), wird nicht gesendet
  (`BUDGET_BLOCKED`). Unbekannte Kosten (Absturz, Timeout) zählen mit ihrer Reservierung; Kosten über der
  Reservierung sperren den Rest des Tages; fehlendes Budget oder fehlende Preise sperren ganz. Rechnen in `Decimal`.
  — Nachweis: `uv run pytest tests/llm/test_budget.py`
- [ ] AK5: Provenienz: Jeder Versuch hat eine Zeile in `llm_calls` (Zeit, Markt, Modell, `max_output_tokens`,
  Effort, Prompt-SHA-256, Kontext, Antwort-ID, Input-/Output-/Reasoning-Tokens, geschätzte Kosten, Reservierung,
  Dauer, Status, Fehlertyp). Die Entscheidung verweist auf ihren Versuch. Der API-Key steht nie in DB, Ausgabe
  oder Fehlermeldung. — Nachweis: `uv run pytest tests/llm/test_provenance.py` (sucht den Test-Key in allen
  Zeilen und der CLI-Ausgabe)
- [ ] AK6: Fail closed wie Arena-`llm_cycle`: Kein Aufruf ohne frische Quotes beider Märkte oder mit einem
  Snapshot älter als 120 s. Nach der Antwort: Quotes nicht frisch (> 20 s) oder Mittelkurs > 2 % vom Snapshot
  entfernt → Entscheidung `STALE_QUOTE`, kein Trade. — Nachweis: `uv run pytest tests/llm/test_llm_cycle.py`
- [ ] AK7: Ein Fehler oder Timeout bei BTC blockiert ETH nicht; ein Aufruf dauert höchstens 60 s pro Versuch
  (SDK-Timeout), mit höchstens 2 Wiederholungen bei 429/5xx. — Nachweis: `uv run pytest tests/llm/test_llm_cycle.py`
- [ ] AK8: Schema v2 (`PRAGMA user_version = 2`) mit Tabelle `llm_calls`; eine v1-DB wird mit klarer Meldung
  abgelehnt, nicht migriert (ADR-0008). — Nachweis: `uv run pytest tests/paper/test_db.py`
- [ ] AK9: `BOT_AGENT=gpt` ohne `OPENAI_API_KEY` ist ein Konfigurationsfehler (keine DB-Änderung).
  `OPENAI_MODEL` Standard `gpt-6.1-sol`; ein anderes Modell ohne gesetzte Preise wird nicht aufgerufen.
  Standard-Agent bleibt `trend`, bis das Deployment `gpt` setzt. — Nachweis: `uv run pytest tests/llm/test_config.py`
- [ ] AK10: [manuell] Mit echtem Key zweimal `BOT_AGENT=gpt uv run trading-bot paper-cycle --db <pfad>`: Lauf 1
  zeigt je Markt Signal, Tokens (inkl. Reasoning) und Kosten; Lauf 2 innerhalb von 30 min ruft nicht auf. Die
  gemessenen Reasoning-Tokens kommen in den „Verlauf" und prüfen die Kostenschätzung.
- [ ] AK11: Lint, Format, `mypy src tests tools`, CI grün; kein Key im Repo (`git grep -n "sk-"` ohne Treffer
  außer Test-Platzhaltern). — Nachweis: Befehle aus `CLAUDE.md`, CI im PR

## Abgrenzung
- Nicht Teil dieser Spec: Claude/Anthropic (vom Nutzer verworfen), mehrere LLM-Agenten parallel, Worker-Threads
  und Dauerbetrieb (Nr. 5), Drawdown-Stopp (Nr. 4), Deployment und Key auf dem Server (Nr. 6), Dashboard (Nr. 7).
- Keine Prompt-Optimierung: Prompt und Schema bleiben die der Arena; jede Änderung wäre eine neue Strategieversion.
- Keine Batch-/Flex-Tarife (billiger, aber verzögert bzw. ohne Verfügbarkeitszusage).

## Relevante Entscheidungen & Learnings
- ADR-0004: Das LLM ist nur Ratgeber; Orders bleiben virtuell, `live` gesperrt.
- ADR-0007: Prompt, Schema, Kontext und Takt identisch zur Arena, belegt durch Golden-Tests **mit Grenzfällen**
  und Mutationstest (Learning „Golden-Fixtures beweisen Gleichheit nur mit Grenzfällen").
- ADR-0008: Schema-Erweiterung erhöht `user_version`; keine automatische Migration; ein aktiver Agent.
- ADR-0009 (angenommen): OpenAI/gpt-6.1-sol, `openai`-SDK, EUR-Budget und Provenienz in der DB.
- ADR-0002: Fremdpaket erlaubt, wenn per `uv add` gelockt.
- CLAUDE.md: Fail closed bei ungültigen LLM-Antworten; BTC-Fehler blockiert ETH nicht; Secrets nie loggen.
- LEARNINGS #windows #encoding: deutsche Texte, Ausgaben als UTF-8; Ersetzungen mit Nicht-ASCII per Edit.

## Ansatz
Neues Paket `src/llm_trading_bot/llm/`:
- `contract.py`: `SYSTEM`, `SIGNAL_SCHEMA`, `user_message(payload)`, `parse_signal(text) -> Signal`
  (Arena `strict_response` + `validate_signal`).
- `due.py`: `llm_due(...)` und Tageszähler wie Arena.
- `budget.py`: `BudgetSettings` (Decimal), `reserve(con, ...)`, `settle(con, ...)` in `write_tx`.
- `openai_client.py`: `SignalModel`-Protocol und `OpenAIResponses` (Responses API, `text.format` json_schema
  strict, `reasoning.effort` medium, `max_output_tokens` 8000, Timeout 60 s, `max_retries` 2). Liefert Text,
  Antwort-ID, Status und `usage` (inkl. `output_tokens_details.reasoning_tokens`). Tests nutzen ein Fake.
- `cycle.py`: `llm_cycle` je Markt wie Arena: fällig? → Budget reservieren → Aufruf → abrechnen → prüfen →
  Quote-Recheck → `record_decision` (Schlüssel `int(now)*10000 + attempt` wie Arena).
- `paper/db.py`: Schema v2 mit `llm_calls`; `paper/cycle.py`/CLI: Agent `gpt` verzweigt in `llm_cycle`.
- Generator `tools/golden/generate_llm_golden.py`: Arena-`SYSTEM`, Schema, Benutzer-Nachrichten und
  `llm_due`-Grenzfälle.

Kosten (Messung des Kontexts: ~800 Eingabe-Token, ~250 sichtbare Ausgabe; Reasoning-Tokens geschätzt ~1.500 bei
`medium`, in AK10 nachgemessen): mit `gpt-6.1-sol` ($2/$10 je 1 Mio.) ~0,019 $ pro Aufruf; bei höchstens 60
Aufrufen pro Tag ~1,15 $, im 30-min-Takt ohne Deckel ~1,85 $. Schlimmster Fall pro Aufruf (8.000 Ausgabe-Token)
~0,08 $ – darum 0,10 € Reservierung.

Verworfen: Claude (Nutzer), urllib (siehe ADR-0009), Budget-Datei der Arena (siehe ADR-0009), höherer Takt
(Modell sieht nur abgeschlossene Kerzen; mehr Aufrufe heißen eher mehr Trades bei ~0,65 % Roundtrip-Kosten).

## Plan
- [x] 1. Generator + `contract.py` + `due.py` mit Golden-Tests (AK1, AK2, AK3)
- [x] 2. Schema v2 mit `llm_calls`, v1 abgelehnt (AK8)
- [ ] 3. `budget.py` mit Reservierung/Abrechnung (AK4)
- [ ] 4. `uv add openai`, `openai_client.py` mit Fake-Tests und `net`-Test (AK2, AK5, AK7)
- [ ] 5. `llm_cycle` + Agent `gpt` in `paper-cycle`, Konfiguration (AK5, AK6, AK7, AK9)
- [ ] 6. Mutationstest erweitern, Doku (ARCHITEKTUR, README, `docs/betrieb/`), manueller Live-Test (AK10)

## Risiken & offene Fragen
- Reasoning-Tokens bei `medium` sind geschätzt; liegen sie deutlich höher, greift die Budgetsperre früher.
  AK10 misst nach.
- Ob strikte Structured Outputs die Zahlen-Enum `[0, 0.25, 0.45]` annehmen, sagt die OpenAI-Doku nicht
  ausdrücklich; die Arena sendet das Schema so. Prüfung im `net`-Test/AK10; falls abgelehnt: neues ADR statt
  stiller Schemaänderung.
- Die Arena setzt `max_output_tokens` 1500; wir setzen 8000 (OpenAI empfiehlt viel Reserve, sonst `incomplete`
  mit bezahlter, leerer Antwort). Bewusste Abweichung, in der Provenienz gespeichert.
- EUR/USD: Preise sind Schätzwerte; Standard rechnet USD 1:1 als EUR (eher zu hoch). Zusätzlich Limit im
  OpenAI-Konto setzen.
- Für AK10 braucht es einen OpenAI-Key lokal (nur in der Shell-Umgebung, nie in Dateien im Repo).
- Bei der Freigabe am 2026-11-06 bestätigt: `openai`-SDK, Budget/Provenienz in der DB (Schema v2),
  `max_output_tokens` 8000, Preis-Standard 2,00/10,00 € je 1 Mio. Token.

---

## Verlauf

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
