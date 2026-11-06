# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `feat/s006-web` (baut auf `feat/s005-llm-strategie` auf) · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 006 Weboberfläche: Login mit Einrichtungscode, Eingabe von OpenAI-Key und GPT-Einstellungen, nur 127.0.0.1
  (ADR-0014). Schritte 1–4 umgesetzt, Seite läuft lokal; Ersteinrichtung/Login/Key-Eingabe durch den Nutzer offen.
- Spec 005 GPT-Depots (`gpt-luna` Haupt, `gpt-sol` Gegenprobe, ADR-0012/0013): alle Schritte umgesetzt,
  Mutationstest gesamt 132/132 wie erwartet; offen nur AK10 (Live-Test mit echtem Key) und `/abschluss`.
- Fertig davor: Spec 004 (PR #4), 003 (PR #3), 001 (PR #2), 002 (PR #1).

## Nächster Schritt
1. Nutzer: auf der Seite Passwort festlegen, Key eintragen, „Verbindung testen" (Spec 006 AK9).
2. Dann Live-Test Spec 005 AK10: `uv run trading-bot paper-cycle --db <datenverzeichnis>/bot.db` zweimal.
3. `/abschluss` für 005, danach für 006 (Branch baut auf 005 auf → nach dem Merge von 005 auf `main` neu aufsetzen).

## In Arbeit
- Spec: 006 (in Arbeit), 005 (in Arbeit, nur AK10 offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- OpenAI-Key vom Nutzer (über die Weboberfläche) für die Live-Tests.

## Offen – manuell prüfen
- Spec 006 AK9: Ersteinrichtung, Login, Key, Verbindungstest, Logout im Browser.
- Spec 005 AK10: zwei echte `paper-cycle`-Läufe mit Key; gemessene Tokens/Kosten in den Verlauf.
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
