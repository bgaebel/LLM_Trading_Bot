# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Bot läuft auf dem Server** seit 2026-11-06 22:38 UTC: sechs Depots plus Buy & Hold, Live-Übersicht per Tunnel,
  Backups täglich (Specs 009, 010; ADR-0019, ADR-0020).
- Specs 001–010 fertig. M1-Kriterium „≥ 7 Tage ununterbrochen“ läuft seit 2026-11-06.

## Nächster Schritt
1. Nr. 4 Risiko-Grenzen (`/spec`).
2. Nr. 8 Echtgeld-Schnittstelle als Entwurf.
3. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf, Neustart ohne Doppel-Orders, Backups geprüft – erledigt).

## In Arbeit
- Spec: –
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
