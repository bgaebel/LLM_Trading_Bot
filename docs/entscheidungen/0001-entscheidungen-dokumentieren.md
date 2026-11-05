# ADR-0001: Tragende Entscheidungen als ADR festhalten; nur per neuem ADR revidieren

- **Status:** angenommen
- **Datum:** 2026-11-05
- **Bereich:** #prozess

## Kontext
Das Projekt wird überwiegend mit Claude Code über viele Sessions und mehrere Geräte entwickelt.
Claude hat kein Gedächtnis zwischen Sessions. Ohne festgehaltene Begründung werden bewusst getroffene
Entscheidungen später als „seltsam" wahrgenommen und „verbessert" – bereits verworfene Alternativen
werden erneut ausprobiert und dieselben Fehler wiederholt.

## Entscheidung
- Jede Entscheidung, die schwer rückgängig zu machen ist oder bei der eine naheliegende Alternative
  bewusst verworfen wurde, wird als ADR in `docs/entscheidungen/` festgehalten.
- Der Index `INDEX.md` wird über `CLAUDE.md` in jede Session geladen und gilt als bindend.
- Angenommene ADRs sind unveränderlich. Revision nur durch ein neues ADR, das das alte ablöst,
  und nur mit Zustimmung des Nutzers.
- Code, der wegen eines ADRs ungewöhnlich aussieht, bekommt einen Kommentar `ADR-NNNN`.

## Verworfene Alternativen
- **Entscheidungen in CLAUDE.md sammeln** – bläht die immer geladene Datei auf; die Begründung
  (Kontext, Alternativen) fehlt oder geht verloren.
- **Entscheidungen nur in PR-Beschreibungen / Issues** – nicht offline greifbar, nicht durchsuchbar
  im Repo, verschwinden bei einem Plattformwechsel.
- **Ein großes DECISIONS.md** – wächst unkontrolliert; Änderungen überschreiben die Historie.

## Konsequenzen
- Positiv: Begründungen bleiben erhalten; Claude kann vor einer Änderung prüfen, ob sie einer Entscheidung widerspricht.
- Negativ: kleiner Mehraufwand pro Entscheidung (durch `/entscheidung` minimiert).
- Neu bewerten, wenn: der Index so lang wird, dass er spürbar Kontext kostet (> ~60 Einträge) –
  dann abgelöste ADRs aus dem Index in ein Archiv verschieben.

## Code-Anker
- –
