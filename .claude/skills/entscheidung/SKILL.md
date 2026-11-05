---
name: entscheidung
description: Hält eine getroffene Entscheidung als ADR fest (oder löst ein bestehendes ADR ab). Nutzen, sobald eine tragende oder nicht naheliegende Entscheidung gefallen ist – Technologie, Architektur, Schnittstelle, Protokoll, bewusst verworfene Alternative.
argument-hint: "<Entscheidung in einem Satz>"
---

# /entscheidung – ADR anlegen

Eingabe: $ARGUMENTS

**Wann ja:** schwer rückgängig zu machen · eine naheliegende Alternative wurde bewusst verworfen ·
jemand würde den Code später sonst „verbessern" · externe Vorgabe prägt das Design.
**Wann nein:** reine Stilfragen (→ Linter/Konventionen), triviale Implementierungsdetails.

1. **Duplikat/Konflikt prüfen:** `docs/entscheidungen/INDEX.md` lesen. Berührt die Entscheidung ein bestehendes
   ADR → es lesen. Widerspricht sie ihm: Das ist eine **Ablösung** und braucht die ausdrückliche Zustimmung
   des Nutzers (Begründung nennen: was hat sich geändert?).
2. **Inhalt aus dem Gespräch ziehen**, nach `docs/entscheidungen/0000-vorlage.md`. Nächste freie Nummer
   (auch auf `origin/main` und anderen Branches prüfen: `git ls-tree -r --name-only origin/main docs/entscheidungen/`).
   - „Verworfene Alternativen" mit Grund ist Pflicht. Kennst du den Grund nicht: Nutzer fragen, nicht erfinden.
   - „Neu bewerten, wenn" ausfüllen – macht spätere Revision sachlich statt beliebig.
   - Datum = heute, Status „angenommen" (bzw. „vorgeschlagen", wenn noch offen).
3. **Index:** Zeile in `docs/entscheidungen/INDEX.md` (Entscheidung als ein knapper Satz).
4. **Ablösung:** Im neuen ADR `Löst ab: ADR-NNNN` setzen. Im alten ADR nur die Statuszeile ändern →
   `abgelöst durch [ADR-NNNN](...)`, im Index ebenso. Inhalt des alten ADRs nicht anfassen.
   Code-Anker des alten ADRs prüfen und anpassen.
5. **Code-Anker:** An Stellen, die wegen der Entscheidung ungewöhnlich aussehen, `// ADR-NNNN: <Kurzgrund>` setzen
   und im ADR auflisten.
6. **Commit:** `docs(adr): NNNN <kurztitel>` – ADR-Dateien und Index in einem eigenen Commit, ohne Code.
   Auf einem Feature-Branch und gilt die Entscheidung unabhängig davon, ob das Feature gemergt wird:
   den Commit zusätzlich nach `main` bringen (`git switch main && git cherry-pick <hash> && git push && git switch -`;
   bei uncommitteten Änderungen vorher `git stash`). Dem Nutzer Nummer + Titel nennen.
