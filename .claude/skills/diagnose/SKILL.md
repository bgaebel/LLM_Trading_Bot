---
name: diagnose
description: Systematische Fehlersuche mit persistentem Protokoll (Hypothesen, Tests, Ergebnisse), das über Sessions hinweg verhindert, dass Verworfenes erneut getestet wird. Nutzen bei Bugs, die nicht beim ersten oder zweiten Versuch verschwinden.
argument-hint: "<Symptom> | <Debug-Nummer zum Fortsetzen>"
---

# /diagnose – Fehler systematisch eingrenzen

Eingabe: $ARGUMENTS

## Start
1. **Vorwissen prüfen:** `docs/debug/` nach offenen/ähnlichen Sitzungen durchsuchen (Fortsetzen statt neu),
   `docs/LEARNINGS.md` nach passenden Tags greppen. Gibt es eine passende Sitzung: deren Hypothesentabelle
   ist die Ausgangslage – widerlegte Hypothesen werden **nicht** erneut getestet, außer sich hat
   nachweislich etwas geändert (dann begründen).
2. **Protokoll anlegen:** `docs/debug/NNN-<kurzname>.md` nach `_VORLAGE.md`. In `docs/STAND.md` als offene
   Debug-Sitzung eintragen.

## Vorgehen
1. **Symptom präzisieren:** exakte Meldung, Reproduktion, Häufigkeit, Umgebung (Commit, Board-Rev, Toolchain).
   Nicht reproduzierbar → zuerst Reproduktion herstellen (Logging, Stresstest, Trigger).
2. **Fakten von Vermutungen trennen.** Fakten nur, was beobachtet/gemessen wurde.
3. **Hypothesen** (mehrere, nach Wahrscheinlichkeit × Prüfaufwand sortiert) in die Tabelle.
4. **Testen:** Pro Test genau eine Variable ändern. Vor dem Test notieren, welches Ergebnis was bedeutet.
   Ergebnis **sofort** in die Tabelle schreiben – auch bei „kein Unterschied" – mit Datum, Commit und HW-Revision.
   Nach jeder Testrunde Protokoll committen und pushen (`docs(debug): NNN <stand>`), damit es auf jedem Gerät
   aktuell ist, auch wenn die Session ohne `/ende` endet.
5. **Hardware/Messungen:** Was nur der Nutzer kann (Oszilloskop, Logic Analyzer, Board tauschen, Spannung messen)
   als konkrete Anleitung formulieren: was anschließen, was messen, welcher Wert bedeutet was.
6. **Nach 3 widerlegten Hypothesen:** Schritt zurück – Annahmen in „Bekannte Fakten" hinterfragen,
   Suchraum halbieren (git bisect, Modul isolieren, Minimalbeispiel), Datenblatt/Errata/Issue-Tracker der
   Bibliothek prüfen. Bei Bedarf einen Subagenten mit unvoreingenommenem Blick auf das Protokoll ansetzen.

## Abschluss
- **Ursache** (nicht nur Auslöser) und Fix dokumentieren. Regressionstest schreiben, wo möglich.
- Fix committen: `fix(<bereich>): <was> (debug NNN)`.
- Erkenntnis nach `docs/LEARNINGS.md` (wie `/lernen`); gefährlich → Regel in `CLAUDE.md` vorschlagen.
- Status „gelöst", Eintrag in `docs/STAND.md` entfernen.
- Unterbrochen: Status `pausiert` + nächste sinnvolle Hypothese festhalten, damit die nächste Session dort ansetzt.
- Aufgegeben: Status `aufgegeben (Grund)`; Learnings trotzdem übertragen.
