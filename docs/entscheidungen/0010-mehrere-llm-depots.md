# ADR-0010: Drei GPT-Depots zum Modellvergleich plus Buy & Hold; Schema bleibt versioniert

- **Status:** vorgeschlagen
- **Löst ab:** [ADR-0008](0008-paper-schema-ein-agent.md)
- **Datum:** 2026-11-06
- **Bereich:** #persistenz #paper #llm

## Kontext
ADR-0008 hat genau einen aktiven Agenten plus Buy & Hold festgelegt. Der Nutzer will am 2026-11-06 messen, ob
das teurere Reasoning von `gpt-6.1-sol` einen Mehrwert gegenüber günstigeren Modellen liefert, der die Kosten
rechtfertigt. Ein belastbarer Vorteil eines dieser Modelle beim Handeln ist nicht belegt; das Projekt soll ihn
experimentell prüfen. Dafür braucht es Depots, die dieselben Daten zur selben Zeit unter denselben Regeln und
Gebühren bekommen.

## Entscheidung
- Der Bot führt mehrere Depots parallel, konfiguriert über `BOT_AGENTS` (kommagetrennt). Standard:
  `gpt-sol,gpt-terra,gpt-luna` mit den Modellen `gpt-6.1-sol` (Haupt-Trader), `gpt-6.1-terra` und `gpt-6-luna`.
  **Buy & Hold** läuft immer als Vergleich. Klassische Strategien (`trend` usw.) bleiben als Agenten wählbar.
- Alle LLM-Depots eines Zyklus bekommen denselben Snapshot, dieselben Kosten- und Positionsregeln (Spec 004)
  und werden im selben Zyklus nacheinander gefragt; jedes Depot handelt nur auf eigene Rechnung.
- Weiter gültig aus ADR-0008: eigenes SQLite-Schema mit `PRAGMA user_version` (jede Erweiterung erhöht die
  Version), Ablehnung fremder oder älterer Versionen ohne automatische Migration, Spalten und
  Idempotenzschlüssel wie in der Arena.

## Verworfene Alternativen
- **Ein aktiver Agent (ADR-0008):** misst nicht, ob ein Modell sein Geld wert ist.
- **Vergleich später in eigener Spec:** vom Nutzer verworfen; der Vergleich soll von Anfang an mitlaufen.
- **Alle sieben Arena-Agenten:** der Bot bleibt schlank; Claude wurde vom Nutzer verworfen, klassische Depots
  sind wählbar, aber nicht Standard.
- **`gpt-6-astra` als Vergleich:** etwa fünfmal teurer als `gpt-6.1-sol` ohne belegten Handelsvorteil.

## Konsequenzen
- Positiv: direkter, fairer Modellvergleich nach Gebühren und Slippage, gegen Buy & Hold.
- Negativ: drei LLM-Kostenstellen; Zyklen dauern länger (drei Anfragen nacheinander); mehr Zeilen in der DB.
  Unterschiede sind bei kurzer Laufzeit statistisch schwach – Auswertung erst nach ausreichend Wochen.
- Neu bewerten, wenn: ein Modell nach Kosten klar zurückliegt, OpenAI die Modellreihe ändert oder der Echtgeld-
  Betrieb (M2) einen einzigen Agenten verlangt.

## Code-Anker
- `src/llm_trading_bot/paper/cycle.py`, `src/llm_trading_bot/llm/agents.py` (Spec 005)
