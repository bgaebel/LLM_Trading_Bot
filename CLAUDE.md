# {{PROJEKTNAME}}

{{Ein Satz: Was ist das, für wen, welches Problem löst es.}}
Details: `docs/PROJEKT.md` · Aufbau: `docs/ARCHITEKTUR.md` · Plan: `docs/ROADMAP.md`

<!-- Diese Datei wird in JEDER Session vollständig geladen. Halte sie unter ~120 Zeilen.
     Alles, was nur manchmal gebraucht wird, gehört nach docs/ und wird hier nur verlinkt. -->

## Befehle

Stack: {{z. B. ESP32 / ESP-IDF 5.5 / C}} · Shell: {{Bash-Tool | PowerShell-Tool – womit die Befehle laufen}}

| Zweck | Befehl |
|---|---|
| Build | `{{...}}` |
| Test (Host) | `{{...}}` |
| Lint/Format | `{{...}}` |
| Flash / Run | `{{...}}` |
| Monitor / Logs | `{{...}}` |

„Fertig" heißt: Build + Tests + Lint grün. Schritte, die echte Hardware oder einen Menschen brauchen,
benenne ich ausdrücklich als **offen – manuell prüfen** und behaupte sie nie als erledigt.
Flashen nur mit angeschlossenem Board und Zustimmung; Monitor/Run-Befehle, die nicht von selbst enden, nur mit Timeout.

## Arbeitsweise

1. **Ziel vor Code.** Bei allem, was über eine kleine Änderung hinausgeht: erst `/spec`, dann `/umsetzen`.
   Kleine Änderungen (ein Fix, wenige Dateien, klares Ergebnis) direkt erledigen.
2. **Entscheidungen respektieren.** ADRs mit Status „angenommen" (Liste unten) sind bindend. Bevor ich etwas ändere,
   das eine Entscheidung berührt, lese ich das ADR. Will ich davon abweichen: Begründung nennen,
   den Nutzer fragen, bei Zustimmung ein neues ADR anlegen, das das alte ablöst (`/entscheidung`).
   Nie still revidieren.
3. **Code-Anker beachten.** `// ADR-NNNN: …` oder `// LEARNING(#tag): <Titel>` im Code markiert absichtlich
   so gebauten Code. Nicht „aufräumen", ohne das Referenzierte gelesen zu haben
   (Learning finden: `grep -n "<Titel>" docs/LEARNINGS.md`).
4. **Vor Arbeit in einem Bereich** `docs/LEARNINGS.md` nach dem Bereich durchsuchen (grep nach dem
   Tag, z. B. `#uart`). Bei Fehlersuche zusätzlich `docs/debug/` prüfen, damit verworfene
   Hypothesen nicht noch einmal getestet werden.
5. **Wissen sofort sichern.** Eine getroffene Entscheidung → `/entscheidung`. Eine teuer erkaufte
   Erkenntnis (Bug-Ursache, Hardware-Eigenheit, Toolchain-Falle) → `/lernen`. Nicht bis Session-Ende warten.
6. **Verifizieren statt behaupten.** Ergebnis nur melden, wenn ein Befehl es belegt; Ausgabe zitieren.
7. **Kleine, atomare Commits** (Conventional Commits, Englisch): `feat(uart): add DMA rx`.

## Git & GitHub

- `main` ist immer baubar.
- Features und Specs: Branch `feat/<issue>-<kurzname>` (ohne Issue: `feat/s<spec-nr>-<kurzname>`), Bugs `fix/...`
  → PR → CI grün → Squash-Merge (`/abschluss`).
- Kleine Fixes und Doku dürfen direkt auf `main`.
- `docs/STAND.md` lebt auf dem Branch, auf dem gerade gearbeitet wird. Konflikt beim Merge → Branch-Version gewinnt.
- ADRs/Learnings, die unabhängig vom Feature gelten, zusätzlich nach `main` bringen (siehe `/entscheidung`, `/lernen`),
  damit sie nicht mit einem verworfenen Branch verloren gehen.
- Status eines Features steht nur in seiner Spec. Issues sind das Backlog (optional für Solo-Arbeit);
  `docs/ROADMAP.md` ordnet Specs/Issues in Meilensteine. PRs verlinken ihr Issue (`Closes #NN`).
- Nie: Force-Push auf `main`, Secrets committen, Hooks mit `--no-verify` umgehen.

## Sprache & Stil

- Mit dem Nutzer und in `docs/`: Deutsch.
- Code, Bezeichner, Code-Kommentare, Commit-Messages, PR-Titel: Englisch.
- Kommentare erklären das *Warum*, nicht das *Was*.

## Sessions

- Start: `/start` (pull, Stand laden, nächsten Schritt vorschlagen).
  Ein Hook zeigt beim Start automatisch Branch, Änderungen und letzte Commits.
- Ende / Gerätewechsel: `/ende` (STAND.md aktualisieren, commit, push). Ohne Push ist nichts übergeben.
- Nach einem Compact: `docs/STAND.md` und die aktive Spec neu lesen, bevor ich weiterarbeite.

## Dokumente: was wo steht

| Datei | Inhalt | Wann aktualisieren |
|---|---|---|
| `docs/STAND.md` | Aktueller Fokus, nächster Schritt, Blocker (max. ~40 Zeilen, wird überschrieben) | Session-Ende, Fokuswechsel |
| `docs/entscheidungen/` | ADRs, unveränderlich nach Annahme | bei jeder tragenden Entscheidung |
| `docs/LEARNINGS.md` | Erfahrungen: Symptom → Ursache → Regel, mit #tags | sofort, wenn etwas Zeit gekostet hat |
| `docs/specs/NNN-*.md` | Ziel, Akzeptanzkriterien, Plan, Ergebnis je Feature | `/spec`, während `/umsetzen`, `/abschluss` |
| `docs/debug/NNN-*.md` | Fehlersuche-Protokoll (Hypothesen, Ergebnisse) | während `/diagnose` |
| `docs/ARCHITEKTUR.md` | Module, Schnittstellen, Datenfluss | wenn sich die Struktur ändert |
| `docs/PROJEKT.md` | Ziel, Nicht-Ziele, Randbedingungen, Glossar | selten, nur mit Nutzer |
| `docs/hardware/` | Fakten: Pinout, Datenblatt-Notizen, Errata (nur Firmware) | bei Hardware-Fakten; Erfahrung damit → LEARNINGS mit Link |

## Projektspezifische Regeln

<!-- Harte Regeln, deren Verletzung Schaden anrichtet. Kurz halten. Beispiele:
- Nie `idf.py erase-flash` ohne Rückfrage – auf dem Board liegen Kalibrierdaten.
- Interrupt-Handler allokieren keinen Speicher (ADR-0004). -->
- {{...}}

## Aktueller Stand

@docs/STAND.md

## Entscheidungen (angenommene sind bindend)

@docs/entscheidungen/INDEX.md
