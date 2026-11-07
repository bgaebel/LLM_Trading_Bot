# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `feat/s011-risiko` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 011 Risiko-Grenzen** freigegeben (ADR-0021 angenommen): Drawdown-Stopp 10 % wie Arena, Wiederfreigabe und
  Handelspause über die Weboberfläche, Kurse je Markt.
- Bot läuft auf dem Server seit 2026-11-06 22:38 UTC (Specs 001–010 fertig); M1-Kriterium „≥ 7 Tage“ läuft.

## Nächster Schritt
1. `/umsetzen 011` auf `feat/s011-risiko` (Schritte 1–5), Schritt 6 nur mit Zustimmung (Server).
2. Nr. 8 Echtgeld-Schnittstelle als Entwurf.
3. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf, Neustart ohne Doppel-Orders, Backups geprüft – erledigt).

## In Arbeit
- Spec: 011 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
