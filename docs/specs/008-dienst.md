# Spec 008: Dienst – Paper-Runde jede Minute, sauberer Stopp, Health, Logging

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s008-dienst` · **Meilenstein:** M1 (Nr. 5)
- **Erstellt:** 2026-11-06

## Ziel
Der Bot läuft dauerhaft als ein Prozess (`trading-bot run`), der jede Minute eine Paper-Runde ausführt, Änderungen
aus der Weboberfläche ohne Neustart übernimmt, Fehler meldet statt abzustürzen, bei Hängern neu startet und auf
SIGTERM sauber endet. Ob der Dienst läuft, zeigen die Übersicht (Statusdatei) und systemd (Watchdog). Die
systemd-Dateien liegen geprüft bereit; installiert werden sie mit Nr. 6 (ADR-0017).

## Akzeptanzkriterien
- [ ] AK1: Takt: `trading-bot run --db <pfad> [--secrets <datei>]` startet Runden jeweils 5 s nach dem Minutenwechsel
  (UTC). Dauert eine Runde länger als bis zum nächsten Termin, folgt die nächste am nächsten freien Termin – keine
  nachgeholten Runden. — Nachweis: `tests/service/test_schedule.py` (Uhr und Warten simuliert, Grenzwerte
  :04,999 / :05,000 / Überlauf um 1 und um 3 Termine)
- [ ] AK2: Ein Schreiber: Der Dienst hält den `WriterLock` die ganze Laufzeit; ein zweites `run` oder `paper-cycle`
  wird mit Exit 3 abgelehnt; die Weboberfläche liest währenddessen (ADR-0016). — Nachweis:
  `tests/service/test_run.py::test_second_writer_is_refused_*`, `test_web_reads_while_the_service_runs`
- [ ] AK3: Konfiguration je Runde: Eine Änderung in `secrets.env` greift in der nächsten Runde (z. B. `BOT_AGENTS`,
  Effort, Budget, Key). Ungültige Werte → letzte gültige Konfiguration bleibt, eine Warnung je neuem Fehlerbild
  (nicht jede Minute). Ungültig schon beim Start → Exit 1. Ein neu gewähltes Depot bekommt 10.000 € Startkapital;
  ein abgewähltes behält Historie und Bestand. Neuer Key → neuer OpenAI-Client. — Nachweis:
  `tests/service/test_config_reload.py`
- [ ] AK4: Fehler: Wirft eine Runde (z. B. Bitvavo nicht erreichbar), wird Typ und bereinigte Meldung geloggt, die
  Statusdatei zeigt `degraded` und die Zahl der Fehler in Folge, die nächste Runde läuft normal; nach einer guten
  Runde wieder `running`. Falsche Schemaversion → Exit 1 ohne Änderung an der DB. Eine Runde über 300 s → Status
  `stalled`, Exit 4. — Nachweis: `tests/service/test_run.py::test_failing_round_*`, `test_schema_*`, `test_stalled_*`
- [ ] AK5: Stopp: SIGTERM/SIGINT → keine neue Runde; eine laufende Runde startet keine neue GPT-Anfrage mehr
  (`paper_cycle` bekommt dafür eine Stopp-Abfrage), bewertet die Depots aber noch; Ende spätestens 75 s nach dem
  Signal, sonst harter Exit mit Logzeile. Danach Status `stopped`, Lock freigegeben, Exit 0. Ohne laufende Runde
  endet der Dienst sofort (Warten wird unterbrochen). — Nachweis: `tests/service/test_shutdown.py`,
  `tests/paper/test_cycle.py::test_stop_request_skips_gpt_*`
- [ ] AK6: systemd-Benachrichtigung über `NOTIFY_SOCKET` ohne Fremdpaket: `READY=1` nach DB-Öffnung und gültiger
  Konfiguration, `WATCHDOG=1` aus der Hauptschleife mindestens alle 20 s (auch während einer langen Runde, solange
  sie unter 300 s bleibt), `STATUS=…`, `STOPPING=1` beim Stopp; ohne `NOTIFY_SOCKET` keine Wirkung. — Nachweis:
  `tests/service/test_notify.py` (echter Unix-Datagramm-Socket; unter Windows übersprungen)
- [ ] AK7: Statusdatei `service-status.json` neben der DB, atomar geschrieben (Rechte 600 unter POSIX): Zustand
  (`starting`/`running`/`degraded`/`stalled`/`stopping`/`stopped`), PID, Start, Takt, letzte Runde (Beginn, Dauer,
  Ergebnis, Fehlertyp), Fehler in Folge, nächster Termin, Konfigurationswarnung. Nie ein Key oder Secret. —
  Nachweis: `tests/service/test_status.py`
- [ ] AK8: Übersicht: neues Lebenszeichen „Dienst“ aus der Statusdatei – „läuft“ (Zustand `running` und letzte
  Runde ≤ 3 min alt), „verzögert“ (`degraded` oder ≤ 15 min), „steht“ (sonst, auch `stopped`/`stalled`), „kein Dienst“
  ohne Datei. Gelesen nur lesend. — Nachweis: `tests/web/test_overview.py::test_service_pulse_*`
- [ ] AK9: Logging auf stdout: je Runde eine INFO-Zeile (Dauer, GPT-Ergebnis je Depot mit Kosten, Zahl der Trades,
  Equity je Depot), je Trade eine Zeile, Fehler als WARNING/ERROR; Stufe per `LOG_LEVEL` (Standard `INFO`). Ein Key
  (`sk-…`) erscheint nie im Log, auch nicht, wenn eine Fehlermeldung ihn enthält. — Nachweis:
  `tests/service/test_logging.py`
- [ ] AK10: `deploy/llm-trading-bot.service`, `deploy/llm-trading-bot-web.service`, `deploy/journald@llm-trading-bot.conf`
  mit Platzhaltern (`__SERVICE_USER__`, `__APP_DIR__`, `__DATA_DIR__`): `Type=notify`, `NotifyAccess=main`,
  `WatchdogSec=60`, `Restart=on-failure`, `TimeoutStopSec=90`, Härtung (`ProtectSystem=strict`, `ReadWritePaths` nur
  Datenverzeichnis, `NoNewPrivileges`, `PrivateTmp`, `UMask=0077`), Journal ≤ 200 MB / 14 Tage. CI prüft die
  Units mit Testwerten per `systemd-analyze verify`; keine echten Server-Daten im Repo. — Nachweis:
  `scripts/check-units.sh` in der CI, `tests/test_deploy_files.py`
- [ ] AK11: [manuell] Lokal `trading-bot run` auf einer Test-DB mindestens 5 Runden (klassische Depots oder mit
  Key): je Minute eine Logzeile, Übersicht zeigt „Dienst läuft“; Strg+C beendet sauber (Status `stopped`).
- [ ] AK12: Lint, Format, `mypy src tests tools`, shellcheck, CI grün. — Nachweis: Befehle aus `CLAUDE.md`, CI

## Abgrenzung
- Nicht Teil dieser Spec: Installation auf dem Server, Benutzer/Verzeichnisse/Backup/Update (Nr. 6, nur mit
  Zustimmung), schneller Kurs-/Risiko-Worker und Drawdown-Stopp (Nr. 4), HTTP-Health-Endpunkt, Benachrichtigungen,
  Start/Stopp über die Weboberfläche, Log-Versand.

## Relevante Entscheidungen & Learnings
- ADR-0017 (angenommen mit dieser Spec): ein Prozess, Runde pro Minute, Watchdog + Statusdatei, Config-Reload.
- ADR-0005 / ADR-0006: systemd, unprivilegierter Dienstbenutzer, Daten außerhalb des Codes, `.env`/`secrets.env` 600.
- ADR-0016: genau ein Schreiber (der Dienst), Weboberfläche nur lesend; Statusdatei liegt neben der DB.
- ADR-0013 / ADR-0012: GPT nur bei Bedarf, Budget je Depot – der Dienst ändert daran nichts; eine abgebrochene
  Anfrage behält ihre Reservierung.
- ADR-0004: kein Echtgeld, `live` bleibt gesperrt.
- LEARNINGS #windows (Lock-Freigabe, Konsolen-Encoding): Tests mit Lock-Wiederholung, UTF-8-Ausgabe.
- Arena@7563724 `main.py`/`operations.py`: SIGTERM-Ereignis, Stopp-Frist, harter Exit, wenn ein Schreiber nicht
  endet; Unit-Optionen und Journal-Limit als Vorlage (Herkunftsvermerk bei übernommenen Teilen, ADR-0003).

## Ansatz
- Neues Paket `service/`: `schedule.py` (nächster Termin, reine Funktion), `notify.py` (sd_notify per
  `socket.AF_UNIX`), `status.py` (Statusdatei über `web/files.write_private` bzw. gemeinsamen Helfer), `logs.py`
  (Logger mit Redaktion), `run.py` (Hauptschleife: Lock, DB, Config-Reload, Runde in einem Worker-Thread, Warten
  mit Stopp-Ereignis, Watchdog-Pings, Stall- und Stopp-Frist).
- `paper_cycle` bekommt optional `stop_requested: Callable[[], bool]`, vor dem GPT-Schritt geprüft.
- `paper/db.py`: `ensure_portfolios(con, agents)` für neu gewählte Depots (dasselbe `INSERT OR IGNORE` wie `open_db`).
- `cli.py`: Befehl `run`; Exit-Codes 0/1/3/4. Übersicht: Lebenszeichen „Dienst“ aus der Statusdatei.
- `deploy/`: zwei Units + Journal-Konfiguration mit Platzhaltern; `scripts/check-units.sh` für die CI.
- Verworfen: siehe ADR-0017 (Arena-Threads, HTTP-Health, Timer, Config nur beim Start).

## Plan
- [x] 1. ADR-0017, `service/schedule.py` + `paper_cycle`-Stopp-Abfrage + `ensure_portfolios` mit Tests (AK1, Teile AK3/AK5)
- [x] 2. `service/run.py` Grundschleife: Lock, DB, Runden, Fehlerbehandlung, Stall-Grenze, Config-Reload (AK2–AK4)
- [x] 3. Stopp per Signal mit Frist, Warten unterbrechbar (AK5)
- [x] 4. `notify.py`, `status.py`, `logs.py` mit Redaktion (AK6, AK7, AK9)
- [x] 5. CLI `run`, Lebenszeichen „Dienst“ in der Übersicht (AK2, AK8)
- [x] 6. `deploy/`-Units, Journal-Konfiguration, `scripts/check-units.sh` + CI-Schritt (AK10); Doku: README,
  ARCHITEKTUR, `docs/betrieb/server.md` (Abschnitt Dienst, Installation folgt Nr. 6), STAND; manuelle Prüfung AK11

## Risiken & offene Fragen
- `sd_notify` über `AF_UNIX`-Datagramme gibt es unter Windows nicht → dort nur Tests überspringen, Funktion ist
  ohne `NOTIFY_SOCKET` ohnehin wirkungslos.
- Eine hängende GPT-Anfrage (bis ~3 min mit Wiederholungen) verzögert die Runde; die Stall-Grenze (300 s) liegt
  darüber, die Stopp-Frist (75 s) darunter → beim Stopp kann eine Anfrage hart abgebrochen werden (Reservierung bleibt,
  sicher, aber Budget kurzzeitig belegt).
- `systemd-analyze verify` in der CI prüft nur Syntax und Pfade mit Testwerten, nicht das Verhalten auf dem Server
  (das folgt in Nr. 6).
- Signale treffen nur den Hauptthread: Runden laufen deshalb in einem Worker-Thread, die Hauptschleife wartet,
  pingt den Watchdog und überwacht Fristen.

---

## Verlauf
- 2026-11-06: Schritte 2–4 in einem Commit, weil die Schleife Status, Watchdog und Logging direkt braucht.
  Die Hauptschleife wartet in Scheiben von 1 s (Stopp unter Windows sonst bis zu 20 s verzögert; Frist ab
  Bemerken ≤ 76 s). `open_db(check_same_thread=False)`, damit die Runde im Worker-Thread dieselbe Verbindung nutzt
  (immer nur ein Thread). Stopp vor dem GPT-Schritt erscheint als Status `STOPPING`, zählt nicht als Fehler.
  Key-Redaktion bewusst ohne Wortgrenze (lieber ein verstümmeltes Wort als ein Key im Log).
  Hart-Exit bei Stall und überschrittener Stopp-Frist: Exit 4.
- 2026-11-06: Probelauf lokal mit echten Bitvavo-Daten (`trend,breakout`): 3 Runden je um :05, 1,3–4,8 s, Status
  korrekt (AK11 selbst bleibt beim Nutzer). Buy-&-Hold-Käufe bekommen eine eigene Logzeile.
- 2026-11-06: Review (5 Prüfer, je Befund Gegenprüfung): 3 von 12 Befunden bestätigt und behoben – seltener
  Deadlock, wenn der Signal-Handler `stop.set()` ausführt, während der Hauptthread in `stop.wait()` steckt (jetzt
  `sleep` + `is_set`, Regressionstest im Unterprozess, Learning #signal); fehlender Test „Stopp während des
  Wartens“ (AK5); ARCHITEKTUR nannte noch `/etc/llm-trading-bot/env`. Dazu Tests für GPT-Kosten in der Rundenzeile
  (AK9) und READY erst nach geöffneter DB (AK6).
- 2026-11-06: AK11-Test beim Nutzer: „Letzter Zyklus“ schien eingefroren. Ursache: Die Übersicht lud sich alle
  60 s neu (Spec 007 AK9), also im Gleichtakt mit den 60-s-Runden – jedes Laden zeigte dasselbe Alter. Jetzt Neuladen
  alle 15 s, Lebenszeichen mit Sekunden, die Kennzahl „Letzter Zyklus“ wird per Beschriftung statt Position gewählt
  (seit dem Dienst-Lebenszeichen stand dort an Position 0 der Dienst).
- 2026-11-06: Auf Wunsch des Nutzers (beim AK11-Test): Der Banner der Übersicht zeigt statt fester Hinweise die
  gespeicherten Kurse (Mitte, Geld/Brief, Spread, Kerzenschluss, Stand) und jedes Depot mit Wert und Ergebnis. Er läuft
  über das Neuladen hinweg an derselben Stelle weiter (Phasen-Klasse aus der Uhr, kein Skript), und die sich selbst
  neu ladende Seite verzichtet auf die Einblend-Animation. Login- und Einstellungsseite behalten die festen Hinweise.

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
