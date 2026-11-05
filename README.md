# Claude-Projekt-Template

Schlankes Arbeits-Framework für Software- und Firmware-Projekte mit Claude Code und GitHub.
Ein Nachfolger für GetShitDone (GSD), mit weniger Dateien und weniger Zeremonie. Der Fokus liegt darauf,
dass **Entscheidungen und Erfahrungen nicht verloren gehen**.

> Diese README beschreibt das Template. `/kickoff` ersetzt sie im neuen Projekt durch eine projektbezogene README.

## Neues Projekt anlegen

**Variante A – GitHub-Template (empfohlen, funktioniert auf jedem PC ohne lokale Template-Kopie):**
```bash
gh repo create mein-projekt --template bgaebel/claude-projekt-template --private --clone
cd mein-projekt
claude
```
Dann in Claude: `/kickoff`

**Variante B – lokal kopieren:**
```powershell
.\scripts\neues-projekt.ps1 -Ziel C:\Pfad\zu\mein-projekt [-GitHub]
```
oder in Git Bash: `bash scripts/neues-projekt.sh <ziel> [--github]`. Danach `claude` im Zielordner starten und `/kickoff` ausführen.

**Bestehendes Projekt / GSD-Projekt übernehmen:** Template-Dateien ins Projekt kopieren (`CLAUDE.md`, `docs/`,
`.claude/`, `.github/`, `presets/`, `scripts/`), **ohne Vorhandenes zu überschreiben**: Eine bestehende `CLAUDE.md`
vorher in `CLAUDE.alt.md` umbenennen. Gibt es schon `.claude/settings.json`, `.github/workflows/ci.yml` oder gleichnamige
Skripte, die Template-Version mit Endung `.template` daneben legen. Dann `/kickoff` ausführen: Es führt beides zusammen,
erkennt `.planning/` und überführt Entscheidungen, Roadmap, laufende Phase, Stand und Learnings ins neue Format.

## Täglicher Ablauf

```
/start ──► /spec <feature> ──► /umsetzen NNN ──► /abschluss NNN ──► PR ──► merge
  ▲              │                   │  ▲
  │              │     /entscheidung ┤  │ /lernen
  │              │                   ▼  │
  │              │               /diagnose (Bug hält sich hartnäckig)
  └──── /ende ◄──┴─────────────────────┘  (Pause / Gerätewechsel – jederzeit)
```

| Befehl | Wann | Ergebnis |
|---|---|---|
| `/kickoff` | einmal pro Projekt | Projekt eingerichtet, Befehle laufen, erste ADRs, erster Commit |
| `/start` | Arbeitsbeginn, nach Gerätewechsel | Pull, Stand geladen, nächster Schritt vorgeschlagen |
| `/spec <thema>` | vor jedem nicht-trivialen Feature | `docs/specs/NNN-*.md` mit prüfbaren Akzeptanzkriterien, freigegeben |
| `/umsetzen NNN` | nach Freigabe | Branch, schrittweise Commits, Plan abgehakt |
| `/abschluss NNN` | Feature fertig | unabhängige Prüfung (Agent `pruefer`), Doku nachgezogen, PR |
| `/entscheidung <satz>` | sobald etwas entschieden ist | ADR + Index-Eintrag |
| `/lernen <erkenntnis>` | sobald etwas Zeit gekostet hat | Eintrag in `docs/LEARNINGS.md` |
| `/diagnose <symptom>` | Bug nach 2 Versuchen nicht weg | Debug-Protokoll mit Hypothesentabelle, das Sessions überdauert |
| `/ende` | Pause, Feierabend, Gerätewechsel | STAND.md überschrieben, commit, push |

Kleine Änderungen brauchen keinen dieser Befehle. Claude erledigt sie direkt, verifiziert sie und committet.

## Wie das Gedächtnis funktioniert

| Mechanismus | Schützt vor |
|---|---|
| **ADRs** (`docs/entscheidungen/`) mit Pflichtabschnitt „Verworfene Alternativen"; der Index wird in jede Session geladen und gilt als bindend; Revision nur per neuem ADR mit Zustimmung | Entscheidungen, die still zurückgedreht werden; verworfene Ideen, die erneut vorgeschlagen werden |
| **Code-Anker** `// ADR-0007: …`, `// LEARNING: …` direkt an der Stelle | „Aufräumen" von absichtlich so gebautem Code |
| **LEARNINGS.md** mit `#tags`, wird vor der Arbeit in einem Bereich durchsucht | denselben Fehler zweimal machen |
| **Debug-Protokolle** mit Hypothesentabelle; widerlegte Hypothesen bleiben stehen | dieselbe falsche Spur in der nächsten Session erneut verfolgen |
| **Specs** mit Abschnitten Verlauf und Ergebnis | Ziel aus den Augen verlieren; nicht wissen, warum vom Plan abgewichen wurde |
| **STAND.md** (überschrieben, max. ~40 Zeilen, automatisch geladen) + **SessionStart-Hook** (Branch, Änderungen, Commits) | Einarbeitungszeit zu Beginn jeder Session; Wissensverlust beim Gerätewechsel |
| **Prüfer-Agent** ohne Vorwissen aus der Umsetzung | „fertig" behauptet, aber nicht belegt |

## Prinzipien (bewusst anders als GSD)

- **Wenige Dateien mit je einem klaren Zweck.** Jede Datei hat eine Regel, wann sie aktualisiert wird
  (siehe Tabelle in `CLAUDE.md`). Keine Audit-, Summary- oder Research-Dateien, die sich ansammeln.
- **`CLAUDE.md` bleibt kurz** (< ~120 Zeilen), denn sie kostet in jeder Session Kontext. Details stehen in `docs/` und werden nur bei Bedarf gelesen.
- **Überschreiben statt Anhängen** für den Zustand (STAND.md), **unveränderlich** für Begründungen (ADRs).
  Die Historie steckt in git.
- **Verifizieren statt behaupten:** „fertig" heißt, ein Befehl belegt es. Hardware-Schritte werden ausdrücklich als manuell markiert.
- **GitHub ergänzt, ersetzt aber nicht:** Issues sind das Backlog, PRs der Review-Punkt. Alles, was Claude zum Arbeiten braucht, liegt jedoch offline im Repo.

## Struktur

```
CLAUDE.md                     Einstieg, in jeder Session geladen (Befehle, Regeln, Stand, ADR-Index)
docs/
  PROJEKT.md                  Ziel, Kernwert, Nicht-Ziele, Randbedingungen, Glossar
  ARCHITEKTUR.md              IST-Aufbau: Module, Schnittstellen, Datenfluss
  ROADMAP.md                  Meilensteine → Features → Issues
  STAND.md                    Übergabe-Notiz (überschrieben)
  LEARNINGS.md                Fallstricke mit #tags
  entscheidungen/             ADRs + INDEX.md
  specs/                      eine Datei pro Feature
  debug/                      ein Protokoll pro hartnäckigem Bug
  hardware/                   Pinout, Datenblatt-Notizen, Errata (nur Firmware)
.claude/
  settings.json               Rechte, SessionStart-Hook
  hooks/session-start.sh      Git- und Arbeitsstand als Kontext beim Start
  skills/                     die Slash-Befehle oben
  agents/pruefer.md           unabhängiger Prüfer
.github/                      PR-/Issue-Vorlagen, CI, Doku-Check
presets/                      Stack-Startpakete (von /kickoff angewendet und dann entfernt)
scripts/                      neues-projekt.*, github-labels.sh, docs-check.sh
```

## Voraussetzungen

- Claude Code; unter Windows Git for Windows (für `bash` im Hook und in den Skripten).
- GitHub CLI `gh`, angemeldet über `gh auth login`. Ohne `gh` funktioniert alles bis auf das Anlegen von Issues und PRs aus Claude heraus.

## Hinweis zu Dropbox / OneDrive

**Empfehlung: Projekte außerhalb synchronisierter Ordner anlegen** (z. B. `C:\Projekte\`) und zwischen Geräten
ausschließlich über git synchronisieren (`/ende` → push, `/start` → pull). Gründe:
- Synchronisiert Dropbox auch `.git`, entstehen Konfliktkopien von Index und Refs, und das Repo geht kaputt.
- Ignoriert Dropbox nur `.git`, gibt es zwei konkurrierende Sync-Wege: Die Dateien kommen per Dropbox,
  die Commits per git. `git pull` scheitert dann mit „local changes would be overwritten".
- `.venv`, `node_modules`, `.pio` und `build` sind riesig und maschinenspezifisch (absolute Pfade).

Muss ein Projekt doch in Dropbox liegen, den **ganzen** Projektordner von der Synchronisierung ausnehmen
(PowerShell: `Set-Content -Path <projektordner> -Stream com.dropbox.ignored -Value 1`). Der Session-Hook warnt,
falls trotzdem Konfliktkopien auftauchen.

## Template weiterentwickeln

Das Template lebt nur auf GitHub (`bgaebel/claude-projekt-template`); eine dauerhafte lokale Kopie ist nicht nötig.
Zum Ändern: `gh repo clone bgaebel/claude-projekt-template`, Änderung committen, pushen, Klon wieder löschen.
Verbesserungen am Ablauf direkt hier im Template-Repo vornehmen. Bestehende Projekte übernehmen sie gezielt
durch Kopieren der betroffenen Dateien aus `.claude/skills/` bzw. `.claude/agents/`.
`docs/` ist hier die Vorlage für Projekte. Begründungen für das Template selbst gehören in diese README
(Abschnitt „Prinzipien") oder in die Commit-Messages, nicht nach `docs/entscheidungen/`.

`.claude/settings.vorschlag.json` ist die überarbeitete Fassung von `.claude/settings.json` (zusätzliche Rechte,
strengere Sperren). Sie wird erst wirksam, wenn du sie selbst nach `settings.json` kopierst.
