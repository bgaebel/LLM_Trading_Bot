---
name: kickoff
description: Richtet ein Projekt aus diesem Template ein – neues Projekt, bestehende Codebasis übernehmen oder GSD-Projekt (.planning/) migrieren. Einmalig nach dem Kopieren des Templates ausführen.
argument-hint: "[kurze Projektbeschreibung]"
disable-model-invocation: true
---

# /kickoff – Projekt einrichten

Ziel: Am Ende sind alle `{{...}}`-Platzhalter ersetzt, Build/Test/Lint-Befehle in `CLAUDE.md` sind **ausgeführt und
funktionieren**, die tragenden Entscheidungen stehen als ADR, und alles ist committet.

Eingabe des Nutzers: $ARGUMENTS

## 1. Lage erkennen (ohne Rückfrage)

- `git status`, `git remote -v`, Verzeichnisinhalt, vorhandene Build-Dateien (`platformio.ini`, `CMakeLists.txt`,
  `sdkconfig`, `prj.conf`/`west.yml`, `*.tsproj`/`*.sln`, `pyproject.toml`, `package.json`, …).
- Modus bestimmen:
  - **A – Neu:** kein Quellcode vorhanden.
  - **B – Bestehende Codebasis:** Code vorhanden, kein `.planning/`.
  - **C – GSD-Migration:** `.planning/` existiert (zusätzlich gilt B, falls Code vorhanden).
- In B/C: Liegen neben den Template-Dateien Vorgängerversionen (`CLAUDE.alt.md`, `*.template`-Dateien, eigene
  `.github/workflows/`, `.claude/settings.json`)? Die werden in Schritt 4–5 **zusammengeführt, nie blind überschrieben**.
- `gh auth status` prüfen. Ist `gh` nicht angemeldet: dem Nutzer `gh auth login` nennen und GitHub-Schritte
  am Ende nachholen – nicht blockieren.

## 2. Bestand erfassen (nur Modus B/C)

Für größere Codebasen einen Explore-Subagenten beauftragen: Stack, Verzeichnisstruktur, Module, Build-/Test-Befehle,
Konventionen, auffällige Stellen. Ergebnis als Entwurf für `docs/ARCHITEKTUR.md`.
Offensichtliche, bereits getroffene Entscheidungen (Framework, RTOS, Sprache, Datenformat, Architekturmuster)
als ADR-Kandidaten sammeln.

### Modus C: GSD-Artefakte übernehmen
`.planning/` kann sehr groß sein: nicht ins eigene Kontextfenster laden, sondern Subagenten je Quelle
beauftragen und nur deren Extrakt verwenden.

| GSD-Quelle | Ziel | Hinweis |
|---|---|---|
| `PROJECT.md`, `REQUIREMENTS.md` | `docs/PROJEKT.md` | auf Ziel, Kernwert, Nicht-Ziele, Randbedingungen kürzen |
| `codebase/*.md` (ARCHITECTURE, STACK, CONVENTIONS, CONCERNS) | `docs/ARCHITEKTUR.md` | IST-Zustand; Concerns → Issues oder ROADMAP „Ideen" |
| Entscheidungen in `PROJECT.md`/`STATE.md`/Phasen-`CONTEXT.md` | ADRs | siehe ADR-Regel unten |
| `ROADMAP.md`, `MILESTONES.md`, offene `todos/` | `docs/ROADMAP.md` | erledigte Phasen nur als einzeilige Zusammenfassung (eingeklappt) |
| aktive Phase (`phases/*/PLAN.md` mit offenen Tasks) | `docs/specs/001-*.md`, Status „in Arbeit" | Ziel, AKs, Rest-Plan übernehmen – sonst geht laufende Arbeit verloren |
| `STATE.md`, `HANDOFF.json` | `docs/STAND.md` | nur aktueller Fokus, Blocker, nächster Schritt |
| `debug/` | `docs/debug/` | offene Sitzungen ins neue Format (Status `pausiert`); gelöste → Ursache als Learning |
| `RETROSPECTIVE.md`, Summaries, Fallstricke-Notizen | `docs/LEARNINGS.md` | nur Erkenntnisse, die noch gelten |
| alte `CLAUDE.md` (bzw. `CLAUDE.alt.md`) | neue `CLAUDE.md` | nur harte Projektregeln und Befehle; GSD-Workflow, Session-Protokolle und Stack-Recherche **nicht** |

**ADR-Regel bei der Migration:** Nur tragende Entscheidungen bekommen ein eigenes ADR (Faustregel ≤ 10).
Der Rest geht als Liste in ein Sammel-ADR „Übernommene GSD-Entscheidungen". Fehlende Alternativen/Begründungen
als „nicht überliefert" kennzeichnen – den Nutzer nur bei den tragenden fragen, nicht bei jeder einzelnen.

Audits, Research-Dateien und abgeschlossene Phasenpläne **nicht** migrieren. Danach dem Nutzer empfehlen:
`git tag gsd-final` (Stand bleibt per `git show gsd-final:.planning/<datei>` erreichbar), dann `.planning/` löschen.
Nicht nach `docs/` archivieren – das verschmutzt jede spätere Suche.

**GSD-Reste außerhalb des Projekts:** Ist GSD global installiert (`~/.claude/skills/gsd-*`, `~/.claude/agents/gsd-*`,
GSD-Hooks in `~/.claude/settings.json`), dem Nutzer melden: Die Skills bleiben sonst in jedem Projekt aktiv,
kosten Kontext und können wieder `.planning/` anlegen. Deinstallation nur auf ausdrückliche Zustimmung.
Projekteigene GSD-/Session-Skills unter `.claude/skills/` (z. B. `session-start`, `session-ende`) entfernen –
ersetzt durch `/start` und `/ende`. Projektspezifische Skills behalten.

## 3. Interview (Modus A, Lücken bei B/C)

Frage gebündelt, höchstens 5 Fragen pro Runde, mit Vorschlägen statt offener Fragen. Was du aus Code oder
`$ARGUMENTS` ableiten kannst, fragst du nicht. Benötigt:
1. Ziel, Kernwert (was darf nie kaputtgehen), Erfolgskriterien, Nicht-Ziele.
2. Plattform/Hardware und Stack → passendes Preset aus `presets/` vorschlagen.
3. Randbedingungen (Toolchain-Versionen, Normen, Ressourcen-Budget).
4. Erster Meilenstein: was soll man am Ende vorführen können?
5. GitHub: Repo existiert schon / neu anlegen (privat?) / später. Arbeitet der Nutzer mit Issues oder nur mit Specs?

## 4. Preset anwenden

`presets/README.md` und das gewählte `presets/<name>/PRESET.md` lesen und dessen „Anwendung" folgen
(Befehle → `CLAUDE.md` inkl. Zeile „Shell:", `ci.yml`, Anhänge für `.gitignore` / ggf. `.gitattributes` / `.editorconfig`,
Gerüst nur in Modus A). Kein Preset passt → Befehle mit dem Nutzer festlegen. Für die Stack-Wahl ein ADR anlegen.
In B/C: bestehende CI **zusammenführen** statt ersetzen.

**Befehle prüfen:** Build, Test (Host) und Lint jetzt ausführen – sie müssen grün sein oder der Grund steht in
`docs/STAND.md` unter „Blocker". Flash nur mit angeschlossenem Board und Zustimmung des Nutzers. Monitor/Run
nicht ausführen, wenn der Befehl nicht von selbst endet – stattdessen unter „Offen – manuell prüfen" eintragen.

## 5. Dokumente füllen

- `CLAUDE.md`: Kopfzeile, Befehle, Shell, projektspezifische Regeln. Unter ~120 Zeilen bleiben.
  In B/C die Inhalte der alten `CLAUDE.md` gemäß Tabelle übernehmen, danach `CLAUDE.alt.md` löschen (git hat die Historie).
- `docs/PROJEKT.md`, `docs/ARCHITEKTUR.md` (bei A: Zielbild, knapp), `docs/ROADMAP.md` (M1 ausgearbeitet, spätere grob).
- ADRs für die Grundsatzentscheidungen + `docs/entscheidungen/INDEX.md`. In ADR-0001 das Datum setzen.
- „Typische Fallstricke" des Presets, die zum Projekt passen → `docs/LEARNINGS.md`.
- Firmware: `docs/hardware/README.md` mit bekannten Fakten füllen, Unbekanntes als „–". Reine Software: Ordner löschen.
- `docs/STAND.md`: Fokus = M1, nächster Schritt = erste `/spec` (bzw. bei C: die übernommene Spec).
- `README.md`: in Modus A durch eine projektbezogene README ersetzen (Zweck, Setup, Build – kurz).
  In B/C die bestehende README behalten; höchstens einen Abschnitt „Entwicklung" mit Verweis auf `CLAUDE.md`/`docs/` ergänzen.
- Template-Ballast entfernen: `presets/`, `scripts/neues-projekt.*`, `.claude/skills/kickoff/` (dieser Skill),
  übrig gebliebene `*.template`-Dateien. Vorher prüfen, dass nichts mehr darauf verweist.
- Keine `{{`-Platzhalter mehr: `grep -rn "{{" CLAUDE.md docs/ .github/ --exclude=_VORLAGE.md --exclude=0000-vorlage.md`
  – Reste beim Nutzer klären.
- `bash scripts/docs-check.sh` muss grün sein.

## 6. Git & GitHub

- `git init -b main` falls nötig. Commit: `chore: set up project via kickoff` (A) bzw. `chore: adopt claude workflow template` (B/C).
- Mit Zustimmung des Nutzers: `gh repo create <name> --private --source . --push` (oder vorhandenes Remote nutzen).
- `bash scripts/github-labels.sh` (Labels anlegen). Arbeitet der Nutzer mit Issues: für M1 Issues anlegen und in
  `docs/ROADMAP.md` verlinken – Liste vorher zeigen.
- Branch-Schutz für `main` empfehlen (Force-Push und Löschen verbieten; bei Bedarf CI als Pflicht-Check).
  Einstellen nur mit Zustimmung (`gh api` oder GitHub-Weboberfläche).

## 7. Abschluss

Bericht in 5–8 Zeilen: Modus, Stack/Preset, was läuft (mit Befehl), was offen ist, angelegte ADRs,
nächster Schritt (`/spec <erstes Feature>`).
