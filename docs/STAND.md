# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `feat/s013-oeffentlich-https` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 013 Weboberfläche öffentlich per HTTPS** freigegeben (ADR-0024 angenommen, löst ADR-0005 ab): Caddy vor
  der App, gehärteter Login, Passwort ab 12 Zeichen für den Zugang aus dem Internet.
- Alle anderen M1-Features fertig (Specs 001–012); Bot läuft auf dem Server seit 2026-11-06 22:38 UTC.

## Nächster Schritt
1. `/umsetzen 013` (Schritte 1–5), Schritt 6 nur mit Zustimmung (Server: Deploy, Caddy, Ports 80/443).
2. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf).

## In Arbeit
- Spec: 013 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
