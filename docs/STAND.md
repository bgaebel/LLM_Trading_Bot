# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 011 Risiko-Grenzen fertig und deployt** (Release `5fdddcd`): Drawdown-Stopp 10 % wie Arena, Wiederfreigabe
  und Handelspause über die Weboberfläche, Kurse je Markt. Pausenschalter auf dem Server geprüft.
- Bot läuft auf dem Server seit 2026-11-06 22:38 UTC (Specs 001–011 fertig); M1-Kriterium „≥ 7 Tage“ läuft.

## Nächster Schritt
1. Nr. 8 Echtgeld-Schnittstelle als Entwurf (`/spec`).
2. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf, Neustart ohne Doppel-Orders, Backups geprüft – erledigt).

## In Arbeit
- Spec: –
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
