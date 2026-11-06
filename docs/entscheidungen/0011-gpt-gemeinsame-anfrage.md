# ADR-0011: Eine GPT-Anfrage für BTC und ETH mit eigenem Prompt v1, 4/h plus Ereignisse, Effort low

- **Status:** abgelöst durch [ADR-0013](0013-gpt-abfragen-nur-bei-bedarf.md)
- **Löst ab:** [ADR-0009](0009-llm-agent-openai-budget.md)
- **Datum:** 2026-11-06
- **Bereich:** #llm #budget #persistenz

## Kontext
ADR-0009 sah je Markt eine Anfrage mit dem Arena-Prompt im Arena-Takt (30 min, ≤ 60/Tag) und Effort `medium`
vor. Der Nutzer hat am 2026-11-06 entschieden: beide Märkte in **einer** Anfrage mit eigenem Antwortformat,
4 reguläre Aufrufe pro Stunde plus höchstens 4 ereignisbasierte (≤ 8/h), Effort zunächst `low` (später gegen
`medium` testen). Das ist eine Testkonfiguration, keine nachgewiesen optimale Handelsfrequenz. Die gemeinsame
Anfrage ist ~2.900 Zeichen Kontext (gemessen) statt zweimal ~1.450.

## Entscheidung
- **Prompt v1 (`llm-v1`)**: eigener Systemprompt auf Basis des Arena-Prompts; Eingabe `{"markets": {"BTC-EUR": …,
  "ETH-EUR": …}}` mit dem Arena-Kontext `feature_payload` je Markt (inkl. Referenzsignale der klassischen
  Strategien, Depotgewicht, Kosten). Antwort je Markt: `action`, `target_fraction` ∈ {0, 0.25, 0.45},
  `confidence`, `market_regime` ∈ {UPTREND, DOWNTREND, SIDEWAYS}, `reason` – striktes `json_schema`. Fehlt einem
  Markt der Snapshot, enthält die Anfrage nur den anderen. Prompt und Schema sind versioniert; jede Änderung ist
  eine neue Version (Hash-Test).
- **Prüfung wie Arena je Markt** (genau die Felder, BUY nur 0.25/0.45, SELL nur 0, confidence in [0, 1], reason
  nicht leer). Ist ein Teil der Antwort ungültig, wird die ganze Antwort verworfen (fail closed).
- **Takt je Agent**: regulär alle 15 min (`LLM_MIN_INTERVAL_SECONDS`, min. 600), Ereignis nach ≥ 1,8 % Bewegung
  eines Marktes seit dem letzten Versuch (`LLM_EVENT_MOVE_THRESHOLD`, min. 0,5 %), frühestens nach 5 min
  (`LLM_EVENT_COOLDOWN_SECONDS`, min. 300); harte Grenze 8 Versuche je rollierender Stunde (`LLM_MAX_CALLS_PER_HOUR`).
- **OpenAI Responses API** über das Paket `openai`, `reasoning.effort` `low` (`LLM_EFFORT`), `max_output_tokens`
  8000, Timeout 60 s, höchstens 2 Wiederholungen.
- **Budget in der Bot-DB**, je Versuch eine Zeile `llm_calls` (Provenienz und Ledger zugleich). Gesamtgrenze
  `LLM_DAILY_BUDGET_EUR` 3 € je UTC-Tag; zusätzlich je Agent `gpt-sol` 2,00 €, `gpt-terra` 0,90 €, `gpt-luna` 0,10 €
  (`LLM_DAILY_BUDGET_EUR_<AGENT>`), damit ein Depot die anderen nicht aushungert. Reservierung je Versuch =
  schlimmster Fall aus Preisen (Eingabezeichen als Tokens, plus `max_output_tokens`). Unbekannte Kosten zählen mit
  der Reservierung. Preise (EUR je 1 Mio. Token, Listenpreise in USD 1:1, eher zu hoch): sol 2/10, terra 1/5,
  luna 0,10/0,50; Modell ohne Preis wird nicht aufgerufen.
- Der API-Key wird nie gespeichert oder ausgegeben.

## Verworfene Alternativen
- **Arena-Prompt je Markt (ADR-0009):** vom Nutzer verworfen; dafür ist das LLM nicht mehr direkt mit der Arena
  vergleichbar (die klassischen Strategien und die Ausführung bleiben es).
- **Antwort ohne `target_fraction`** (Vorschlag mit nur action/regime/reason): die Ausführung braucht eine
  Zielgewichtung; ohne sie müsste der Bot sie erfinden.
- **Teilweise gültige Antworten nutzen:** fail closed ist einfacher prüfbar; ein Markt mit kaputtem Teil handelt
  nicht auf Vermutungen.
- **Feste Reservierung 0,10 €:** passt nur zu `gpt-6.1-sol`; je Modell aus Preisen berechnet bleibt sie für alle
  Modelle eine echte Obergrenze.
- **urllib, JSON-Budgetdatei, Claude:** Gründe wie in ADR-0009.

## Konsequenzen
- Positiv: halb so viele Anfragen je Takt; Modelle sehen beide Märkte zusammen; Kosten erwartet je Tag ~0,96–1,92 $
  (sol), ~0,48–0,96 $ (terra), ~0,05–0,10 $ (luna) bei ~1.500 Eingabe- und ~700 Ausgabe-Token (Reasoning `low`
  geschätzt, nachzumessen).
- Negativ: eigener Prompt ohne Arena-Golden; die LLM-Ergebnisse der Arena sind nur noch Orientierung.
  Ein Ausfall der Anfrage betrifft beide Märkte dieses Agenten (die klassischen Signale und andere Agenten nicht).
- Neu bewerten, wenn: gemessene Kosten oder Reasoning-Tokens deutlich abweichen, `low` gegen `medium` getestet ist
  oder die Depots zeigen, dass ein anderer Takt besser ist.

## Code-Anker
- `src/llm_trading_bot/llm/` (Spec 005), `src/llm_trading_bot/paper/db.py` (Tabelle `llm_calls`)
