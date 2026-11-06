# ADR-0013: GPT nur bei Bedarf fragen – stündlich, bei starker Bewegung oder Signalwechsel, höchstens 4/h

- **Status:** angenommen
- **Löst ab:** [ADR-0011](0011-gpt-gemeinsame-anfrage.md)
- **Datum:** 2026-11-06
- **Bereich:** #llm #budget #persistenz

## Kontext
ADR-0011 sah 4 reguläre Abfragen pro Stunde plus höchstens 4 ereignisbasierte vor. Der Nutzer hat am 2026-11-06
vorgegeben, die Anzahl der Abfragen „so gering wie möglich, aber so viel wie nötig" einzustellen. Das Modell sieht
nur abgeschlossene Kerzen (1/15/60/240 min), Kurse, das Depotgewicht und die Referenzsignale der klassischen
Strategien (`trend`, `breakout`, `meanrev`). Neue, entscheidende Information entsteht, wenn eine Stundenkerze
schließt, der Kurs sich stark bewegt oder die klassischen Strategien ihr Urteil ändern – nicht durch bloßes
häufigeres Fragen. Jeder zusätzliche Trade kostet ~0,65 % Roundtrip, das ist mehr als jede LLM-Abfrage.

## Entscheidung
- **Takt je Depot** (Abfrage = eine Anfrage für beide Märkte):
  - regulär **alle 60 min** (`LLM_MIN_INTERVAL_SECONDS` 3600, Untergrenze 900);
  - **Ereignis**, frühestens 10 min nach dem letzten Versuch (`LLM_EVENT_COOLDOWN_SECONDS` 600, Untergrenze 300),
    wenn sich ein Markt seit dem letzten Versuch um ≥ 1,8 % bewegt hat (`LLM_EVENT_MOVE_THRESHOLD`, Untergrenze
    0,5 %) **oder** ein Referenzsignal eines Marktes (`trend`/`breakout`/`meanrev`) sich gegenüber dem letzten
    Versuch geändert hat;
  - harte Grenze **4 Versuche je rollierender Stunde** (`LLM_MAX_CALLS_PER_HOUR`).
  Erwartet: ~30–50 Abfragen pro Tag und Depot statt 96–192.
- Unverändert übernommen aus ADR-0011: eine Anfrage für BTC und ETH mit Prompt v1 (`llm-v1`, versioniert, Hash-
  Test), Antwort je Markt mit `action`, `target_fraction`, `confidence`, `market_regime`, `reason`, strenge Prüfung
  je Markt nach Arena-Regeln, ganze Antwort verwerfen bei einem ungültigen Teil; OpenAI Responses API über `openai`,
  Effort `low`, `max_output_tokens` 8000, Timeout 60 s, höchstens 2 Wiederholungen, `store=False`; Budget und
  Provenienz je Versuch in `llm_calls`, Gesamtgrenze 3 €/Tag, Reservierung = schlimmster Fall aus Preisen,
  unbekannte Kosten zählen mit, Modell ohne Preis wird nie gefragt; API-Key nie gespeichert oder ausgegeben.
- Depots und Anteile: siehe ADR-0012 (`gpt-luna` 0,50 €, `gpt-sol` 2,50 €).

## Verworfene Alternativen
- **4/h + ≤ 4 Ereignisse (ADR-0011):** mehr Abfragen ohne neue Information; vom Nutzer zugunsten „so wenig wie
  möglich" verworfen.
- **Nur stündlich ohne Ereignisse:** zu langsam bei starken Bewegungen oder einem Trendwechsel der Indikatoren.
- **Jede 15-min-Kerze:** die meisten Abfragen sähen fast dieselben Daten; mehr HOLD-Antworten oder unnötige Trades.
- **Signalwechsel der Ensemble-Strategie als Auslöser:** das Ensemble ist im Kontext nicht enthalten; die drei
  Referenzsignale decken es ab.

## Konsequenzen
- Positiv: deutlich weniger Kosten (sol ~0,30–0,50 $/Tag, luna ~0,02 $/Tag erwartet); Abfragen fallen dort an,
  wo sich die Lage ändert.
- Negativ: Reaktion auf langsame Drifts innerhalb einer Stunde nur über die Referenzsignale; mehrere schnelle
  Ereignisse hintereinander sind auf 4/h begrenzt.
- Neu bewerten, wenn: die Paper-Depots zeigen, dass ein anderer Takt nach Kosten besser abschneidet, oder die
  Ereignis-Auslöser deutlich häufiger/seltener feuern als erwartet.

## Code-Anker
- `src/llm_trading_bot/llm/due.py`, `src/llm_trading_bot/llm/` (Spec 005), `src/llm_trading_bot/paper/db.py`
