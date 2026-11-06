# ADR-0016: Weboberfläche liest die Bot-DB nur lesend – genau ein Schreiber, Leser erlaubt

- **Status:** angenommen
- **Datum:** 2026-11-06
- **Bereich:** #web #persistenz #betrieb

## Kontext
Die Weboberfläche (Spec 006, ADR-0015) soll eine Startseite mit Depot-Übersicht bekommen (Spec 007): Equity,
Positionen, Vergleich mit Buy & Hold, letzte Entscheidungen, GPT-Kosten, Trades, Lebenszeichen des Bots. Alle
Daten liegen in der SQLite-DB des Bots (Schema v2, WAL). `CLAUDE.md` sagt „nie zwei Prozesse auf derselben DB“;
gemeint ist der Schutz der Handelshistorie durch genau einen Schreiber (`WriterLock`, `write_tx`). Die
Weboberfläche ist ein eigener Prozess. Der Nutzer hat am 2026-11-06 den direkten, nur lesenden Zugriff gewählt.

## Entscheidung
- Genau **ein schreibender** Prozess je Bot-DB (unverändert: `paper-cycle`, später der Dienst, mit `WriterLock`).
- Die Weboberfläche darf die DB **nur lesen**: Verbindung über die URI `file:<pfad>?mode=ro`, zusätzlich
  `PRAGMA query_only=1`, kurze `busy_timeout`, je Seitenaufruf eine Verbindung, die danach geschlossen wird.
  Sie nimmt nie den `WriterLock`, legt keine DB an (fehlt die Datei: „noch keine Daten“) und migriert nie.
- Vor dem Lesen gilt dieselbe Schemaprüfung wie im Bot (`check_schema`); eine andere Version wird angezeigt,
  nicht angefasst.
- Die Regel in `CLAUDE.md` wird nach Annahme präzisiert: „nie zwei **schreibende** Prozesse auf derselben DB;
  zusätzliche Prozesse nur lesend (ADR-0016)“.

## Verworfene Alternativen
- **Status-Datei vom Bot (`status.json`):** hält die Regel wörtlich ein, zeigt aber nur, was der Zyklus
  exportiert (begrenzte Historie), und braucht Zusatzcode im Schreibpfad. Vom Nutzer verworfen.
- **Weboberfläche im selben Prozess wie der Bot:** koppelt Webserver-Fehler an den Handelszyklus; der Dienst
  (Roadmap Nr. 5) gibt es noch nicht.
- **Lesen ohne `mode=ro`:** ein Programmierfehler könnte schreiben oder die DB anlegen.

## Konsequenzen
- Positiv: volle Historie sichtbar, kein Eingriff in den Schreibpfad; SQLite-WAL erlaubt Lesen neben dem
  Schreiber, der Leser sieht immer einen abgeschlossenen Stand.
- Negativ: Ein WAL-Leser braucht Schreibrecht auf die `-shm`-Datei bzw. das DB-Verzeichnis; der Web-Prozess muss
  daher unter dem Dienstbenutzer (ADR-0006) oder mit passenden Rechten laufen. Lang laufende Lesetransaktionen
  können den WAL-Checkpoint verzögern – darum kurze Verbindungen je Aufruf.
- Neu bewerten, wenn: die Weboberfläche etwas am Bot auslösen soll (Start/Stopp, Not-Aus) oder ein Echtgeld-Betrieb
  (M2) dazukommt.

## Code-Anker
- `src/llm_trading_bot/web/readonly.py` (geplant, Spec 007)
