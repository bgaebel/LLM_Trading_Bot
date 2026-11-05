# Learnings

<!-- Erkenntnisse, die Zeit gekostet haben und nicht aus dem Code ablesbar sind.
     Neueste oben innerhalb des Bereichs. Jeder Eintrag hat #tags, damit man per grep findet:
       grep -n "#uart" docs/LEARNINGS.md
     Regeln:
     - Ein Eintrag = Symptom → Ursache → was hilft. Max. ~6 Zeilen. Längeres → Link auf docs/debug/ oder Spec.
     - Würde ein Wiederholen Schaden anrichten (Hardware zerstört, Daten weg)? → zusätzlich als
       harte Regel in CLAUDE.md unter "Projektspezifische Regeln".
     - Veraltet ein Eintrag (Bug im Tool gefixt, Bauteil getauscht)? Nicht löschen, sondern
       ~~durchstreichen~~ und "(überholt JJJJ-MM-TT: Grund)" ergänzen.
     - Reine Fakten aus Datenblatt/Errata gehören nach docs/hardware/; hier steht die Erfahrung damit (mit Link).
     - Code-Anker: // LEARNING(#tag): <Kurztitel exakt wie hier> -->

## Format

```
### <Kurztitel> #bereich #werkzeug
*<JJJJ-MM-TT> · Quelle: <Spec/Debug/Commit>*
- **Symptom:** was man beobachtet
- **Ursache:** was wirklich dahintersteckt
- **Lösung / Regel:** was man tun bzw. lassen muss
```

---

## Toolchain & Build

### uv python install scheitert am Versions-Link #uv #windows
*2026-11-05 · Quelle: Kickoff*
- **Symptom:** `uv sync` / `uv python install 3.13` lädt CPython herunter und bricht dann mit „Missing expected target directory for Python minor version link" ab.
- **Ursache:** Nicht abschließend geklärt; das Verzeichnis `cpython-3.13.18-…` existiert, der Link `cpython-3.13-…` wird nicht gültig angelegt (Aufruf aus Git Bash in der Claude-Sandbox).
- **Lösung / Regel:** System-Python 3.12 nutzen (`.python-version` = 3.12, ADR-0002). Nicht erneut verbiegen, um 3.13 zu erzwingen.

### .venv im Dropbox-Ordner synchronisiert #uv #dropbox #windows
*2026-11-05 · Quelle: Preset python*
- **Symptom:** Dropbox lädt tausende Dateien hoch, `uv sync` scheitert mit „Zugriff verweigert", kaputte venv auf dem Zweitgerät.
- **Ursache:** `.venv` liegt im synchronisierten Projektordner.
- **Lösung / Regel:** `.venv` ist per Dropbox-Stream `com.dropbox.ignored` ausgeschlossen. Legt uv die venv neu an (z. B. Python-Wechsel), Markierung neu setzen (PowerShell: `Set-Content -Path .venv -Stream com.dropbox.ignored -Value 1`). venv nie zwischen Geräten teilen – auf jedem Gerät `uv sync`.

### uv.lock fehlt oder ist veraltet #uv #ci
*2026-11-05 · Quelle: Preset python*
- **Symptom:** CI bricht bei `uv sync --locked` ab oder installiert andere Versionen als lokal.
- **Ursache:** `uv.lock` nicht committet oder `pyproject.toml` von Hand geändert, ohne neu zu locken.
- **Lösung / Regel:** `uv.lock` immer committen; Abhängigkeiten nur per `uv add`/`uv remove`, nach Hand-Edits `uv lock`.

### Falsches Python / Modul nicht gefunden #uv #pytest
*2026-11-05 · Quelle: Preset python*
- **Symptom:** `ModuleNotFoundError: llm_trading_bot` oder fehlende Pakete beim direkten Aufruf von `pytest`/`python`.
- **Ursache:** Globales Python statt Projekt-venv; das src-Layout ist erst nach `uv sync` importierbar.
- **Lösung / Regel:** Alles über `uv run …`; nie `pip install` in die Projekt-venv.

## Laufzeit & Code

### Encoding-Fehler nur unter Windows #windows #encoding
*2026-11-05 · Quelle: Preset python*
- **Symptom:** `UnicodeDecodeError`/kaputte Umlaute beim Lesen von Dateien, nur unter Windows.
- **Ursache:** `open()`/`read_text()` ohne `encoding` nutzt unter Windows cp1252, auf dem Server UTF-8.
- **Lösung / Regel:** Immer `encoding="utf-8"` angeben (auch bei `subprocess.run(..., text=True)`).

### Tests grün unter Windows, rot in der Linux-CI #windows #pfade #ci
*2026-11-05 · Quelle: Preset python*
- **Symptom:** Datei/Modul „nicht gefunden" nur in CI bzw. auf dem Server.
- **Ursache:** Windows ignoriert Groß/Kleinschreibung; von Hand zusammengesetzte Backslash-Pfade.
- **Lösung / Regel:** `pathlib.Path`, Dateinamen exakt schreiben, Testdaten über `tmp_path`.

## Prozess & Werkzeuge

### Argumente mit Leerzeichen über ssh zerfallen #ssh #deploy
*2026-11-05 · Quelle: Spec 002*
- **Symptom:** `ssh host 'bash -s' -- prepare a b "ssh-ed25519 AAAA… kommentar"` – das Skript bekommt den Key als drei Argumente.
- **Ursache:** `ssh` fügt alle entfernten Argumente mit Leerzeichen zu **einer** Kommandozeile zusammen, die die entfernte Shell neu zerlegt; lokale Anführungszeichen gehen verloren.
- **Lösung / Regel:** Ganze entfernte Kommandozeile in doppelte Anführungszeichen, Werte darin in einfache: `ssh host "bash -s -- prepare a b '$KEY'" < skript.sh`.

### sshd übernimmt den ersten Wert – cloud-init schaltet Passwort-Login ein #ssh #hetzner #ubuntu
*2026-11-05 · Quelle: Spec 002*
- **Symptom:** `PasswordAuthentication no` in einer eigenen Datei wirkt nicht.
- **Ursache:** Ubuntu-Cloud-Images haben `/etc/ssh/sshd_config.d/50-cloud-init.conf` mit `PasswordAuthentication yes`; sshd nimmt je Option den **ersten** gelesenen Wert, Dateien alphabetisch.
- **Lösung / Regel:** Eigenen Drop-in `01-…` nennen und immer mit `sshd -T` (wirksame Werte) prüfen, nie nur die Datei ansehen.
