# Stand

**Aktualisiert:** 2026-11-05 · **Branch:** `feat/s002-server-setup` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Spec 002 (Server-Härtung) ist umgesetzt. Der Server ist zu: nur Key-Login über den Admin-Benutzer, kein Root-
oder Passwort-Login, ufw nur SSH, automatische Sicherheitsupdates. Neustart und zweiter Lauf sind geprüft.
Zugangsdaten stehen nur lokal (`~/.ssh/config`, Alias), nicht im Repo.

## Nächster Schritt
1. PR für Spec 002 durchsehen (AK8) und mergen (Squash), wenn die CI grün ist.
2. `/umsetzen 001` – Marktdaten aus der Arena übernehmen.

## In Arbeit
- Spec: 002 (fertig, PR offen) · 001 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Offene Projektentscheidungen: LLM-Budget, Startkapital für M2.
