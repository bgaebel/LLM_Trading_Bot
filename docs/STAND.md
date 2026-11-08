# Stand

**Aktualisiert:** 2026-11-08 · **Branch:** `feat/s019-oeffentliche-uebersicht` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 019** (Übersicht ohne Login, ADR-0028 löst 0024 ab) gebaut und geprüft; dazu „Letzte Signale“ ohne HOLD.
  Nächster Schritt: PR, CI, Merge, Deploy mit Prüfung von außen (AK11).
- **Spec 015 fertig:** Live-Aufzeichnung in `observations.db` läuft seit 2026-11-08 08:23 UTC (Release aac30e4).
- **Strategie-Labor** (ADR-0026/0027): danach Spec 016 Kerzenarchiv, Backtest, Basisbericht v0.3.1
  (Q2 2021–Q3 2025; ab 2025-10-01 versiegelt). 017/018 sind Skizzen.
- Specs 001–014 fertig; Impressum/Datenschutz und HTTPS-Zugang live.

## Nächster Schritt
1. Spec 019: PR → CI grün → Merge → Deploy (Nutzer will die Übersicht ohne Login), dann `curl` von außen.
2. `/umsetzen 016` (nur PC, kein Server).
3. M1-Abschluss: 7 Tage ab dem letzten Deploy (Nutzer 2026-11-08); jeder Deploy startet die Uhr neu.
   Bisher nur die zwei Erstkäufe von Buy & Hold: sehr ruhiger Markt, alle Strategien warten auf Bewegung.

## In Arbeit
- Spec: 019 (in Arbeit), 016 (freigegeben)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall; Backups/Snapshot nur nach Anpassung der
  Datenschutzerklärung (Journal mit IP-Adressen, siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
- Beobachtungs-DB wächst ≈ 2 MB/Tag: vor 2027-05 Ausdünnung/Archivierung planen (neues ADR).
- Lizenz des Repos: übernommener Arena-Code steht unter GPL-3.0, das Repo hat keine Lizenzangabe.
- Für Spec 014: Postfach `kontakt@<domain>` bei Migadu erreichbar?, AVV mit Hetzner und Migadu prüfen.
