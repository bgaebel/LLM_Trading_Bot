# ADR-0029: Strategie-Labor wie ADR-0026; Ausführungsvarianten ohne Regeländerung brauchen keine Hürden

- **Status:** angenommen
- **Löst ab:** [ADR-0026](0026-strategie-labor.md)
- **Datum:** 2026-11-08
- **Bereich:** #strategie #auswertung #betrieb

## Kontext
ADR-0026 legt das Strategie-Labor fest und erlaubt neue Strategieversionen nur über Hürden aus dem Basisbericht
(Spec 018) und nur als eigenes Paper-Depot. Der Nutzer will BTC über das günstigere Paar BTC/USDC handeln
(2026-11-08). Dabei bleiben alle Regeln von v0.3.1 gleich; nur der Ausführungsweg und damit die Kosten ändern sich.
Die Hürden schützen vor Zufallstreffern aus vielen getesteten Varianten – eine Ausführungsvariante entsteht aber nicht
aus der Datensuche im Labor.

## Entscheidung
- **Alles aus ADR-0026 gilt weiter:** Labor nur auf dem PC; Backtests über den unveränderten Live-Pfad mit
  historischem `MarketClient` und Treue-Test; versiegelter Prüfzeitraum ab 2025-10-01; Prüfregister für jeden
  Versuch; GPT nur live bewertet; Labordaten außerhalb von Repo und Dropbox; Kopien der Bot-Daten nur mit Zustimmung.
- **Neue Strategieversionen mit geänderten Regeln oder Parametern** gehen weiter nur über die Hürden aus Spec 018
  und nur als eigenes Paper-Depot neben v0.3.1 nach Freigabe des Nutzers (ADR-0007; ADR-0004 bleibt).
- **Neu – Ausführungsvarianten:** Eine Version, die die Regeln von v0.3.1 unverändert lässt und nur Markt, Weg oder
  Kosten der Ausführung ändert (z. B. `v0.3.1-usdc`), darf ohne diese Hürden als eigenes Paper-Depot laufen, wenn
  der Nutzer sie freigibt. Sie bekommt eine eigene Version, eigene Tests und eine Laborvariante; die v0.3.1-Depots
  bleiben unverändert daneben.

## Verworfene Alternativen
- **Hürden auch für Ausführungsvarianten:** schützt vor keinem Mehrfachtest-Risiko und verzögert einen reinen
  Kostenvorteil bis nach Spec 018.
- **Bestehende Depots umstellen:** mischt die Handelshistorie (CLAUDE.md: nie still migrieren).

## Konsequenzen
- Positiv: günstigere Ausführung ist sofort im Paper-Vergleich sichtbar; v0.3.1 bleibt die Referenz.
- Negativ / Preis: mehr Depots; die Ausnahme muss eng bleiben (nur Ausführung, keine Regeln).
- Neu bewerten, wenn: eine „Ausführungsvariante“ doch Regeln ändern will, oder Echtgeld kommt (M2).

## Code-Anker
- `src/llm_trading_bot/lab/` (Spec 016–018), USDC-Depots: Spec 020
