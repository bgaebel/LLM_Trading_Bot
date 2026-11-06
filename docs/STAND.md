# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 005 GPT-Depots und Spec 006 Weboberfläche sind gemergt (PR #5, #6, Chart-Hintergrund #7); alle manuellen
  Abnahmen erledigt: 006 AK9 (Nutzer: Login, Key) und 005 AK10 (zwei Live-Läufe: luna 0,00034 €, sol 0,0057 € je
  Anfrage, Lauf 2 `NOT_DUE`). Es fehlt nur der formale Abschluss (`/abschluss 005`, `/abschluss 006`).
- Fertig davor: Spec 004 (PR #4), 003 (PR #3), 001 (PR #2), 002 (PR #1).

## Nächster Schritt
1. `/abschluss 005` und `/abschluss 006` (Nutzer startet; Prüfer, Ergebnis-Abschnitt, Status „fertig").
2. Spec 007 Übersicht (freigegeben, ADR-0016): `/umsetzen 007` auf `feat/s007-uebersicht`.
3. Dann nächste M1-Spec laut `docs/ROADMAP.md` (Dauerbetrieb/Deploy auf Hetzner).

## In Arbeit
- Spec: 005, 006 (alle AKs erfüllt, Abschluss offen), 007 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
