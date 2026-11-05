# ADR-0004: Paper-Modus zuerst; Echtgeld bleibt gesperrt bis zu eigener Spec und eigenem ADR

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-05
- **Bereich:** #trading #sicherheit

## Kontext
Ziel ist ein Bot, der später mit echtem Geld über Bitvavo handelt. Fehler in Ausführung, Risiko oder
Konfiguration kosten dann echtes Geld. Die Arena hat bewusst nur Paper Trading.

## Entscheidung
- M1 läuft ausschließlich im **Paper-Modus**. Fehlende Konfiguration bedeutet Paper.
- `TRADING_MODE=live` wird abgelehnt (`NotImplementedError`), bis der Echtgeld-Meilenstein (M2) eine
  angenommene Spec **und** ein neues ADR hat, das dieses ablöst.
- Bis dahin: keine Order-Endpunkte implementieren oder aufrufen, keine Bitvavo-API-Keys mit Handelsrecht
  anlegen oder auf den Server legen.
- Die Ausführung liegt hinter einer Broker-Schnittstelle, damit ein Live-Broker später ohne Änderung an
  Strategie und Risiko ergänzt werden kann.

## Verworfene Alternativen
- **Echtgeld von Anfang an** – Betrieb, Risiko-Grenzen und Wiederanlauf sind noch nicht erprobt.
- **Live per Konfigurationsschalter schon jetzt vorbereiten** – ein falscher Wert könnte echte Orders auslösen.
- **Nur Paper für immer** – entspricht nicht dem Projektziel.

## Konsequenzen
- Positiv: Kein Geldrisiko, solange Betrieb und Strategie erprobt werden.
- Negativ: Echte Ausführungseffekte (Teilausführung, Ablehnung, Latenz) bleiben bis M2 ungetestet.
- Neu bewerten, wenn: M1 abgeschlossen ist und M2 geplant wird.

## Code-Anker
- `src/llm_trading_bot/core.py` (`parse_mode`)
