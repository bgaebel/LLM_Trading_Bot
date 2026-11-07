# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Bot läuft auf dem Server** seit 2026-11-06 22:38 UTC (Spec 009, ADR-0018): sechs Depots plus Buy & Hold,
  Weboberfläche per Tunnel, Backups täglich. Update, Rollback, Wiederherstellung und Neustart geprüft.
- Spec 010 Live-Übersicht (ADR-0019): umgesetzt und deployt (PR #16); offen nur AK6 – Durchsicht auf dem Server.

## Nächster Schritt
1. Nutzer: Übersicht per Tunnel prüfen (Alter zählt, neue Runde ohne Neuladen) → AK6 von Spec 010.
2. `/abschluss 009` und `/abschluss 010`.
3. Danach Nr. 4 Risiko-Grenzen. Der 7-Tage-Lauf für M1 läuft seit 2026-11-06.

## In Arbeit
- Spec: 009 (alle AKs erledigt, Abschluss offen), 010 (nur AK6 offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
