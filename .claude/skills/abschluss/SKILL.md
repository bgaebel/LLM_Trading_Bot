---
name: abschluss
description: Schließt eine umgesetzte Spec ab – unabhängige Prüfung gegen Akzeptanzkriterien und ADRs, Doku nachziehen, Pull Request erstellen, nach grüner CI mergen.
argument-hint: "<Spec-Nummer>"
disable-model-invocation: true
---

# /abschluss – Feature abschließen

Eingabe: $ARGUMENTS  (Spec-Nummer; leer → aktive Spec aus `docs/STAND.md`)

1. **Vollständige Verifikation:** `git fetch`, dann Build, alle Tests, Lint aus `CLAUDE.md`. Befehle und
   Ergebnis (letzte Zeilen der Ausgabe) als Protokoll festhalten.
2. **Unabhängige Prüfung:** Agent-Tool mit `subagent_type: "pruefer"` aufrufen. Mitgeben: Pfad der Spec,
   Diff-Basis `origin/main`, das Verifikationsprotokoll aus Schritt 1. **Nicht** mitgeben: deine Einschätzung,
   was gut oder fertig ist.
3. **Befunde abarbeiten:** Blocker beheben (Commit), danach erneut prüfen lassen. Hinweise: beheben oder
   begründet als Folge-Issue notieren. Nicht erfüllbare AKs dem Nutzer vorlegen – nie stillschweigend streichen.
4. **Doku nachziehen** (ein Commit `docs: ...`):
   - Spec: Abschnitt „Ergebnis" ausfüllen, Status „fertig".
   - `docs/ARCHITEKTUR.md`, falls Struktur/Schnittstellen sich geändert haben.
   - Learnings/ADRs aus dem „Verlauf" übertragen, falls noch nicht geschehen.
   - Ist damit ein Meilenstein komplett: dessen „Fertig, wenn" aus `docs/ROADMAP.md` prüfen, Symbol → ✅,
     Abschnitt in `<details>` einklappen. Beim letzten Meilenstein zusätzlich die Erfolgskriterien aus `docs/PROJEKT.md`.
   - `docs/STAND.md`: nächster Schritt.
5. **Pull Request:** `git push`, dann `gh pr create` nach `.github/pull_request_template.md`:
   Titel als Conventional Commit, `Closes #NN` (falls Issue), Link zur Spec, Nachweise (Befehle + Ergebnis),
   offene manuelle Prüfungen. Ohne `gh`: Branch pushen und dem Nutzer den Link zum PR-Formular nennen.
6. **CI abwarten:** `gh pr checks --watch`. Rote Checks wie Blocker behandeln (Ursache beheben, nicht übergehen).
7. **Merge nur auf Ansage des Nutzers und nur bei grüner CI:** `gh pr merge --squash --delete-branch`,
   dann `git switch main && git pull --ff-only`.
8. **Bericht:** PR-Link, Prüfergebnis, CI-Status, offene manuelle Prüfungen, Folge-Issues.
