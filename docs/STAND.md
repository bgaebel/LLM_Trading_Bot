# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 014 Impressum und Datenschutz** freigegeben (ADR-0025): Impressum nur auf dem Server, Datenschutz im Repo
  an den Code gebunden, Log-Fristen auf dem Server durchgesetzt (15 Tage, Firewall ohne Log).
- Spec 013 fertig: Weboberfläche öffentlich per HTTPS (ADR-0024), live seit 2026-11-07. Release auf dem Server: 850221e.
- Alle anderen M1-Features fertig (Specs 001–012); Bot läuft auf dem Server seit 2026-11-06 22:38 UTC.

## Nächster Schritt
1. `/umsetzen 014` (Schritte 1–6), Schritt 7 nur mit Zustimmung (Server, Aufräumen alter Logs).
2. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf seit 2026-11-06 22:38 UTC).

## In Arbeit
- Spec: 014 (in Arbeit)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
- Für Spec 014: Postfach `kontakt@<domain>` bei Migadu, AVV mit Hetzner und Migadu prüfen.
