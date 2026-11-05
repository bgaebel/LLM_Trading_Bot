---
name: ende
description: Session beenden bzw. Gerätewechsel vorbereiten – Wissen sichern, STAND.md überschreiben, committen, pushen.
disable-model-invocation: true
---

# /ende – Session übergeben

Ziel: Eine neue Session auf einem anderen Gerät kann allein aus dem Repo nahtlos weitermachen.

1. **Wissen sichern (vor allem anderen):** Gehe die Session durch:
   - Wurde etwas entschieden, das nicht als ADR existiert? → ADR anlegen (wie `/entscheidung`).
   - Hat etwas überraschend Zeit gekostet? → Learning anlegen (wie `/lernen`).
   - Aktive Spec: Plan-Häkchen und „Verlauf" aktuell? Aktive Debug-Sitzung: Hypothesentabelle aktuell,
     Status `pausiert`, nächste sinnvolle Hypothese notiert?
   Im Zweifel kurz beim Nutzer nachfragen statt raten.
2. **`docs/STAND.md` überschreiben** (nicht anhängen, max. ~40 Zeilen): Datum, Branch, Meilenstein, Fokus,
   **nächster Schritt so konkret, dass er ohne Rückfrage ausführbar ist** (Datei, Funktion, Befehl),
   Blocker, „Offen – manuell prüfen".
3. **Commit** (STAND.md auf dem aktuellen Branch):
   - Auf einem Feature-Branch: alles committen; unfertiger Code als `wip: <was>` (wird beim Squash-Merge zusammengefasst).
   - Auf `main`: nur Doku und abgeschlossene kleine Änderungen. Unfertige Code-Änderungen auf `main` →
     Nutzer fragen, ob ein Branch angelegt werden soll (`git switch -c feat/<kurzname>`).
   - Nie Dateien committen, die nach Secrets aussehen (`.env`, `secrets.h`, `*.key`, Tokens) – stattdessen melden.
4. **Push:** `git push` (bei neuem Branch `git push -u origin HEAD`). Schlägt der Push fehl: Ursache melden,
   nicht mit `--force` lösen.
5. **Bericht (3–5 Zeilen):** was gesichert wurde, Commit-Hash, Push-Status, nächster Schritt.
   Steht die Arbeit auf einem Feature-Branch, ausdrücklich nennen: „Auf dem anderen Gerät: `/start`
   (findet den Branch `<name>`)".
