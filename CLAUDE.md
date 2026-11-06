# LLM Trading Bot

Trading-Bot für BTC/EUR und ETH/EUR auf einem Hetzner-Cloud-Server, gebaut aus Modulen der
[LLM Trading Arena](https://github.com/bgaebel/LLM-Trading-Arena) – erst Paper, später Echtgeld.
Details: `docs/PROJEKT.md` · Aufbau: `docs/ARCHITEKTUR.md` · Plan: `docs/ROADMAP.md`

<!-- Diese Datei wird in JEDER Session vollständig geladen. Halte sie unter ~120 Zeilen.
     Alles, was nur manchmal gebraucht wird, gehört nach docs/ und wird hier nur verlinkt. -->

## Befehle

Stack: Python 3.12 / uv / pytest / ruff / mypy (strict) · Ziel: Hetzner Cloud CX23, Ubuntu, systemd · Shell: Bash-Tool (Git Bash)

| Zweck | Befehl |
|---|---|
| Build | `uv sync` |
| Test (Host) | `uv run pytest` |
| Lint/Format | `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools && uv run shellcheck deploy/*.sh scripts/*.sh` (Fix: `uv run ruff check --fix . && uv run ruff format .`) |
| Run (lokal) | `uv run trading-bot` (Dienst läuft später endlos → nur mit Timeout/im Hintergrund) |
| Deploy | `bash scripts/deploy.sh <alias> <dienst> deploy` (nur Commits auf `origin/main` mit grüner CI; nie ohne Zustimmung; Ablauf `docs/betrieb/server.md`) |
| Logs (Server) | `ssh <alias> 'sudo journalctl --namespace=llm-trading-bot -u llm-trading-bot -n 50 --no-pager'` (`-f` nur mit Timeout) |

uv ist per winget installiert; findet die Shell `uv` nicht (PATH erst nach Neustart aktiv), liegt es unter
`$LOCALAPPDATA/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe/uv.exe`.

„Fertig" heißt: Build + Tests + Lint grün. Schritte, die den echten Server, echte APIs oder einen Menschen
brauchen, benenne ich ausdrücklich als **offen – manuell prüfen** und behaupte sie nie als erledigt.

## Arbeitsweise

1. **Ziel vor Code.** Bei allem, was über eine kleine Änderung hinausgeht: erst `/spec`, dann `/umsetzen`.
   Kleine Änderungen (ein Fix, wenige Dateien, klares Ergebnis) direkt erledigen.
2. **Entscheidungen respektieren.** ADRs mit Status „angenommen" (Liste unten) sind bindend. Bevor ich etwas ändere,
   das eine Entscheidung berührt, lese ich das ADR. Will ich davon abweichen: Begründung nennen,
   den Nutzer fragen, bei Zustimmung ein neues ADR anlegen, das das alte ablöst (`/entscheidung`).
   Nie still revidieren.
3. **Code-Anker beachten.** `# ADR-NNNN: …` oder `# LEARNING(#tag): <Titel>` im Code markiert absichtlich
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

## Projektspezifische Regeln

<!-- Harte Regeln, deren Verletzung Schaden anrichtet. Kurz halten. -->
- **Kein Echtgeld vor M2 (ADR-0004):** keine Order-Endpunkte implementieren oder aufrufen, keine Bitvavo-Keys
  mit Handelsrecht anlegen/verwenden. `live` bleibt gesperrt, bis ein neues ADR es freigibt.
- Secrets (`.env`), SQLite-DBs, Backups und Logs nie committen, loggen oder in Diffs zeigen.
- **Das Repo ist öffentlich (gewollt):** keine Server-IP, Hostnamen, Linux-Benutzer, SSH-Ports, Kontostände
  oder echten Trades in Code, Doku, Commits oder PRs – in Anleitungen Platzhalter wie `<server>` verwenden.
- Fail closed: bei fehlenden/veralteten Kursen oder ungültigen LLM-Antworten keine neue Order;
  nur abgeschlossene Kerzen; BTC-Fehler blockiert ETH nicht und umgekehrt.
- Handelshistorie nie still zurücksetzen oder migrieren; nie zwei schreibende Prozesse auf derselben DB
  (weitere Prozesse nur lesend mit `mode=ro`, ADR-0016).
- Gebühren, Slippage, Strategieregeln und Prompts nur mit Tests und neuer Strategieversion ändern.
- HTTP bindet nur an `127.0.0.1`; Zugriff per SSH-Tunnel (ADR-0005).
- Befehle auf dem Hetzner-Server (SSH, Deploy, Neustart) nur mit Zustimmung des Nutzers.
- Aus der Arena übernommener Code bekommt den Herkunftsvermerk aus ADR-0003; das Arena-Repo wird von hier nicht geändert.

## Aktueller Stand

@docs/STAND.md

## Entscheidungen (angenommene sind bindend)

@docs/entscheidungen/INDEX.md
