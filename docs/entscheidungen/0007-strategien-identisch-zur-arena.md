# ADR-0007: Übernommene Strategien verhalten sich identisch zu Arena v0.3.1; Änderungen nur als neue Version

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-06
- **Bereich:** #strategie #arena

## Kontext
Die Arena vergleicht Strategien unter festen Bedingungen. Sie sichert sie über einen Vertragstest ab
(Syntaxbaum von `strategies.py` unverändert) und betont: Strategieänderungen verändern die Versuchsanordnung.
Der Bot übernimmt Indikatoren und Strategien (Spec 003). Weicht er unbemerkt ab, sind Arena-Ergebnisse nicht mehr
auf ihn übertragbar, und die LLM-Strategie bekäme einen anderen Kontext als in der Arena.

## Entscheidung
- Indikatoren, klassische Strategien und LLM-Kontext (`feature_payload`) verhalten sich **identisch** zu
  Arena@7563724 (Strategiestand v0.3.1). Das belegen Golden-Fixtures, die aus dem Arena-Code erzeugt werden.
  Toleranz für Gleitkommazahlen: relativ 1e-12, Texte exakt.
- Jedes Signal trägt Strategiename und `STRATEGY_VERSION` (`v0.3.1`).
- Jede Änderung an Parametern, Logik oder Rechenweg ist eine **neue Strategieversion**. Sie braucht eigene Spec,
  eigene Tests und eine neue Versionskennung. Die alte Version bleibt für Vergleiche verfügbar. Die Fixtures für
  v0.3.1 werden nie angepasst, damit ein Test wieder grün wird.

## Verworfene Alternativen
- **Strategien beim Übernehmen „aufräumen"** (z. B. vektorisieren, Parameter optimieren): bricht die
  Vergleichbarkeit mit der Arena, unbemerkte Abweichungen in Gleitkommazahlen.
- **Nur stichprobenartige Tests:** belegt keine Gleichheit.
- **Arena-Code im Test direkt importieren:** Test- und Laufzeitabhängigkeit, widerspricht ADR-0003.

## Konsequenzen
- Positiv: Ergebnisse und Erkenntnisse der Arena bleiben auf den Bot übertragbar; Abweichungen fallen sofort auf.
- Negativ: Offensichtliche Schwächen der Strategien bleiben bis zu einer bewusst neuen Version bestehen.
- Neu bewerten, wenn: der Bot eigene Strategieversionen fährt, die in der Arena nicht vorkommen.

## Code-Anker
- `src/llm_trading_bot/strategy/` (Spec 003), `tests/strategy/fixtures/`
