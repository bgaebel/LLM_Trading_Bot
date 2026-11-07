# Stand

**Aktualisiert:** 2026-11-08 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Strategie-Labor** freigegeben (ADR-0026/0027): Spec 015 Live-Aufzeichnung (zuerst), Spec 016 Kerzenarchiv,
  Backtest, Basisbericht v0.3.1 (Q2 2021–Q3 2025; ab 2025-10-01 versiegelt). 017/018 sind Skizzen.
- Spec 014 fertig: Impressum und Datenschutz live (ADR-0025). Release auf dem Server: 30536d0 (2026-11-07 22:08 UTC).
- Spec 013 fertig: Weboberfläche öffentlich per HTTPS (ADR-0024), live seit 2026-11-07.
- Alle anderen M1-Features fertig (Specs 001–012); Bot läuft auf dem Server seit 2026-11-06 22:38 UTC.

## Nächster Schritt
1. `/umsetzen 015`, Deploy nur mit Zustimmung; danach `/umsetzen 016`.
2. M1-Abschluss: 7 Tage ab dem letzten Deploy (Nutzer 2026-11-08); jeder Deploy startet die Uhr neu.
   Bisher nur die zwei Erstkäufe von Buy & Hold: sehr ruhiger Markt, alle Strategien warten auf Bewegung.

## In Arbeit
- Spec: 015 (in Arbeit), 016 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall; Backups/Snapshot nur nach Anpassung der
  Datenschutzerklärung (Journal mit IP-Adressen, siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
- Lizenz des Repos: übernommener Arena-Code steht unter GPL-3.0, das Repo hat keine Lizenzangabe.
- Für Spec 014: Postfach `kontakt@<domain>` bei Migadu erreichbar?, AVV mit Hetzner und Migadu prüfen.
