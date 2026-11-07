# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Bot läuft auf dem Server** seit 2026-11-06 22:38 UTC (Spec 009, ADR-0018): sechs Depots plus Buy & Hold,
  Weboberfläche per Tunnel, Backups täglich. Update, Rollback, Wiederherstellung und Neustart geprüft (AK7–AK10).
- Spec 010 Live-Übersicht (ADR-0019): freigegeben – Seite aktualisiert sich ohne Neuladen über `/app.js`.

## Nächster Schritt
1. `/umsetzen 010`, danach Deploy (mit Zustimmung erteilt in der Freigabe).
2. `/abschluss 009` und `/abschluss 010`.
3. Danach Nr. 4 Risiko-Grenzen. Der 7-Tage-Lauf für M1 läuft seit 2026-11-06.

## In Arbeit
- Spec: 009 (alle AKs erledigt, Abschluss offen), 010 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
