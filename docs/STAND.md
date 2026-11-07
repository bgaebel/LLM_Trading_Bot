# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `feat/s012-broker-naht` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 012 Echtgeld-Schnittstelle als Entwurf** freigegeben (ADR-0022 angenommen, ADR-0023 bleibt Vorschlag bis
  M2): Broker-Naht, Live-Typen und Zustandsmaschine, Sperren als Tests – keine Laufzeitänderung, kein Deploy.
- Bot läuft auf dem Server seit 2026-11-06 22:38 UTC (Specs 001–011 fertig, Release `5fdddcd`); M1-Kriterium
  „≥ 7 Tage“ läuft.

## Nächster Schritt
1. `/umsetzen 012` auf `feat/s012-broker-naht` (Schritte 1–5).
2. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf, Neustart ohne Doppel-Orders, Backups geprüft – erledigt).

## In Arbeit
- Spec: 012 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
