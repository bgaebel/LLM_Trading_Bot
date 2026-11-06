# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `feat/s006-web` (baut auf `feat/s005-llm-strategie` auf) · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 006 Weboberfläche: Login mit Einrichtungscode, Eingabe von OpenAI-Key und GPT-Einstellungen, nur 127.0.0.1
  (ADR-0015). Schritte 1–4 umgesetzt; unabhängige Prüfung durchgeführt, Befunde werden behoben.
- Spec 005 GPT-Depots (`gpt-luna` Haupt, `gpt-sol` Gegenprobe, ADR-0012/0013): umgesetzt, Prüfbefunde behoben
  (auf `feat/s005-llm-strategie`, hier zusammengeführt); offen nur AK10 (Live-Test mit echtem Key).
- Fertig davor: Spec 004 (PR #4), 003 (PR #3), 001 (PR #2), 002 (PR #1).

## Nächster Schritt
1. Web-Befunde beheben, dann PRs für 005 und 006 (CI), Merge nach Freigabe durch den Nutzer.
2. Nutzer: Key über die Seite eintragen (Passwort ist bereits gesetzt), dann AK10 (005) und AK9 (006).

## In Arbeit
- Spec: 006 (in Arbeit), 005 (in Arbeit, nur AK10 offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- OpenAI-Key vom Nutzer (über die Weboberfläche) für die Live-Tests.

## Offen – manuell prüfen
- Spec 006 AK9: Login, Key, Verbindungstest, Logout im Browser.
- Spec 005 AK10: zwei echte `paper-cycle`-Läufe mit Key; gemessene Tokens/Kosten in den Verlauf.
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
