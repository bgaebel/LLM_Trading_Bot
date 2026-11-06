# ADR-0008: Eigenes versioniertes SQLite-Schema; ein aktiver Agent plus Buy & Hold

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-06
- **Bereich:** #persistenz #paper

## Kontext
Die Arena hält sieben Agenten (GPT, Claude, vier klassische, Buy & Hold) in einer SQLite-DB mit
`user_version = 0` und vielen Tabellen für den Vergleichsbetrieb. Der Bot ist kein Vergleichsaufbau: Er fährt
eine Strategie (in M1 die LLM-Strategie, bis dahin `trend`) und soll sich gegen Buy & Hold messen
(Erfolgskriterium in PROJEKT.md). Eine Arena-Historie wird nicht übernommen.

## Entscheidung
- Eigenes Schema mit `PRAGMA user_version = 1`. Tabellen: `portfolios`, `decisions` (UNIQUE agent/symbol/bar_ts),
  `trades`, `equity` (UNIQUE ts/agent), `market`. Erweiterungen (z. B. LLM-Budget) erhöhen die Version.
  Ältere oder fremde Versionen werden abgelehnt, es gibt keine automatische Migration.
- Zwei Depots: der **aktive Agent** (konfigurierbar, Standard `trend`) und **Buy & Hold** als Vergleich.
- Spalten, Idempotenzschlüssel und Ausführungslogik folgen der Arena (ADR-0007), damit Ergebnisse vergleichbar bleiben.

## Verworfene Alternativen
- **Arena-Schema 1:1 mit allen Agenten:** macht den Bot zu einer zweiten Arena; mehr laufende LLM-Kosten und Komplexität.
- **Nur ein Depot ohne Buy & Hold:** dann fehlt der Vergleich, den PROJEKT.md verlangt.
- **ORM/automatische Migrationen:** neue Abhängigkeit; Migrationen gefährden die Historie (CLAUDE.md: nie still migrieren).

## Konsequenzen
- Positiv: kleines, prüfbares Schema; Neustart- und Doppel-Schutz über UNIQUE-Schlüssel wie in der Arena.
- Negativ: Arena-DBs lassen sich nicht direkt öffnen; ein späterer Vergleich braucht einen Export.
- Neu bewerten, wenn: der Bot mehrere Strategien parallel fahren soll oder eine Arena-Historie übernommen werden muss.

## Code-Anker
- `src/llm_trading_bot/paper/db.py` (Spec 004)
