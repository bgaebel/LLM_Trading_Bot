# ADR-0002: Python 3.12 mit uv, pytest, ruff und mypy strict im src-Layout

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-05
- **Bereich:** #build #python

## Kontext
Die Arena ist eine Python-Anwendung (Standardbibliothek, Python 3.11+, flache Imports, unittest). Der Bot
übernimmt Module daraus (ADR-0003) und läuft auf einem Hetzner-Server mit Ubuntu 24.04, dessen System-Python 3.12 ist.
Lokal scheiterte `uv python install 3.13` am Anlegen des Versions-Links (siehe LEARNINGS), 3.12 ist installiert.

## Entscheidung
- Python **3.12** (`.python-version`, `requires-python >=3.12`), verwaltet mit **uv**; `uv.lock` wird committet.
- Installierbares Paket `llm_trading_bot` im src-Layout, Einstiegspunkt `trading-bot`.
- Tests mit **pytest**, Lint/Format mit **ruff**, Typprüfung mit **mypy strict** (maßgeblich für „grün").
- Fremdpakete sind erlaubt, wenn sie per `uv add` gelockt werden – Standardbibliothek bleibt erste Wahl.

## Verworfene Alternativen
- **Python 3.13** – auf Ubuntu 24.04 nicht System-Python; bringt für den Bot keine benötigte Funktion.
- **Arena-Stil (nur Standardbibliothek, unittest, flache Imports)** – keine Typprüfung, kein Lockfile,
  Imports hängen vom Arbeitsverzeichnis ab.
- **pyright statt mypy** – braucht Node; mypy läuft versioniert in der uv-Umgebung.

## Konsequenzen
- Positiv: reproduzierbare Umgebung lokal, in CI und auf dem Server; Typfehler fallen vor dem Deployment auf.
- Negativ: übernommener Arena-Code muss typisiert und an pytest angepasst werden.
- Neu bewerten, wenn: der Server auf eine Ubuntu-Version mit neuerem System-Python wechselt.

## Code-Anker
- `pyproject.toml`, `.python-version`
