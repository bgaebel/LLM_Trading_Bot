# Spec 009: Deploy auf Hetzner – Installation, Update/Rollback, Backup und Wiederherstellung

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s009-deploy` · **Meilenstein:** M1 (Nr. 6)
- **Erstellt:** 2026-11-06

## Ziel
Der Paper-Bot (`trading-bot run`) und die Weboberfläche laufen als systemd-Dienste auf dem gehärteten CX23 und
überstehen Updates, Rollbacks und einen Neustart des Servers ohne Verlust oder Verdopplung der Historie. Ein Update
ist ein Befehl von hier: geprüfter Commit von `main` → neues Release → Gesundheitscheck → sonst automatisch zurück.
Die Datenbank wird täglich auf dem Server gesichert, und eine Wiederherstellung ist einmal echt geprobt (ADR-0018).

## Akzeptanzkriterien
- [ ] AK1: `trading-bot backup --db <pfad> --dir <verz> [--keep N]` sichert über die SQLite-Online-Backup-API,
  auch während ein Schreiber läuft und eine Transaktion offen hat; die Kopie besteht `integrity_check`, hat die
  Schemaversion 2 und dieselben Zeilenzahlen je Tabelle wie der gesicherte Stand; Name mit UTC-Zeit, Rechte 600,
  erst nach Prüfung unter dem endgültigen Namen sichtbar (nie überschrieben). Rotation löscht nur eigene Kopien in
  diesem Verzeichnis über `N` hinaus (Standard 14); ein fehlgeschlagenes Backup löscht nichts. Exit ≠ 0 bei Fehler.
  — Nachweis: `tests/ops/test_backup.py`
- [ ] AK2: `trading-bot verify-db --db <pfad>` prüft nur lesend (Schemaversion, `integrity_check`, eindeutige
  Schlüssel) und gibt Zeilenzahlen aus; `trading-bot restore --backup <datei> --db <neuer pfad>` prüft die Kopie und
  schreibt nur in ein Ziel, das es noch nicht gibt (sonst Exit ≠ 0, nichts geändert). — Nachweis:
  `tests/ops/test_restore.py`, `tests/test_cli.py::test_verify_db_*`, `test_restore_*`
- [ ] AK3: Springt die Wanduhr während des Wartens zurück, berechnet der Dienst seinen nächsten Termin neu und
  verpasst höchstens die laufende Minute (statt so lange zu warten, wie die Uhr zurücksprang); ein Sprung vorwärts
  bleibt wie bisher (kein Nachholen). — Nachweis: `tests/service/test_run.py::test_a_clock_jump_back_*`
- [ ] AK4: Units in `deploy/`: Bot-Unit mit `RestartPreventExitStatus=3`; neue `llm-trading-bot-backup.service`
  (Oneshot, Dienstbenutzer, gehärtet, schreibt nur Backup- und Datenverzeichnis) und `.timer` (täglich 03:30 UTC,
  `Persistent=true`, `RandomizedDelaySec`). Alle mit Platzhaltern, in der CI per `systemd-analyze verify` geprüft;
  keine echten Server-Daten. — Nachweis: `scripts/check-units.sh` in der CI, `tests/test_deploy_files.py`
- [ ] AK5: `deploy/release.sh` (läuft per `sudo bash -s` auf dem Server, wie `setup-server.sh`) mit den Modi
  `install` (Verzeichnisse und Rechte, uv in fester Version mit SHA-256-Prüfung, Journal-Konfiguration,
  `systemd-time-wait-sync`; idempotent), `deploy <sha>`, `rollback`, `status`. `deploy`:
  - lehnt ein Archiv mit `secrets.env`, `.env*`, `*.db*` oder `web-auth.json` ab,
  - baut den Kandidaten (venv, `verify-db` auf der bestehenden DB), bevor der laufende Dienst gestoppt wird,
  - sichert vor dem Umschalten, installiert die Units, schaltet `current` atomar um, startet,
  - wartet ≤ 3 min auf eine gesunde Runde (Statusdatei: `running`, PID = `MainPID`, Runde `ok` nach dem Start),
  - rollt sonst Code und Units auf das vorige Release zurück (nie die DB) und endet mit Exit ≠ 0,
  - prüft, dass `secrets.env` und Drop-ins unverändert sind, behält zwei Vorgänger-Releases.
  — Nachweis: `tests/deploy/test_release.py` (Bash-Skript in einer Sandbox mit Ersatz für `systemctl`, `uv`,
  `runuser`; Linux, in der CI): Erfolg, Gesundheitscheck scheitert → Rollback, verbotene Datei → Abbruch vor dem
  Stoppen, Idempotenz von `install`, Aufräumen alter Releases; `shellcheck`
- [ ] AK6: `scripts/deploy.sh <alias> [<sha>]` (lokal, Git Bash) verweigert einen Commit, der nicht auf
  `origin/main` liegt oder keine grüne CI hat, überträgt `git archive` genau dieses Commits und ruft
  `release.sh` aus demselben Commit auf; sendet nie lokale Dateien außerhalb des Archivs. Nimmt den Alias als
  Argument, im Repo steht kein Server-Name. — Nachweis: `tests/deploy/test_deploy_script.py` (Verweigerung mit
  Ersatz für `git`/`gh`/`ssh`), `shellcheck`
- [x] AK7: [manuell] Erstinstallation auf dem Server (nur mit Zustimmung): `install`, dann `deploy`; erst läuft nur
  die Weboberfläche (ohne `secrets.env` startet `deploy` den Bot nicht und sagt das); der Nutzer richtet per Tunnel
  Passwort, Key und Depots ein; danach `deploy` erneut → Bot gesund. Prüfen: `stat` (Daten 700, Dateien 600, Release
  root/755), `uv --version`, `systemctl is-active` beider Units, eine Rundenzeile je Minute im Journal, Übersicht
  „Dienst läuft“.
- [x] AK8: [manuell] Update auf einen neueren Commit mit grüner CI: Gesundheitscheck besteht, Vorgänger liegt noch
  da; `rollback` schaltet zurück und der Dienst ist wieder gesund; Zeilenzahlen der DB wachsen nur (vorher/nachher
  `verify-db`).
- [x] AK9: [manuell] Neustart des Servers (`sudo reboot`, mit Zustimmung): beide Units laufen danach ohne Eingriff,
  die Runden gehen weiter; keine doppelten Entscheidungen oder Trades (Abfrage auf die eindeutigen Schlüssel ohne
  Treffer), alle Zeilen von vorher sind noch da.
- [x] AK10: [manuell] Backup-Timer ist aktiv und hat (bzw. per `systemctl start` einmal) eine Kopie mit Rechten 600
  erzeugt; Wiederherstellungsprobe: `restore` der Kopie in ein neues Verzeichnis, `verify-db` zeigt dieselben
  Zeilenzahlen wie beim Backup. Ablauf der echten Wiederherstellung (Dienst stoppen, DB beiseitelegen, nie löschen,
  Kopie einsetzen, starten) steht in `docs/betrieb/server.md`.
- [ ] AK11: Doku: `docs/betrieb/server.md` (Installation, Update, Rollback, Backup, Wiederherstellung, Logs,
  Drop-in für Variablen außerhalb von `secrets.env`), `CLAUDE.md` (Deploy- und Log-Befehle), ARCHITEKTUR –
  nur Platzhalter (`<alias>`, `<dienst>`); `docs-check` grün.
- [ ] AK12: Lint, Format, `mypy src tests tools`, shellcheck, CI grün.

## Abgrenzung
- Nicht Teil dieser Spec: Backups außerhalb des Servers (vom Nutzer verworfen, ADR-0018), Schemamigrationen (eigene
  Spec, wenn nötig), Monitoring/Benachrichtigungen, Risiko-Grenzen (Nr. 4), der 7-Tage-Lauf des Meilensteins
  (beginnt mit AK7, wird beim Meilenstein-Abschluss geprüft).
- Die Server-Härtung (Spec 002) bleibt unverändert; neue Pakete nur, wo nötig.
- Die Paper-Historie beginnt auf dem Server neu; lokale Test-DBs werden nicht übertragen.

## Relevante Entscheidungen & Learnings
- ADR-0018 (angenommen mit dieser Spec): Release je Commit per SSH, Backups täglich auf dem Server, automatischer
  Code-Rollback, `RestartPreventExitStatus=3`, Zeit-Synchronisation.
- ADR-0005 / ADR-0006: CX23, systemd, Admin mit sudo ohne Passwort, Dienstbenutzer ohne Shell; HTTP nur 127.0.0.1.
- ADR-0017: ein Prozess, Runde je Minute, Watchdog, Statusdatei (Grundlage des Gesundheitschecks), Exit-Codes.
- ADR-0016: genau ein Schreiber; Backup und `verify-db` lesen nur (`mode=ro`), halten nie den `WriterLock`.
- ADR-0015: `secrets.env` 600, nur über die Weboberfläche; nie übertragen, geloggt oder gesichert.
- ADR-0004: kein Echtgeld; der Server bekommt keine Bitvavo-Keys.
- ADR-0003: Übernommene Teile aus Arena@7563724 (`deploy_release.py`, `backup.py`, `operations.py`) mit Vermerk.
- LEARNINGS #ssh „Argumente mit Leerzeichen über ssh zerfallen“: entfernte Kommandozeile in doppelte, Werte in
  einfache Anführungszeichen. „sshd übernimmt den ersten Wert“: Wirkung immer mit dem Prüfbefehl belegen.
- `docs/betrieb/server.md` „Fallen“: ufw-Rate-Limit (≤ 6 SSH-Verbindungen in 30 s) → Befehle je Sitzung bündeln.
- LEARNINGS #windows/#encoding: Shell-Skripte mit LF (`.gitattributes`), Ausgaben UTF-8.
- LEARNINGS „Sich selbst neu ladende Seite …“ und #signal: bestehende Anker im Dienst/Web nicht anfassen.

## Ansatz
- **Python (`src/llm_trading_bot/ops/`):** `backup.py` mit `backup_db`, `verify_db`, `restore_db` (Online-Backup-API
  von einer `mode=ro`-Verbindung, Prüfung, `os.link`/`os.replace` auf neuen Namen, Rotation nach eigenem
  Namensmuster); CLI `backup`, `verify-db`, `restore`. Dienst: in `_wait_until` den Termin neu berechnen, wenn er
  mehr als eine Periode plus Versatz in der Zukunft liegt (Uhr zurückgesprungen).
- **Server (`deploy/release.sh`, Bash, root via sudo):** Pfade `/opt/llm-trading-bot/{releases,current}`,
  `/var/lib/llm-trading-bot`, `/var/backups/llm-trading-bot/{daily,pre-update}`; Dienstbenutzer als Argument.
  Units aus dem Release rendern (Platzhalter ersetzen), `systemd-analyze verify`, installieren. Gesundheitscheck über
  die Statusdatei und `systemctl show -p MainPID`. Testbar über Umgebungsvariablen für Wurzelpfad und Befehle (nur
  für die Tests; ohne sie gelten die echten Pfade).
- **Lokal (`scripts/deploy.sh`):** prüft Commit (auf `origin/main`, `gh run list --commit` grün), streamt
  `git archive` per `ssh <alias> "sudo …"` in eine Datei unter `/opt/llm-trading-bot/incoming/`, ruft dann
  `git show <sha>:deploy/release.sh | ssh <alias> "sudo bash -s -- deploy <sha> '<dienst>'"` auf. Zwei SSH-Verbindungen.
- **Erstinstallation:** `install` → `deploy`: ohne `secrets.env` startet nur die Weboberfläche; Einrichtungscode
  aus dem Journal; nach der Einrichtung `deploy` erneut.
- Verworfen: siehe ADR-0018 (git auf dem Server, In-Place-Checkout, Docker, Off-Site-Backups, Daten-Rollback).

## Plan
- [x] 1. ADR-0018 annehmen; `ops/backup.py` + CLI `backup`, `verify-db`, `restore` mit Tests (AK1, AK2)
- [x] 2. Uhrsprung im Dienst + Test (AK3)
- [x] 3. Units: `RestartPreventExitStatus=3`, Backup-Service und -Timer; `check-units.sh` und Datei-Tests (AK4)
- [x] 4. `deploy/release.sh` + Sandbox-Tests in der CI (AK5)
- [x] 5. `scripts/deploy.sh` + Tests (AK6); Doku `docs/betrieb/server.md`, `CLAUDE.md`, ARCHITEKTUR (AK11)
- [ ] 6. Mit Zustimmung auf dem Server: `install`, `deploy`, Einrichtung durch den Nutzer, `deploy` (AK7)
- [ ] 7. Mit Zustimmung: Update, `rollback`, Backup und Wiederherstellungsprobe, Neustart (AK8–AK10); STAND

## Risiken & offene Fragen
- **Backups nur auf dem Server** (Entscheidung des Nutzers): Fällt der Server aus oder wird er gelöscht, ist die
  Paper-Historie weg. Hetzner-Backups lassen sich später mit einem Klick zuschalten.
- **PyPI beim Update:** `uv sync` braucht Netz; scheitert es, bricht `deploy` vor dem Stoppen ab – der alte Stand
  läuft weiter.
- **Erste Installation ohne Key:** Die Standard-Depots brauchen `OPENAI_API_KEY`; deshalb erst Weboberfläche, dann
  Bot. Ohne diesen Schritt würde der Bot alle 15 s mit Exit 1 neu starten.
- **Rollback nach einem Schemawechsel** wäre nicht mehr möglich; heute gibt es keinen (Schema v2). Eine Spec mit
  Migration muss das eigens regeln.
- **ufw-Rate-Limit:** `scripts/deploy.sh` öffnet zwei Verbindungen; Prüfbefehle bündeln.
- **Neustart 04:00 UTC durch unattended-upgrades** prüft die Neustartfestigkeit regelmäßig mit; der Backup-Timer
  liegt bewusst davor (03:30).
- Platz: Releases mit venv je ~100–200 MB (drei behalten), Backups 14 × DB-Größe; 38 GB reichen lange.

---

## Verlauf
<!-- Während der Umsetzung: Abweichungen vom Plan, Überraschungen, Zwischenstände (datiert, knapp). -->
- 2026-11-06: Schritte 1–5 umgesetzt. uv 0.13.5 (2026-10-22) statt der lokal genutzten 0.13.7 – ältere, abgehangene
  Version; Prüfsumme aus Release-Datei und GitHub-API übereinstimmend. Releases werden am endgültigen Ort gebaut, nicht
  verschoben: uv schreibt den absoluten venv-Pfad in die Startskripte. Projekt mit `--no-editable` in die venv.
  `release.sh` ist ganz in Funktionen gefasst (`main` am Ende) und liest kein stdin, weil es selbst über stdin kommt.
  Die Sandbox-Tests (`tests/deploy/`) brauchen GNU-Werkzeuge und `flock` und laufen nur in der Linux-CI (hier gibt es
  weder WSL noch `flock`); CI: 19 + 10 Tests grün, `systemd-analyze` akzeptiert alle vier Units.
- 2026-11-06: Ein voller lokaler Lauf unter hoher Last (190 s statt ~70 s) hatte einen Fehlschlag, der sich in vier
  weiteren Läufen nicht wiederholte; welcher Test, ist unbekannt (pytest-Cache im Dropbox-Ordner wird nicht
  geschrieben). Kandidat: ein zeitabhängiger Test. Beobachten.
- 2026-11-06: Prüfung vor dem Server (Workflow, 4 Blickwinkel, je Befund Gegenprüfung): 20 von 24 Befunden bestätigt,
  kein Blocker, alle umgesetzt außer einem. Wichtigste: ohne Ausweich-Release stoppte ein gescheiterter
  Gesundheitscheck auch Weboberfläche und Backups (jetzt nur der Bot, deaktiviert); `rollback`/`status`/`install`
  verlangten eine grüne CI auf dem neuesten `main`-Commit (jetzt: neuester grüner Commit); eine abbrechende
  SSH-Sitzung beendete das Skript mitten im Update (jetzt HUP/PIPE ignoriert, alles auch in `deploy.log`); uv auf
  0.13.7 angehoben, weil `uv_build>=0.13.7` sonst ungepinnt von PyPI geladen würde (Test prüft die Grenze); Sperre
  in `/opt/llm-trading-bot/.deploy.lock` statt `/run/lock` (dessen Rechte `install -d` verändert hätte); Prüfung von
  `secrets.env`/Drop-ins vor dem Start der Weboberfläche; Ausweich-Release vor dem Stoppen geprüft; erneutes
  Deploy desselben Commits fällt auf das vorige Release zurück; Backup-Timer überspringt ohne DB; neue Tests für
  veraltete Statusdatei, Rollback der Units, Aufräumen, abbrechende Sitzung.
  **Bewusste Abwägung (ADR-0018):** Springt die Uhr zurück, kann eine schon gelaufene Minute erneut laufen; der
  Equity-Punkt dieses Zeitstempels wird dann ersetzt (Entscheidungen und Trades sind je Kerze eindeutig und bleiben
  unberührt). Die Alternative wäre, den Sprung abzuwarten (keine Runden). Das Log sagt es ausdrücklich.
- 2026-11-06: PR #15 gemergt (5358d59, CI grün). Auf dem Server mit Zustimmung: `install` (9 Änderungen: Verzeichnisse,
  uv 0.13.7, `systemd-time-wait-sync`), erstes `deploy` → nur Weboberfläche (keine `secrets.env`). Geprüft: Code
  root/755, Daten und Backups Dienstbenutzer/700, 8787 nur auf 127.0.0.1, Backup-Timer aktiv, NTP synchron. Der
  Nutzer hat per Tunnel Passwort, Key und sechs Depots eingerichtet (`secrets.env` 600); zweites `deploy` → Bot gesund:
  erste Runde `ok` in 9,5 s, beide GPT-Depots `ASKED`. Nach dem Deploy ist ein neues Login nötig (Sitzungen nur im
  Speicher, ADR-0015). Kosmetik: Die Meldung nannte beim erneuten Deploy desselben Commits ihn selbst als „previous“.
- 2026-11-07 (UTC 2026-11-06 23:02–23:14): AK8–AK10 mit Zustimmung auf dem Server.
  - AK8: Update 5358d59 → b20b7b8 gesund (Backup vorher in `pre-update/`, previous vermerkt), `rollback` gesund,
    erneutes `deploy` gesund (Release wiederverwendet). `verify-db` jeweils: Zeilen nur gewachsen (Equity 161 → 182).
  - AK10: `systemctl start llm-trading-bot-backup` → `Result=success`, Kopie 600/Dienstbenutzer, geprüft;
    `restore` in `restore-test/` → dieselben Zeilenzahlen; `restore` auf die laufende DB → verweigert (Exit 1);
    Testverzeichnis entfernt.
  - AK9: `systemctl reboot` um 23:05:32, Boot 23:05:36; alle Units ohne Eingriff aktiv, NTP synchron, Dienst ab
    23:05:55, 8 Runden bis 23:14; `verify-db`: Integrität und eindeutige Schlüssel ok, Equity 189 → 245, nichts fehlt.
  - **Befund:** Seit 23:06 sind die Runden `incomplete`: Bitvavo hat für ETH-EUR 23:03–23:05 keine 1m-Kerze (drei
    Minuten ohne Handel). Bis zu zwei fehlende Minuten werden aufgefüllt (Spec 001, wie Arena), eine längere Lücke in
    den letzten 60 Kerzen sperrt ETH-Entscheidungen für bis zu eine Stunde (fail closed, gewollt). BTC unberührt.
    Folge für den Gesundheitscheck (AK5 verlangt eine Runde `ok`): Ein Deploy in so einer Stunde würde fälschlich
    zurückrollen. Entscheidung des Nutzers offen. Learning angelegt.

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
