# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `feat/s009-deploy` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 009 Deploy (ADR-0018): Schritte 1–5 fertig (Backup-Befehle, Uhrsprung, Units, `release.sh`, `deploy.sh`,
  Doku), Prüfung vor dem Server mit 20 bestätigten Befunden umgesetzt; CI grün (1149 Tests). Draft-PR #15.
- Specs 001–008 fertig.

## Nächster Schritt
1. Mit Zustimmung: PR #15 mergen (`deploy.sh` schickt nur Commits von `main`), dann auf dem Server
   `deploy.sh <alias> <dienst> install` und `deploy` (erst nur Weboberfläche) – AK7.
2. Nutzer: Einrichtung per Tunnel (Passwort, Key, Depots), dann `deploy` erneut → Bot läuft.
3. Mit Zustimmung: Update/Rollback, Backup- und Wiederherstellungsprobe, Neustart (AK8–AK10), dann `/abschluss 009`.

## In Arbeit
- Spec: 009 (in Arbeit, Server-Schritte offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
