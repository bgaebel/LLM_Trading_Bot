# Stand

**Aktualisiert:** 2026-11-05 · **Branch:** `feat/s001-marktdaten` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Spec 002 (Server-Härtung) ist fertig und gemergt (PR #1). Spec 001 (Marktdaten) ist umgesetzt, Live-Abruf gegen Bitvavo erfolgreich. Der Server ist zu: nur Key-Login über den Admin-Benutzer, kein Root-
oder Passwort-Login, ufw nur SSH, automatische Sicherheitsupdates. Neustart und zweiter Lauf sind geprüft.
Zugangsdaten stehen nur lokal (`~/.ssh/config`, Alias), nicht im Repo.

## Nächster Schritt
1. PR für Spec 001 mergen (Squash), wenn die CI grün ist.
2. Danach M1 Nr. 2 planen: welche Arena-Strategie der Bot fährt.

## In Arbeit
- Spec: 001 (fertig, PR offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- API-Keys (OpenAI/Anthropic) erst bei Bedarf in `/etc/llm-trading-bot/env` auf dem Server (Datei existiert leer, root, 600).
- Offene Projektentscheidungen: LLM-Budget, Startkapital für M2.
