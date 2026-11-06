# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Fertig und gemergt: Spec 002 Server-Härtung (PR #1), Spec 001 Marktdaten (PR #2), Spec 003 Indikatoren,
klassische Strategien und LLM-Kontext identisch zu Arena v0.3.1 (PR #3; Golden-Fixtures mit Grenzfällen,
Mutationstest 51/51). Diagnose: `uv run trading-bot market`, `uv run trading-bot signals`.

## Nächster Schritt
1. Spec 004 (Paper-Ausführung + SQLite, Entwurf) und ADR-0008 (vorgeschlagen) durchsehen und freigeben.
   Zu bestätigen: Startkapital 10.000 EUR, Slippage 0,05 %, aktiver Agent `trend` bis zur LLM-Strategie.
2. Danach `/umsetzen 004`, dann Spec für 3a LLM-Strategie (Budgetsperre, Provenienz).

## In Arbeit
- Spec: 004 (Entwurf, wartet auf Freigabe)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- Freigabe von Spec 004 / ADR-0008 durch den Nutzer.

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- API-Keys (OpenAI/Anthropic) erst bei Bedarf in `/etc/llm-trading-bot/env` auf dem Server (Datei existiert leer, root, 600).
- Offene Projektentscheidungen: LLM-Budget, Startkapital für M2.
