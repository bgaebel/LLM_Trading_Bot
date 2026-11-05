# Debug NNN: {{Symptom in einem Satz}}

- **Status:** offen | pausiert | gelöst | aufgegeben (Grund)
- **Begonnen:** {{JJJJ-MM-TT}} · **Issue:** #{{NN}}
- **Bereich:** {{#tags}}

## Symptom
- **Erwartet:** …
- **Beobachtet:** … (exakte Fehlermeldung / Log / Messwert)
- **Reproduktion:** {{Schritte oder Befehl; wie zuverlässig, z. B. 3/10}}
- **Umgebung:** {{Board-Rev, Firmware-Commit, Toolchain, OS}}

## Bekannte Fakten
<!-- Nur belegt Beobachtetes. Vermutungen gehören in die Hypothesen. -->
- …

## Hypothesen
<!-- Nie löschen. Verworfene bleiben stehen, damit sie in späteren Sessions nicht erneut getestet werden.
     Pro Test genau EINE Variable ändern. "Wann/Stand" macht prüfbar, ob sich seitdem etwas geändert hat. -->
| # | Hypothese | Test (eine Änderung) | Ergebnis | Wann / Stand (Datum, Commit, HW-Rev) | Status |
|---|---|---|---|---|---|
| H1 | {{...}} | {{...}} | {{...}} | {{JJJJ-MM-TT, abc1234, Rev B}} | offen / ❌ widerlegt / ✅ bestätigt |

## Ursache
{{Wenn gefunden: die eigentliche Ursache, nicht nur der Auslöser.}}

## Lösung
- Fix: {{Commit/PR}}
- Regressionstest: {{Test, der den Fehler künftig fängt}}
- Learning übertragen: [ ] `docs/LEARNINGS.md` · [ ] ggf. harte Regel in `CLAUDE.md`
