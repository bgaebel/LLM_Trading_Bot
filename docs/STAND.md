# Stand

**Aktualisiert:** 2026-11-08 · **Branch:** `feat/s016-backtest` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 020 fertig:** USDC-Depots (`v0.3.1-usdc`, ADR-0030) live seit 2026-11-08 16:56 UTC (Release a44ef78) –
  **M1-Uhr läuft ab da** (7 Tage → 2026-11-15 16:56 UTC). Kopfzeile und Laufband sticky, Kacheln durchsichtiger.
- **Spec 016 in Arbeit** (Strategie-Labor, ADR-0029 löst 0026 ab): Archiv (6,6 Mio. Kerzen), Spread-Stichproben
  (991 Std.), historischer Client, Backtest, Treue-Test, Kennzahlen, Register, Bericht gebaut und in fünf
  Prüfrunden korrigiert; gerade: Anpassung an Spec 020 und Laborvariante `usdc` (020 AK8), Doku `docs/labor.md`.
- Specs 001–015, 019 fertig; Übersicht ohne Login (ADR-0028) live.

## Nächster Schritt
1. Spec 016: Integration mit 020 prüfen, dann Basislauf Q2 2021–Q3 2025 (Basis, Kostenraster, Diagnose, usdc),
   Bericht an den Nutzer; Treue-Test braucht eine Kopie von `bot.db`/`observations.db` (nur mit Zustimmung).
2. PR Spec 016, CI, Merge (kein Deploy nötig: das Labor läuft nur auf dem PC).
3. M1-Abschluss frühestens 2026-11-15 16:56 UTC, wenn kein weiterer Deploy nötig wird.

## In Arbeit
- Spec: 016 (in Arbeit); 017/018 Skizzen
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- Zustimmung des Nutzers für eine Kopie der Bot-Daten (Treue-Test, ADR-0029).

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall; Backups/Snapshot nur nach Anpassung der
  Datenschutzerklärung (Journal mit IP-Adressen, siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
- Beobachtungs-DB wächst (jetzt mit USDC-Büchern) ≈ 2–3 MB/Tag: vor 2027-05 Ausdünnung/Archivierung (neues ADR).
- Lizenz des Repos: übernommener Arena-Code steht unter GPL-3.0, das Repo hat keine Lizenzangabe.
- Für Spec 014: Postfach `kontakt@<domain>` bei Migadu erreichbar?, AVV mit Hetzner und Migadu prüfen.
- Gebührenwährung auf BTC/USDC (vermutlich USDC) erst mit Echtgeld bestätigbar.
