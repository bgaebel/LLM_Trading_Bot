---
name: spec
description: Plant ein Feature oder eine größere Änderung als Spec mit prüfbaren Akzeptanzkriterien und Schritt-Plan – bevor Code geschrieben wird.
argument-hint: "<Feature oder Issue-Nummer>"
---

# /spec – Feature planen

Eingabe: $ARGUMENTS  (Freitext oder `#NN` für ein GitHub-Issue)

Ergebnis: `docs/specs/NNN-<kurzname>.md` nach `docs/specs/_VORLAGE.md`, vom Nutzer freigegeben. **Kein Produktivcode.**

1. **Nummer:** nächste freie dreistellige Nummer in `docs/specs/`. Bei `#NN`: `gh issue view NN` lesen.
2. **Kontext sammeln (selbst, ohne Rückfrage):**
   - `docs/entscheidungen/INDEX.md` → relevante ADRs vollständig lesen.
   - `docs/LEARNINGS.md` nach den betroffenen Bereichen greppen.
   - `docs/ARCHITEKTUR.md` und den betroffenen Code. Bei breiter Suche einen Explore-Subagenten nutzen.
   - `docs/PROJEKT.md` → Nicht-Ziele. Widerspricht das Feature einem Nicht-Ziel: zuerst ansprechen.
3. **Klären:** Nur fragen, was das Ergebnis ändert – vor allem Akzeptanzkriterien und Abgrenzung. Gebündelt,
   mit Vorschlägen. Gibt es mehrere sinnvolle Ansätze: einen empfehlen, Alternativen in einem Satz nennen.
4. **Spec schreiben:**
   - Akzeptanzkriterien: jedes mit konkretem Nachweis (Testname, Befehl, Messung). Manuelle mit `[manuell]`.
   - Plan: Schritte, nach denen jeweils Build+Tests grün sind und ein Commit entsteht. Test zuerst, wo sinnvoll.
   - „Relevante Entscheidungen & Learnings" ausfüllen – das ist der Schutz gegen Wiederholungsfehler.
   - Fällt im Ansatz eine tragende Entscheidung: als ADR-Entwurf (Status „vorgeschlagen") mit anlegen.
5. **Einordnen:** Zeile in `docs/ROADMAP.md` (ohne Status – der steht nur in der Spec). Issue nur, wenn der
   Nutzer mit Issues arbeitet oder es extern sichtbar sein soll: `gh issue create --title ... --body "Spec: docs/specs/NNN-....md" --label type:feature`
   – vorher kurz bestätigen lassen. Branch-Name in der Spec: `feat/<issue>-<kurzname>`, ohne Issue `feat/s<NNN>-<kurzname>`.
6. **Freigabe:** Spec in 5–10 Zeilen zusammenfassen (Ziel, AKs, Anzahl Schritte, Risiken) und um Freigabe bitten.
   Nach Freigabe: Status „freigegeben", vorgeschlagene ADRs → „angenommen" + Index, `docs/STAND.md` aktualisieren,
   Commit `docs(spec): add NNN <kurzname>` auf `main`. Nächster Schritt: `/umsetzen NNN`.

## Spec verwerfen
Wird eine Spec (auch später, während der Umsetzung) aufgegeben: Status „verworfen" + ein Satz Begründung im
„Ergebnis". Zugehörige ADRs mit Status „vorgeschlagen" → „verworfen". Existiert schon ein Branch: vor dem Löschen
`git log main..<branch> --oneline --grep='^docs(adr' --grep='^docs(learning'` prüfen und Treffer per `git cherry-pick` nach
`main` holen – gerade Erkenntnisse aus einem gescheiterten Ansatz sind wertvoll. Erst dann den Branch löschen.
