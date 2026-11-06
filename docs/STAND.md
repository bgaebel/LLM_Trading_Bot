# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `feat/s004-paper` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Spec 004 Paper-Ausführung + SQLite: alle 6 Plan-Schritte umgesetzt und committet. Schema `user_version = 1`
(ADR-0008), Single-Writer-Sperre, virtuelle Ausführung bitgleich zur Arena (Golden-Fixtures mit Grenzfällen und
Verläufen, Mutationstest gesamt 91/91 wie erwartet), Fail-closed bei veralteten Quotes, Buy & Hold einmalig,
`uv run trading-bot paper-cycle --db <pfad>` live zweimal geprüft.
Fertig davor: Spec 002 (PR #1), 001 (PR #2), 003 (PR #3).

## Nächster Schritt
1. `/abschluss` für Spec 004 (Prüfer, PR, CI, Squash-Merge nach Freigabe).
2. Danach Spec für 3a LLM-Strategie (Budgetsperre, Provenienz), dann Nr. 4 Risiko inkl. Drawdown-Stopp.

## In Arbeit
- Spec: 004 (in Arbeit, Schritte erledigt, Abschluss offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- API-Keys (OpenAI/Anthropic) erst bei Bedarf in `/etc/llm-trading-bot/env` auf dem Server (Datei existiert leer, root, 600).
- Offene Projektentscheidungen: LLM-Budget, Startkapital für M2.
