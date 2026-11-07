# Spec 018: Strategie-Labor IV – Kandidaten, Prüfhürden und Schatten-Depots

- **Status:** Entwurf
- **Hinweis:** Skizze; wird nach dem Basisbericht (Spec 016) ausgearbeitet und erst dann zur Freigabe vorgelegt.
- **Issue:** – · **Branch:** `feat/s018-kandidaten` · **Meilenstein:** M3 (vorgezogen: Strategie-Labor)
- **Erstellt:** 2026-11-07

## Ziel
Verbesserte Strategien entstehen nachvollziehbar: als neue Version, entwickelt nur auf Daten vor 2025-10-01, einmal
gegen den versiegelten Zeitraum geprüft – und laufen erst danach als eigenes Paper-Depot neben v0.3.1.

## Bausteine (Skizze)
- **Versionierte Strategien:** Register Name → (Funktion, Zeitrahmen, Version) statt fester Kette in
  `run_strategy`; v0.3.1 bleibt unverändert und golden-geprüft; neue Depots wie `trend-v0.4` mit eigener Version.
- **Schnellpfad** für Parametersuchen (Indikatoren je Snapshot einmal), golden-gleich zum Live-Pfad (ADR-0026).
- **Forschungsprotokoll:** Entwicklung nur vor 2025-10-01 (verankerter Walk-Forward oder gereinigte
  Kreuzvalidierung mit Sperrzone); Versuchsprotokoll nach der Arena-Vorlage `experiments/templates/experiment.md`;
  ein vorab festgelegter Mehrfachtest (Holm/BHY über das Register **oder** SPA – einer, nicht wahlweise).
- **Zwei Stufen** (Schwellen werden erst nach dem Basisbericht festgelegt, an einer Stelle, mit Aussage, welche
  Effektgröße mit den vorhandenen Jahren überhaupt messbar ist):
  1. **Paper-Schatten-Depot:** leichte Hürde, z. B. Deflated Sharpe auf den Entwicklungsdaten, keine Verschlechterung
     gegen v0.3.1 im versiegelten Zeitraum, Kosten-Gleichstand über den Modellkosten.
  2. **Echtgeld (M2):** strenge Hürden (t ≥ 3, PBO ≤ 0,2, ≥ 100 Round-Trips, Ergebnis bleibt positiv ohne die beste
     Marktphase).
- **Schatten-Depot:** live als Paper-Depot; Abgleich gegen die erwartete Verteilung aus dem Backtest; Freigabe je
  Kandidat durch den Nutzer.

## Kandidaten-Ideen
- Lücken-tolerante Gültigkeitsprüfung (Diagnose aus Spec 016 zeigt den Wert): Heute sperrt eine fehlende 1h-Kerze
  beide Märkte ~60 h, eine fehlende 4h-Kerze ~10 Tage; ETH verliert ~8–10 % der Entscheidungen durch 1m-Lücken.
- Marktphasen-Filter, nur mit rückblickend berechneten Phasen.
- Volatilitäts-normierte Positionsgröße statt fester 25/45 %.
- Weniger Umschlag (Mindesthaltedauer, Hysterese an den Schwellen).
- Kostenmodell mit Maker-Orders (Vorbereitung M2; im Paper-Betrieb nur als Annahme).

## Abhängigkeiten
- Spec 015 (Aufzeichnung), Spec 016 (Archiv, Backtest, Register), Spec 017 (Signal-Auswertung).
