# Preset: python

## Passt für

Python-Anwendungen, CLI-Tools, Skripte mit Tests, Host-Tools für Firmware-Projekte (Flasher, Log-Parser, Testrigs).
Python ≥ 3.12, verwaltet mit uv (Projekt + Lockfile), src-Layout. Nicht für reine Notebooks/Data-Science-Spielwiesen.

## Voraussetzungen

Windows 11:
- uv: `winget install --id astral-sh.uv -e` (alternativ `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`).
  Prüfen: `uv --version` (Stand Erstellung: 0.13.7).
- Python: kein separater Installer nötig – `uv sync` holt die Version aus `.python-version` selbst
  (oder `uv python install 3.13`). Das installierte System-Python 3.13 funktioniert ebenfalls.
- ruff, mypy, pytest kommen als Dev-Gruppe über `uv sync` – nicht global installieren.

Linux-CI: `astral-sh/setup-uv` installiert uv; Python und Pakete kommen aus `.python-version` + `uv.lock`.

Pinning: Paketversionen pinnt `uv.lock` (committen!). Python-Version pinnt `.python-version`.
Optional uv selbst pinnen: in `pyproject.toml` `[tool.uv] required-version = ">=0.13"`.

## Befehle für CLAUDE.md

Stack: `Python 3.13 / uv / pytest / ruff / mypy (strict)`

| Zweck | Befehl |
|---|---|
| Build | `uv sync` (Paket bauen: `uv build`) |
| Test (Host) | `uv run pytest` |
| Lint/Format | `uv run ruff check . && uv run ruff format --check . && uv run mypy src` (Fix: `uv run ruff check --fix . && uv run ruff format .`) |
| Flash / Run | `uv run app` (kein Flash; Dienste/interaktive Programme laufen endlos → Claude nur mit Timeout oder im Hintergrund) |
| Monitor / Logs | – (Konsolenausgabe; bei Diensten Log-Datei über `logging` nennen; `tail -f` o. Ä. startet der Nutzer, Claude nur mit Timeout) |

Hinweis: `&&` funktioniert in Git Bash und PowerShell 7, nicht in Windows PowerShell 5.1 (dort `;`).

Typprüfer: **mypy** statt pyright, weil es als reines Python-Paket in der uv-Umgebung läuft (kein Node),
über `uv.lock` versioniert ist und die Referenz für Typ-Semantik ist. pyright/Pylance kann im Editor
zusätzlich laufen; maßgeblich für „grün“ ist mypy.

## Projektgerüst

```
<repo>/
├── .python-version        # 3.13
├── pyproject.toml         # Projekt, Dev-Gruppe, Konfig für pytest/ruff/mypy, Build-Backend uv_build
├── uv.lock                # entsteht beim ersten `uv sync` – committen
├── src/app/
│   ├── __init__.py        # main() = Einstiegspunkt für `uv run app`
│   └── core.py            # reine Logik ohne I/O
└── tests/test_core.py
```

`geruest/` enthält alles außer `uv.lock`; Inhalt 1:1 ins Repo-Root kopieren (inkl. `.python-version`).
Umbenennen von `app`: Ordner `src/app`, `name` und `[project.scripts]` in `pyproject.toml`, Imports in Tests.

## Teststrategie

- Unit (Host): pytest unter `tests/`, Logik in `core`-artigen Modulen ohne I/O halten. Dateisystem über
  `tmp_path`, Umgebung über `monkeypatch` – keine echten Pfade des Entwicklers.
- Integration: externe Systeme (Serielle Schnittstelle, Netzwerk, Hardware) hinter schmalen Adaptern;
  echte Läufe mit Marker kennzeichnen (`@pytest.mark.hw`, Marker in `pyproject.toml` registrieren)
  und in CI mit `-m "not hw"` ausschließen.
- Manuell: alles mit echter Hardware/GUI/Fremdsystem → als **offen – manuell prüfen** melden.

## CI

`ci.yml`: ubuntu-latest, `actions/checkout`, `astral-sh/setup-uv` (voll gepinnt – setup-uv veröffentlicht
seit v8 keine Major-Tags mehr) mit Cache auf Basis von `uv.lock`, dann `uv sync --locked`, ruff check
(Annotationen im PR), ruff format --check, mypy, pytest.
Grenzen: nur Linux – Windows-spezifisches (Pfade, Encoding, Groß/Klein) fällt dort nicht auf; bei Bedarf
Matrix um `windows-latest` erweitern. `--locked` bricht ab, wenn `uv.lock` nicht zu `pyproject.toml` passt.

## Typische Fallstricke

### .venv im Dropbox-Ordner synchronisiert #uv #dropbox #windows
*2026-11-05 · Quelle: Preset python*
- **Symptom:** Dropbox lädt tausende Dateien hoch, `uv sync` scheitert mit „Zugriff verweigert“, kaputte venv auf dem Zweitgerät.
- **Ursache:** `.venv` liegt im synchronisierten Projektordner; Dropbox sperrt Dateien und überträgt plattformspezifische Binaries.
- **Lösung / Regel:** `.venv` von Dropbox ausschließen – vor dem ersten `uv sync` in PowerShell: `New-Item -ItemType Directory .venv; Set-Content -Path .venv -Stream com.dropbox.ignored -Value 1` (uv nutzt den leeren Ordner). Legt uv die venv neu an (z. B. Python-Wechsel), geht die Markierung verloren → erneut setzen. Alternativ pro Shell `UV_PROJECT_ENVIRONMENT` auf einen Pfad außerhalb setzen (nie global für alle Projekte denselben). venv nie zwischen Geräten teilen – auf jedem Gerät `uv sync`.

### uv.lock fehlt oder ist veraltet #uv #ci
*2026-11-05 · Quelle: Preset python*
- **Symptom:** CI bricht bei `uv sync --locked` ab oder installiert andere Versionen als lokal.
- **Ursache:** `uv.lock` nicht committet oder `pyproject.toml` geändert, ohne neu zu locken.
- **Lösung / Regel:** `uv.lock` immer committen. Abhängigkeiten nur per `uv add`/`uv remove` ändern; nach Hand-Edits `uv lock`.

### Encoding-Fehler nur unter Windows #windows #encoding
*2026-11-05 · Quelle: Preset python*
- **Symptom:** `UnicodeDecodeError`/kaputte Umlaute beim Lesen von Dateien, nur unter Windows.
- **Ursache:** `open()`/`read_text()` ohne `encoding` nutzt unter Windows die ANSI-Codepage (cp1252), unter Linux UTF-8.
- **Lösung / Regel:** Immer `encoding="utf-8"` angeben (auch bei `subprocess.run(..., text=True)`). Aufdecken: `PYTHONWARNDEFAULTENCODING=1` meldet jede Stelle ohne `encoding` als `EncodingWarning` (`PYTHONUTF8=1` dagegen verdeckt den Fehler nur).

### Tests grün unter Windows, rot in der Linux-CI #windows #pfade #ci
*2026-11-05 · Quelle: Preset python*
- **Symptom:** Datei/Modul „nicht gefunden“ nur in CI.
- **Ursache:** Windows-Dateisystem ignoriert Groß/Kleinschreibung; von Hand zusammengesetzte Backslash-Pfade.
- **Lösung / Regel:** `pathlib.Path` statt String-Pfade, Dateinamen exakt schreiben, Testdaten über `tmp_path` bzw. relativ zu `__file__`.

### Falsches Python / Modul nicht gefunden #uv #pytest
*2026-11-05 · Quelle: Preset python*
- **Symptom:** `ModuleNotFoundError: app` oder fehlende Pakete beim Aufruf von `pytest`/`python` direkt.
- **Ursache:** Globales Python bzw. global installiertes pytest statt der Projekt-venv; src-Layout ist erst nach `uv sync` importierbar.
- **Lösung / Regel:** Alles über `uv run …` aufrufen; nie `pip install` in die Projekt-venv.

### CRLF-Zeilenenden vermischt #git #windows
*2026-11-05 · Quelle: Preset python*
- **Symptom:** Diffs ändern jede Zeile; Shell-Skripte brechen in CI mit `$'\r': command not found`.
- **Ursache:** `core.autocrlf` unterschiedlich auf den Geräten, keine passende `.gitattributes`.
- **Lösung / Regel:** `.gitattributes` der Vorlage behalten (`* text=auto`, `*.sh eol=lf`); ruff format behält das vorhandene Zeilenende bei.

## ADR-Kandidaten

- Typprüfer: mypy strict (Preset) vs. pyright vs. keiner?
- Python-Mindestversion: 3.12 vs. 3.13 (Features vs. Zielsysteme)?
- Paket vs. Skript: installierbares Paket mit Entry-Point (Preset) vs. `uv run script.py` mit Inline-Metadaten?
- Verteilung: Wheel/PyPI vs. `uv tool install` aus Git vs. Exe (PyInstaller/Nuitka)?
- CLI-Framework: argparse vs. Typer vs. Click?
- Konfiguration: TOML-Datei (`tomllib`) vs. Umgebungsvariablen vs. pydantic-settings?

## Anwendung

1. `presets/python/ci.yml` → `.github/workflows/ci.yml` kopieren.
2. Inhalt von `presets/python/gitignore` an `.gitignore` anhängen.
3. Inhalt von `presets/python/geruest/` (inkl. `.python-version`) ins Repo-Root kopieren; `app` ggf. umbenennen (siehe Projektgerüst).
4. `.gitattributes` der Vorlage beibehalten (nicht überschreiben; regelt Zeilenenden bereits).
5. Liegt das Projekt in Dropbox: **vor** dem ersten `uv sync` `.venv` von Dropbox ausschließen (Fallstrick oben).
6. Tabelle aus „Befehle für CLAUDE.md“ samt Stack-Zeile in `CLAUDE.md` übernehmen.
7. Prüfen: `uv sync`, `uv run pytest`, `uv run ruff check . && uv run ruff format --check . && uv run mypy src`, `uv run app` – Ausgaben zitieren.
8. `uv.lock` und `.python-version` committen.
9. „Typische Fallstricke“ nach `docs/LEARNINGS.md` (Bereich Toolchain & Build) kopieren; ADR-Kandidaten mit dem Nutzer klären (`/entscheidung`).
