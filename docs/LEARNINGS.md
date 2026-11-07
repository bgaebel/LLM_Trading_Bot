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

### uv-Neuinstall scheitert mit „os error 32" – venv danach ohne Paket #uv #windows
*2026-11-06 · Quelle: Spec 003*
- **Symptom:** `uv run` bricht beim Neubau des eigenen Pakets ab („Datei wird von einem anderen Prozess verwendet", os error 32); danach `ModuleNotFoundError: llm_trading_bot` für alle Tests.
- **Ursache:** Kurzzeitige Dateisperre in `.venv` (vermutlich Virenscanner/Indexer); uv hat die alte Installation schon entfernt (`.pth` weg, `dist-info` noch da).
- **Lösung / Regel:** Einfach `uv sync` erneut ausführen – repariert die venv. Nicht mit `--no-sync` weiterarbeiten, sonst laufen Tests gegen eine kaputte Umgebung.

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

### Ein Risikostopp ohne Zwangsverkauf hinterlässt keine Spur #risiko #paper
*2026-11-07 · Quelle: Spec 011 (Review)*
- **Symptom:** Eine alte Freigabe-Anforderung hob einen späteren Stopp sofort wieder auf.
- **Ursache:** `mark` schreibt `RISK_SELL` nur für Bestände > 0. Stoppt ein Verkauf auf 0 das Depot (Gebühr, Spread),
  gibt es keine Zeile – „jünger als der letzte RISK_SELL“ vergleicht dann mit dem vorigen Stopp.
- **Lösung / Regel:** Freigabe nur, wenn die Anforderung jünger als der letzte Trade des Depots ist (ein freigegebenes
  Depot kann erst nach einem Trade wieder fallen). Zeitpunkte von Ereignissen nie aus „letzte Zeile mit Merkmal X“
  ableiten, ohne zu prüfen, ob jedes Ereignis so eine Zeile erzeugt.

### Gespeicherte Zeitstempel sind ganze Sekunden, die Uhr nicht #zeit #marktdaten
*2026-11-07 · Quelle: Spec 011 (Review)*
- **Symptom:** Eine Runde verwarf alle Kurse als älter als 20 s, ohne neu abzurufen – obwohl laut Uhr erst 19,6 s vergangen waren.
- **Ursache:** `iso()` schneidet auf ganze Sekunden ab; das in der DB gemessene Alter ist bis zu 1 s größer als
  `clock() - abrufzeit`.
- **Lösung / Regel:** Altersgrenzen gegen denselben Maßstab prüfen, mit dem später gelesen wird (`int(clock())` beim
  Abruf merken) und für Prüfung und Lesen dieselbe `now` verwenden.

### Nachts fehlen ETH-EUR-Minutenkerzen – eine Stunde ohne ETH-Entscheidung #bitvavo #marktdaten #betrieb
*2026-11-07 · Quelle: Spec 009 (Neustart-Test auf dem Server)*
- **Symptom:** Nach einem Server-Neustart sind alle Runden `incomplete`, Status `degraded`:
  „ETH-EUR: gap in recent 1m bars - no decision“; BTC läuft normal. Sieht aus wie ein Neustart-Fehler.
- **Ursache:** Bitvavo liefert für Minuten ohne Handel keine Kerze. Nachts hat ETH-EUR öfter Lücken von einer Minute,
  manchmal drei (2026-11-06 23:03–23:05 UTC). Bis zu zwei werden aufgefüllt (`fill_tiny_gaps`), längere sperren den
  Markt, bis die Lücke aus dem Fenster der letzten 60 Kerzen fällt (bis zu einer Stunde). Gewollt: fail closed.
- **Lösung / Regel:** Vor einer Fehlersuche die Kerzen prüfen:
  `curl -s "https://api.bitvavo.com/v2/ETH-EUR/candles?interval=1m&limit=60"` und Abstände > 60 s suchen.
  Gesundheitschecks dürfen eine Runde, die nur an Marktdaten scheitert, nicht als Code-Fehler werten.

### Signal-Handler setzt Event, auf das der Hauptthread wartet #python #signal #dienst
*2026-11-06 · Quelle: Spec 008 (Review, reproduziert)*
- **Symptom:** Selten hängt der Prozess bei SIGTERM/Strg+C für immer; kein Watchdog-Ping, Statusdatei bleibt `running`.
- **Ursache:** Python-Handler laufen im Hauptthread zwischen zwei Bytecodes. Wartet der Hauptthread gerade in
  `event.wait()`, hält er kurz den nicht reentranten Lock des Events; `event.set()` im Handler blockiert darauf.
- **Lösung / Regel:** Im Hauptthread nie auf ein Objekt warten, das der Handler sperrt: `time.sleep(slice)` und
  danach `event.is_set()` (ohne Lock). Regressionstest in einem Unterprozess mit Zeitlimit (`_thread.interrupt_main`).

### Eingeengte Fehlerliste muss alle Transportfehler abdecken #python #http #isolation
*2026-11-06 · Quelle: Spec 001 (Prüfer-Blocker)*
- **Symptom:** Ein kaputter BTC-Abruf ließ `build_snapshots` abbrechen; ETH verlor seinen Snapshot, obwohl die Isolation getestet war.
- **Ursache:** Statt `except Exception` nur `(OSError, ValueError, …)` gefangen. `urllib` reicht `http.client.HTTPException` (`IncompleteRead`, `BadStatusLine`) und `OverflowError`/`RecursionError` aus dem Parsen unverpackt durch.
- **Lösung / Regel:** An der Transportgrenze alle bekannten Fehlertypen in eigene Fehler übersetzen (`TransportError`) und die Isolation Ende-zu-Ende über den echten Transport testen, nicht nur mit einem Fake, der saubere Fehler wirft.

### Sich selbst neu ladende Seite darf nicht die ganze Historie lesen #web #sqlite #performance
*2026-11-06 · Quelle: Spec 007 (Abschlussprüfung)*
- **Symptom:** Übersicht in Tests und mit wenigen Tagen Daten schnell; mit einem Jahr Minutenrunden (~1,6 Mio.
  Equity-Zeilen) ~6,7 s je Laden – bei Neuladen alle 15 s und globaler App-Sperre hängt die ganze Oberfläche.
- **Ursache:** `SELECT … FROM equity` ohne Grenze, ausgedünnt erst in Python. Die Datenmenge wächst mit dem Takt
  des Dienstes, die Testdaten nicht.
- **Lösung / Regel:** Was jede Seite lädt, mit festen Grenzen lesen (`LIMIT`, Index-Abfragen je Abschnitt) und mit
  einer künstlichen Jahres-DB messen. Test: alle Abfragen auf die wachsende Tabelle tragen ein `LIMIT`.

### Encoding-Fehler nur unter Windows #windows #encoding
*2026-11-05 · Quelle: Preset python*
- **Symptom:** `UnicodeDecodeError`/kaputte Umlaute beim Lesen von Dateien, nur unter Windows.
- **Ursache:** `open()`/`read_text()` ohne `encoding` nutzt unter Windows cp1252, auf dem Server UTF-8.
- **Lösung / Regel:** Immer `encoding="utf-8"` angeben (auch bei `subprocess.run(..., text=True)`).

### Umlaute in der Konsolenausgabe kaputt unter Windows #windows #encoding
*2026-11-06 · Quelle: Spec 003*
- **Symptom:** `uv run trading-bot signals` zeigt „Abw�rtstrend" statt „Abwärtstrend"; Tests mit `io.StringIO` sind grün.
- **Ursache:** `sys.stdout` nutzt unter Windows die Konsolen-Codepage (cp1252), nicht UTF-8; die Strategie-Begründungen der Arena sind deutsch.
- **Lösung / Regel:** In `main()` `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` (nur wenn `io.TextIOWrapper`). Nicht `PYTHONUTF8` global setzen – das verdeckt Datei-Encoding-Fehler (siehe oben).

### Formular-POST hängt: einfädiger HTTPServer wartet auf leere Browser-Verbindung #web #http #test
*2026-11-06 · Quelle: Spec 006*
- **Symptom:** Klick auf „Passwort festlegen“ tut scheinbar nichts; im Browser stehen POST-Requests auf „pending“
  bzw. `ERR_ABORTED`; alle Tests (eine Verbindung je Request) grün.
- **Ursache:** `http.server.HTTPServer` bearbeitet eine Verbindung nach der anderen. Browser öffnen vorab eine
  Reserve-Verbindung und senden darauf nichts; der Server blockiert beim Lesen dieser Verbindung, der echte POST wartet.
- **Lösung / Regel:** `ThreadingHTTPServer` mit `daemon_threads` und Socket-`timeout` im Handler; App-Logik hinter
  einem Lock. Test mit einer offenen, stummen Verbindung vor dem eigentlichen Request.

### Sperre eines beendeten Prozesses wird unter Windows verzögert frei #windows #lock #test
*2026-11-06 · Quelle: Spec 005 (Lock-Test aus Spec 004 wurde lokal flaky)*
- **Symptom:** `test_lock_of_a_killed_process_is_released` scheitert lokal etwa jedes zweite Mal mit
  `WriterLockBusy`, obwohl der Kindprozess per `kill()` beendet und mit `wait()` abgeräumt ist; in der Linux-CI grün.
- **Ursache:** `msvcrt.locking` nutzt Byte-Range-Locks; Windows gibt die Sperren eines beendeten Prozesses
  asynchron frei („abhängig von verfügbaren Systemressourcen"). `fcntl.flock` unter Linux sofort.
- **Lösung / Regel:** Tests, die die Freigabe nach einem Absturz prüfen, warten unter Windows kurz in einer
  Schleife (Frist 10 s). Im Betrieb (Linux) ist nichts zu tun; unter Windows schlägt ein sofortiger Neustart mit
  „busy" fehl und klappt beim nächsten Versuch – das ist fail closed.

### Tests grün unter Windows, rot in der Linux-CI #windows #pfade #ci
*2026-11-05 · Quelle: Preset python*
- **Symptom:** Datei/Modul „nicht gefunden" nur in CI bzw. auf dem Server.
- **Ursache:** Windows ignoriert Groß/Kleinschreibung; von Hand zusammengesetzte Backslash-Pfade.
- **Lösung / Regel:** `pathlib.Path`, Dateinamen exakt schreiben, Testdaten über `tmp_path`.

## Prozess & Werkzeuge

### Ersetzungen per Python-Heredoc greifen bei Nicht-ASCII nicht #windows #encoding #werkzeug
*2026-11-06 · Quelle: Spec 004*
- **Symptom:** `python - <<'EOF'` mit `s.replace("…–…", …)` in Git Bash ändert nichts; ein Escape wie
  `–` im Skript wird zu „2013" (sed) oder zum `SyntaxError: truncated \uXXXX escape` (Python).
- **Ursache:** Python liest das Skript von stdin in der Windows-Codepage (cp1252), die Datei ist UTF-8 – Umlaute und
  Gedankenstriche im Suchtext passen nicht. Backslash-Escapes werden von sed bzw. dem Python-Parser umgedeutet.
- **Lösung / Regel:** Ersetzungen mit Nicht-ASCII-Zeichen oder Backslashes mit dem Edit-Werkzeug machen; in
  Heredoc-Skripten nur ASCII verwenden. Dateien mit `write_text(..., encoding="utf-8", newline="\n")` schreiben.

### Golden-Fixtures beweisen Gleichheit nur mit Grenzfällen #test #strategie
*2026-11-06 · Quelle: Spec 003 (Prüfer-Blocker)*
- **Symptom:** Alle Golden-Tests grün, jeder BUY/SELL/HOLD-Zweig abgedeckt – trotzdem blieben 23 von 44 Schwellen-/Gewichtsänderungen unentdeckt.
- **Ursache:** Zufallsszenarien treffen die Ergebnisse, aber nicht die Werte nahe den Schwellen (ADX nur in Clustern weit weg von 18/19/20/24); „Zweig erreicht" ≠ „Bedingung geprüft".
- **Lösung / Regel:** Je Bedingung Eingaben knapp unter/auf/über der Schwelle erzeugen, Erwartung aus dem Original berechnen; mit einem Mutationstest (`tools/golden/mutation_check.py`) nachweisen, dass Änderungen auffallen. Überlebende nur akzeptieren, wenn ihre Äquivalenz begründet ist.

### Argumente mit Leerzeichen über ssh zerfallen #ssh #deploy
*2026-11-05 · Quelle: Spec 002*
- **Symptom:** `ssh host 'bash -s' -- prepare a b "ssh-ed25519 AAAA… kommentar"` – das Skript bekommt den Key als drei Argumente.
- **Ursache:** `ssh` fügt alle entfernten Argumente mit Leerzeichen zu **einer** Kommandozeile zusammen, die die entfernte Shell neu zerlegt; lokale Anführungszeichen gehen verloren.
- **Lösung / Regel:** Ganze entfernte Kommandozeile in doppelte Anführungszeichen, Werte darin in einfache: `ssh host "bash -s -- prepare a b '$KEY'" < skript.sh`.

### sshd übernimmt den ersten Wert – cloud-init kann Passwort-Login einschalten #ssh #hetzner #ubuntu
*2026-11-05 · Quelle: Spec 002*
- **Symptom:** (bekannte Falle, auf unserem Hetzner-Server nicht aufgetreten) `PasswordAuthentication no` in einer eigenen Datei wirkt nicht.
- **Ursache:** Viele Ubuntu-Cloud-Images legen `/etc/ssh/sshd_config.d/50-cloud-init.conf` mit `PasswordAuthentication yes` an (das Hetzner-Image vom 2026-11-05 nicht – dort war `yes` der Standardwert). sshd nimmt je Option den **ersten** gelesenen Wert, Dateien alphabetisch.
- **Lösung / Regel:** Eigenen Drop-in `01-…` nennen und immer mit `sshd -T` (wirksame Werte) prüfen, nie nur die Datei ansehen.
