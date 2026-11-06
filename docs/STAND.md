# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 009 Deploy (ADR-0018): freigegeben, Umsetzung beginnt. Release je Commit per SSH mit automatischem
  Code-Rollback, DB-Backups täglich nur auf dem Server (14 Tage), Wiederherstellungsprobe, Neustart-Test.
- Specs 001–008 fertig.

## Nächster Schritt
1. `/umsetzen 009`: Schritte 1–5 (Backup-Befehle, Uhrsprung, Units, `release.sh`, `deploy.sh`, Doku) hier.
2. Schritte 6–7 auf dem Server – jeweils erst nach Zustimmung des Nutzers.
3. Danach Nr. 4 Risiko-Grenzen.

## In Arbeit
- Spec: 009 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
