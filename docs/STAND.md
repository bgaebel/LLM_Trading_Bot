# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 009 Deploy (ADR-0018): Code auf `main` (PR #15). **Bot läuft auf dem Server** seit 2026-11-06 22:38 UTC mit
  sechs Depots (4 klassisch, gpt-luna, gpt-sol) und Buy & Hold; Weboberfläche per Tunnel; Backup-Timer aktiv. AK7 erfüllt
  bis auf die Durchsicht der Übersicht durch den Nutzer.
- Specs 001–008 fertig.

## Nächster Schritt
1. Mit Zustimmung: Update auf einen neueren Commit und `rollback` (AK8), Backup- und Wiederherstellungsprobe (AK10),
   Neustart des Servers (AK9); dann `/abschluss 009`.
2. Ab jetzt läuft der 7-Tage-Lauf für das M1-Kriterium.
3. Danach Nr. 4 Risiko-Grenzen.

## In Arbeit
- Spec: 009 (in Arbeit, AK8–AK10 offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
