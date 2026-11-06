# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Fertig und gemergt: Spec 004 Paper-Ausführung + SQLite (PR #4).
Schema v1 (ADR-0008), Single-Writer-Sperre, Ausführung bitgleich zur Arena (Mutationstest 91/91 wie erwartet),
Fail-closed bei veralteten Quotes, Buy & Hold einmalig, `uv run trading-bot paper-cycle --db <pfad>`.
Fertig davor: Spec 002 (PR #1), 001 (PR #2), 003 (PR #3).

## Nächster Schritt
1. Spec für 3a LLM-Strategie (Budgetsperre, Provenienz; Schema-Erweiterung → `user_version` 2).
2. Danach Nr. 4 Risiko inkl. 10-%-Drawdown-Stopp, Nr. 5 Dienst (Quotes nur einmal je Zyklus holen).

## In Arbeit
- Spec: –
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- API-Keys (OpenAI/Anthropic) erst bei Bedarf in `/etc/llm-trading-bot/env` auf dem Server (Datei existiert leer, root, 600).
- Offene Projektentscheidungen: LLM-Budget, Startkapital für M2.
