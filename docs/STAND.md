# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Alle M1-Features fertig** (Specs 001–012). Spec 012: Broker-Naht und Live-Sperren als Entwurf für Echtgeld
  (ADR-0022 angenommen, ADR-0023 Vorschlag bis M2) – kein Laufzeitcode geändert, kein Deploy.
- Bot läuft auf dem Server seit 2026-11-06 22:38 UTC (Release `5fdddcd`, Spec 011 mit Pausenschalter geprüft);
  M1-Kriterium „≥ 7 Tage“ läuft.

## Nächster Schritt
1. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf, Neustart ohne Doppel-Orders, Backups geprüft – erledigt).
2. Danach M2 planen: zuerst die Entscheidungen des Nutzers aus ADR-0023 (Startkapital, eigenes Bitvavo-Konto,
   welches Depot live handelt).

## In Arbeit
- Spec: –
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- 2026-11-13 (7 Tage Laufzeit).

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
