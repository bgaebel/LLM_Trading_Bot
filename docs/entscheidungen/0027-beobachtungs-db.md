# ADR-0027: Zusätzliche Live-Aufzeichnung in einer eigenen Beobachtungs-DB

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-07
- **Bereich:** #paper #sqlite #auswertung

## Kontext
Für das Strategie-Labor (ADR-0026) fehlen in `bot.db` Daten, die sich später nicht mehr beschaffen lassen:
Bid/Ask mit Tiefe je Runde (Bitvavo hat keine Orderbuch-Historie), was Bitvavo je Runde an Kerzen lieferte, die
Rohantwort von GPT und sein ungekapptes Ziel, der Auslöser einer GPT-Abfrage, Strategieversion und Release je
Entscheidung sowie Runden ohne Entscheidung mit Grund (bisher nur im Journal, 15 Tage). `bot.db` hat Schema v2; jede
Erweiterung erhöht die Version und braucht eine ausdrückliche Migration (ADR-0012) – die Handelshistorie darf nie still
migriert werden.

## Entscheidung
- Der Dienst schreibt diese Beobachtungen in eine **eigene Datei** `observations.db` im Datenverzeichnis, mit eigener
  Schemaversion, nur anhängend. `bot.db` bleibt unverändert bei v2.
- **Schreiber ist nur der Inhaber der `bot.db`-Schreibsperre** (der Dienst); keine zweite Sperre. `paper-cycle`
  (ADR-0016) schreibt keine Beobachtungen; seine Entscheidungen gelten im Labor als nicht aufgezeichnet. Alle anderen
  lesen mit `mode=ro`.
- **Beobachtung blockiert nie den Handel:** Während der Runde wird nur in den Speicher gepuffert; geschrieben wird
  danach, außerhalb jeder `bot.db`-Transaktion. Jeder Fehler (Sperre, Version, Beschädigung) schaltet nur die
  Aufzeichnung ab und wird gezählt; Exit-Code, Rundenergebnis und Gesundheitsprüfung bleiben unberührt. Was ins
  Handeln eingeht (Snapshot, Kontext, Entscheidung, Order), ändert sich nicht – Golden-Fixtures und `PROMPT_VERSION`
  bleiben gleich.
- **Lebenszyklus:** angelegt nur, wenn die Datei fehlt; eine andere Version wird verweigert und nie migriert; eine
  beschädigte Datei wird nie neu angelegt; jeder Dienststart schreibt eine Startmarke.
- Die Beobachtungs-DB enthält **keine personenbezogenen Daten** und wird in einem eigenen Sicherungsprofil täglich
  gesichert; ein Fehler dort berührt nie die Sicherung von `bot.db` oder einen Deploy.

## Verworfene Alternativen
- **Schema v3 von `bot.db` mit neuen Tabellen:** Migration der Handelshistorie für reine Messdaten.
- **Zusätzliche Schlüssel in `decisions.inputs`:** deckt Runden ohne Entscheidung und Quotes je Runde nicht ab.
- **Eigene Sperre für die Beobachtungs-DB:** Exit 3 beendet den Dienst dauerhaft (`RestartPreventExitStatus=3`).
- **Nur Journal:** 15 Tage Aufbewahrung, nicht abfragbar.

## Konsequenzen
- Positiv: Kosten- und GPT-Auswertung auf echten Daten; Datenausfälle messbar; exakter Treue-Test möglich.
- Negativ / Preis: eine zweite DB-Datei (Sicherung, Speicher; Größe wird in Spec 015 gemessen).
- Neu bewerten, wenn: M2 – dann gehören Orderdaten in die Handels-DB, nicht in die Beobachtungen.

## Code-Anker
- geplant: `src/llm_trading_bot/paper/observations.py` (Spec 015)
