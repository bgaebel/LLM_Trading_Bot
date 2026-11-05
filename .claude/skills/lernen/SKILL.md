---
name: lernen
description: Hält eine teuer erkaufte Erkenntnis in docs/LEARNINGS.md fest – Bug-Ursache, Hardware-Eigenheit, Toolchain-Falle, Dinge die nicht funktionieren. Nutzen, sobald etwas Überraschendes Zeit gekostet hat, nicht erst am Session-Ende.
argument-hint: "<Erkenntnis>"
---

# /lernen – Learning festhalten

Eingabe: $ARGUMENTS

1. **Duplikat prüfen:** `docs/LEARNINGS.md` nach Stichworten/Tags greppen. Existiert ein ähnlicher Eintrag:
   ergänzen statt neu anlegen. Ist ein alter Eintrag dadurch überholt: ~~durchstreichen~~ + „(überholt JJJJ-MM-TT: Grund)".
2. **Eintrag schreiben** im Format aus dem Dateikopf, im passenden Abschnitt, neueste oben:
   Symptom → Ursache → Lösung/Regel, max. ~6 Zeilen, 1–3 `#tags` (Bereich, Bauteil, Werkzeug).
   Tags kleingeschrieben und wiederverwendbar – vorhandene Tags bevorzugen.
   Quelle angeben (Spec, Debug-Protokoll, Commit), Datum = heute.
   Reine Datenblatt-/Errata-Fakten → `docs/hardware/README.md`; hier nur die Erfahrung damit, mit Link dorthin.
3. **Eskalation prüfen:**
   - Würde Wiederholen Schaden anrichten (Hardware, Daten, Kunde)? → zusätzlich eine Zeile unter
     „Projektspezifische Regeln" in `CLAUDE.md` vorschlagen.
   - Ist es eigentlich eine Entscheidung („wir machen X nicht mehr, weil …")? → `/entscheidung`.
   - Ist eine Code-Stelle betroffen? → `// LEARNING(#tag): <Kurztitel exakt wie im Eintrag>` dort setzen.
   - Ist es ein Fehler im Werkzeug/in der Bibliothek, der sich reproduzieren lässt? → als Issue/Upstream-Report vorschlagen.
4. **Commit:** `docs(learning): <kurztitel>` – eigener Commit, ohne Code. Auf einem Feature-Branch und gilt die
   Erkenntnis unabhängig vom Feature: zusätzlich nach `main` cherry-picken (wie bei `/entscheidung`).
