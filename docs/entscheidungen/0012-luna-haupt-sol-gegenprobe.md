# ADR-0012: Zwei GPT-Depots – gpt-6-luna als Haupt-Trader, gpt-6.1-sol als Gegenprobe

- **Status:** angenommen
- **Löst ab:** [ADR-0010](0010-mehrere-llm-depots.md)
- **Datum:** 2026-11-06
- **Bereich:** #llm #paper #budget

## Kontext
ADR-0010 sah drei GPT-Depots vor (sol als Haupt-Trader, terra und luna als Vergleich). Der Nutzer hat am
2026-11-06 auf `gpt-6-luna` umgestellt und will `gpt-6.1-sol` nur noch als Gegenprobe: Liefert das teure
Reasoning einen Mehrwert, der die etwa zwanzigfachen Kosten rechtfertigt? Claude Haiku 5.5 wurde geprüft:
gleicher Listenpreis wie `gpt-6-luna` (0,10 $ / 0,50 $ je 1 Mio. Token), also nicht „deutlich günstiger"; der
Nutzer wollte nur unter dieser Bedingung wechseln.

## Entscheidung
- Standard-Depots (`BOT_AGENTS`): `gpt-luna` (`gpt-6-luna`, Haupt-Trader) und `gpt-sol` (`gpt-6.1-sol`,
  Gegenprobe), plus Buy & Hold. `gpt-terra` entfällt. Klassische Agenten bleiben wählbar.
- Beide bekommen im selben Zyklus denselben Snapshot, dieselben Regeln und Gebühren (wie ADR-0010).
- Tagesanteile am Gesamtbudget von 3 € (ADR-0011): `gpt-luna` 0,50 €, `gpt-sol` 2,50 € – ersetzt die Anteile
  aus ADR-0011 (sol 2,00 / terra 0,90 / luna 0,10). Erwartet: luna ~0,05–0,10 $, sol ~0,96–1,92 $ pro Tag.
- Weiter gültig aus ADR-0010/0008: versioniertes Schema, keine automatische Migration.

## Verworfene Alternativen
- **Drei Depots inkl. terra (ADR-0010):** vom Nutzer verworfen; zwei Pole (billig/teuer) reichen für die Frage.
- **Nur Luna:** vom Nutzer verworfen – ohne Gegenprobe bleibt offen, ob das teurere Modell besser wäre.
- **Claude Haiku 5.5 statt Luna:** gleicher Listenpreis, Qualität für diesen Zweck nicht belegt, zweiter Anbieter
  mit eigener Integration; später als Vergleichsdepot denkbar.

## Konsequenzen
- Positiv: billiger Haupt-Trader; der Vergleich beantwortet direkt, ob sich `gpt-6.1-sol` lohnt.
- Negativ: Gegenprobe kostet weiterhin ~1–2 $/Tag; Aussagekraft erst nach Wochen.
- Neu bewerten, wenn: einer der beiden nach Kosten klar zurückliegt oder ein anderer Anbieter deutlich günstiger
  bei belegt gleicher Qualität ist.

## Code-Anker
- `src/llm_trading_bot/llm/agents.py` (Spec 005)
