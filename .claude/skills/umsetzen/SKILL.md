---
name: umsetzen
description: Setzt eine freigegebene Spec Schritt für Schritt um – Branch, je Schritt implementieren, verifizieren, committen, Plan abhaken.
argument-hint: "<Spec-Nummer>"
---

# /umsetzen – Spec umsetzen

Eingabe: $ARGUMENTS  (Spec-Nummer; leer → die Spec, die in `docs/STAND.md` als in Arbeit steht)

## Vorbereitung
1. Spec lesen (inkl. „Verlauf" – bei Wiederaufnahme dort weitermachen). Status muss „freigegeben" oder
   „in Arbeit" sein; „Entwurf" → erst Freigabe holen.
2. In der Spec genannte ADRs und Learnings lesen.
3. Branch: den in der Spec genannten (`feat/<issue>-<kurzname>` bzw. `feat/s<NNN>-<kurzname>`) auschecken
   oder neu von aktuellem `main` anlegen. Spec-Status → „in Arbeit".

## Schleife je Plan-Schritt
1. Implementieren – nur was der Schritt verlangt. Bei neuem Verhalten: Test zuerst oder im selben Schritt.
2. Verifizieren mit den Befehlen aus `CLAUDE.md` (Build, Test, Lint). Rot → beheben, bevor es weitergeht.
3. Commit (Conventional Commit, Englisch), Häkchen in der Spec im selben Commit.
4. Kurz melden (1–2 Zeilen), dann nächster Schritt – nicht nach jedem Schritt auf Bestätigung warten,
   außer der Nutzer will das.

## Anhalten und fragen, wenn …
- ein Akzeptanzkriterium nicht erreichbar ist oder geändert werden müsste,
- die Umsetzung einem ADR widersprechen würde (Optionen nennen, nicht still abweichen),
- der Umfang deutlich wächst (→ Folge-Issue statt Scope-Creep),
- ein Schritt Hardware/manuelle Prüfung braucht (genau sagen, was der Nutzer tun und messen soll).

## Nebenbei festhalten
- Abweichungen vom Plan und Überraschungen → Abschnitt „Verlauf" der Spec (datiert, knapp).
- Entscheidung getroffen → `/entscheidung`. Etwas hat Zeit gekostet → `/lernen`.
- Ungewöhnlicher Code wegen ADR/Learning → Kommentar `// ADR-NNNN: …` bzw. `// LEARNING(#tag): <Titel>` an der Stelle.
- Derselbe Fehler nach 2 Fix-Versuchen nicht weg → aufhören zu raten, auf `/diagnose` umschalten.

## Ende
Alle Schritte erledigt → `docs/STAND.md` aktualisieren, pushen, `/abschluss` vorschlagen.
