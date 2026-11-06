# ADR-0009: LLM-Agent über die OpenAI-API (gpt-6.1-sol) mit hartem EUR-Tagesbudget in der Datenbank

- **Status:** abgelöst durch [ADR-0011](0011-gpt-gemeinsame-anfrage.md)
- **Löst ab:** –
- **Datum:** 2026-11-06
- **Bereich:** #llm #budget #persistenz

## Kontext
Der Nutzer hat für M1 eine LLM-Strategie gewählt. ADR-0008 erlaubt einen aktiven Agenten; die Arena fährt GPT
und Claude parallel. Der Nutzer hat entschieden: nur GPT, Modell `gpt-6.1-sol` (Arena-Default, laut OpenAI
ausgewogen zwischen Können und Kosten), Arena-Takt, harte Grenze 3 € pro UTC-Tag. Die Arena führt ihr Budget in
einer JSON-Datei neben der DB und ihre Provenienz in einzelnen JSON-Dateien; beides liegt außerhalb der
Transaktionen der DB.

## Entscheidung
- Der LLM-Agent heißt `gpt` und nutzt die **OpenAI Responses API** mit strikter `json_schema`-Ausgabe über das
  offizielle Python-Paket **`openai`** (per `uv add`, ADR-0002). Modell per `OPENAI_MODEL`, Standard `gpt-6.1-sol`,
  Reasoning-Effort `medium`, `max_output_tokens` 8000.
- Prompt (`SYSTEM`), Signal-Schema, LLM-Kontext und Takt sind identisch zur Arena v0.3.1 (ADR-0007).
- **Budget in der Bot-DB** (Schema v2): Vor jedem Aufruf wird in einer eigenen, committeten Transaktion eine
  Reservierung (Standard 0,10 €) gebucht. Ein Aufruf findet nur statt, wenn die Summe der heutigen Kosten und
  Reservierungen plus die neue Reservierung ≤ `LLM_DAILY_BUDGET_EUR` (Standard 3 €) bleibt. Nach der Antwort wird
  die Reservierung durch die aus `usage` berechneten Kosten ersetzt. Unbekannte Kosten (Absturz, Timeout) bleiben
  als Reservierung stehen; Kosten über der Reservierung sperren den Rest des Tages.
- Preise sind Schätzwerte in EUR je 1 Mio. Token (`OPENAI_INPUT_EUR_PER_MILLION`, `OPENAI_OUTPUT_EUR_PER_MILLION`).
  Standard für `gpt-6.1-sol`: 2,00 / 10,00 – die USD-Listenpreise 1:1 als EUR, also eher zu hoch. Für ein anderes
  Modell ohne gesetzte Preise wird nicht aufgerufen.
- **Provenienz in der Bot-DB**: jeder Versuch eine Zeile mit Modell, Anfrage-Parametern, Prompt-Hash, Kontext,
  Antwort-ID, Token-Nutzung, Kosten, Dauer und Status; die Entscheidung verweist darauf. Der API-Key wird nie
  gespeichert oder ausgegeben.

## Verworfene Alternativen
- **Claude oder beide Anbieter:** vom Nutzer verworfen (nur GPT; ein Key, eine Integration).
- **urllib wie in der Arena:** keine neue Abhängigkeit, aber Wiederholungen, Timeouts und API-Änderungen müssten wir
  selbst pflegen; der Markt-Transport bleibt bewusst GET-only (Spec 001).
- **Budget-Ledger als JSON-Datei (Arena):** zweite Wahrheitsquelle außerhalb der DB-Transaktionen; mit dem
  Single-Writer (ADR-0008) ist die DB der einfachere, atomare Ort.
- **Kein hartes Budget, nur Aufrufzähler:** die Arena nennt ihren Zähler ausdrücklich „kein Kostenlimit".

## Konsequenzen
- Positiv: Kosten pro Tag sind hart begrenzt und nachvollziehbar; jede LLM-Entscheidung ist mit ihren Eingaben
  und Kosten verknüpft; dieselbe Transaktionslogik wie die Ausführung.
- Negativ: neue Abhängigkeit `openai`; Schema v2 – v1-DBs werden abgelehnt (es gibt noch keine Betriebs-DB).
  Kosten sind Schätzungen, keine Rechnungen; zusätzlich ein Limit im OpenAI-Konto setzen.
- Neu bewerten, wenn: ein zweiter Anbieter dazukommt, OpenAI das Preismodell ändert oder die gemessenen
  Kosten deutlich von der Schätzung abweichen.

## Code-Anker
- `src/llm_trading_bot/llm/` (Spec 005), `src/llm_trading_bot/paper/db.py` (Schema v2)
