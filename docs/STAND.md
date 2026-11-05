# Stand

**Aktualisiert:** 2026-11-05 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Spec 002 (Server-Härtung) ist fertig und gemergt (PR #1). Spec 001 (Marktdaten) ist fertig und gemergt (PR #2): Bitvavo-Quotes und Kerzen, geprüfte Snapshots, `trading-bot market`. Der Server ist zu: nur Key-Login über den Admin-Benutzer, kein Root-
oder Passwort-Login, ufw nur SSH, automatische Sicherheitsupdates. Neustart und zweiter Lauf sind geprüft.
Zugangsdaten stehen nur lokal (`~/.ssh/config`, Alias), nicht im Repo.

## Nächster Schritt
1. `/umsetzen 003` – Indikatoren, klassische Strategien, LLM-Kontext (identisch zu Arena v0.3.1, ADR-0007).
2. Danach Nr. 3 (Paper-Ausführung + SQLite), dann 3a LLM-Strategie (vom Nutzer für M1 gewählt).

## In Arbeit
- Spec: 003 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- API-Keys (OpenAI/Anthropic) erst bei Bedarf in `/etc/llm-trading-bot/env` auf dem Server (Datei existiert leer, root, 600).
- Offene Projektentscheidungen: LLM-Budget, Startkapital für M2.
