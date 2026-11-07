# ADR-0020: Deploy wie ADR-0018; gesund ist auch eine Runde, die nur an Marktdaten scheitert

- **Status:** angenommen
- **Löst ab:** [ADR-0018](0018-deploy-releases-backup-rollback.md)
- **Datum:** 2026-11-07
- **Bereich:** #betrieb #deploy #backup #hetzner #marktdaten

## Kontext
Der Dienst (`trading-bot run`, ADR-0017) und die Weboberfläche (ADR-0015/0016) sind fertig, die systemd-Dateien
liegen geprüft in `deploy/`. Für M1 müssen sie auf dem Hetzner-CX23 laufen (ADR-0005), Updates vertragen, einen
Neustart überstehen, und die Handelshistorie muss sich aus Backups wiederherstellen lassen. Zugang: Admin-Benutzer
mit Key und sudo ohne Passwort, Dienst unter eigenem Systembenutzer ohne Shell (ADR-0006). Das Repo ist öffentlich.
Die Arena (`deploy_release.py`, `backup.py`, Stand 7563724) liefert den Ablauf „Kandidat bauen, während der alte
Dienst läuft → stoppen → sichern → umschalten → Gesundheit prüfen → bei Fehler nur Code zurück, nie die Daten“,
arbeitet aber im Home eines einzigen Benutzers ohne Paketabhängigkeiten und ist auf echter Hardware nie abgenommen.
Der Nutzer hat am 2026-11-06 entschieden: Code von hier per SSH, Backups nur auf dem Server, täglich mit 14 Tagen
Aufbewahrung, automatischer Rollback, wenn der Dienst nach einem Update keine gesunde Runde schafft.

Nachtrag 2026-11-07: Beim Neustart-Test (Spec 009 AK9) waren alle Runden eine Stunde lang `incomplete`, weil
Bitvavo für ETH-EUR drei Minuten ohne Handel keine Kerze lieferte (Learning #marktdaten). Ein Deploy in so einer
Stunde wäre nach ADR-0018 fälschlich zurückgerollt, obwohl der neue Code eine ganze Runde lief. Der Nutzer hat am
2026-11-07 entschieden, solche Runden als gesund zu werten. Alles Übrige aus ADR-0018 bleibt unverändert und ist
hier vollständig wiederholt.

## Entscheidung
- **Release je Commit:** Ein Update überträgt `git archive` eines Commits von `main`, dessen CI grün ist, per SSH
  auf den Server. Jeder Commit wird zu `/opt/llm-trading-bot/releases/<sha>/` (root, nur lesbar für den Dienst) mit
  eigener `.venv` aus `uv sync --frozen --no-dev` gegen das System-Python 3.12. Aktiv ist, worauf der Symlink
  `/opt/llm-trading-bot/current` zeigt; umgeschaltet wird atomar (`mv -T`). Die zwei letzten Vorgänger bleiben liegen.
  uv wird in einer festen Version mit geprüfter SHA-256 installiert. Archive mit Secrets oder DB-Dateien werden
  abgelehnt.
- **Daten getrennt vom Code:** `/var/lib/llm-trading-bot/` (Dienstbenutzer, 700) für `bot.db`, `secrets.env`,
  `web-auth.json`, `service-status.json`. Ein Update fasst dieses Verzeichnis nie an (außer dem Lese-Backup); die
  Prüfsumme von `secrets.env` wird vor und nach dem Wechsel verglichen. Variablen außerhalb von `secrets.env`
  (Gebühren, Takt-Parameter, `LOG_LEVEL`) setzt der Admin per systemd-Drop-in; ein Update ändert keine Drop-ins.
- **Ablauf eines Updates:** Kandidat entpacken und bauen, während der alte Dienst läuft; Kandidat prüft die
  bestehende DB nur lesend (Schemaversion). Dann Dienst und Weboberfläche stoppen, Backup vor dem Update, Units aus
  dem Kandidaten installieren, umschalten, starten. **Gesund** heißt: Die Statusdatei meldet innerhalb von 3 min
  die PID des neuen Prozesses und eine Runde, die nach dem Start begann, mit Ergebnis `ok` – oder `incomplete`,
  wenn das gemeldete Problem ein `MarketDataError` ist (Zustand dann `degraded`). Grenze: Die Statusdatei nennt
  nur das erste Problem einer Runde (Kurse vor Märkten vor GPT); ein zugleich kaputter GPT-Pfad fällt in so einer
  Runde nicht auf. Sonst
  schaltet das Skript auf das vorige Release zurück (Code und Units, **nie** die Datenbank) und endet mit Fehler.
- **Backups nur auf dem Server:** `trading-bot backup` kopiert die DB über die SQLite-Online-Backup-API (der
  Dienst läuft weiter), prüft die Kopie (`integrity_check`, Schemaversion, Zeilenzahlen), schreibt sie mit Rechten
  600 unter einem neuen Namen und behält nur in ihrem Verzeichnis die 14 neuesten. Ein systemd-Timer sichert täglich
  um 03:30 UTC (`Persistent=true`) nach `/var/backups/llm-trading-bot/daily/`; vor jedem Update entsteht eine Kopie
  in `…/pre-update/` (5 bleiben). `secrets.env` und `web-auth.json` werden nicht gesichert. Wiederhergestellt wird
  nur in ein neues Ziel, nie über eine bestehende DB.
- **Neustarts:** `Restart=on-failure` bleibt; Exit 3 (ein anderer Schreiber hält die DB) startet nicht neu
  (`RestartPreventExitStatus=3`) – zwei Schreiber dürfen sich nie abwechseln. Exit 1 (Einstellungen/Schema) startet
  weiter neu, damit eine in der Weboberfläche korrigierte Einstellung ohne Eingriff greift; ein Schema-Fehler nach
  einem Update fällt durch den Gesundheitscheck auf und wird zurückgerollt.
- **Zeit:** `systemd-time-wait-sync.service` ist aktiv, damit `time-sync.target` beim Booten auf NTP wartet. Springt
  die Uhr im Betrieb zurück, berechnet der Dienst seinen nächsten Termin neu, statt entsprechend lange zu warten.

## Verworfene Alternativen
- **Server holt den Code von GitHub (git clone/checkout):** braucht git und Netzzugriff auf GitHub für den Code; vom
  Nutzer zugunsten der Übertragung per SSH verworfen.
- **Checkout an Ort und Stelle wie die Arena:** venv und Code wären während des Wechsels gemischt; kein atomarer
  Rückweg.
- **Docker:** wie ADR-0005 – unnötige Schicht für einen Python-Dienst.
- **Backups außerhalb des Servers (Hetzner-Backups, Storage Box, Kopie auf den PC):** vom Nutzer am 2026-11-06
  verworfen; bei Verlust des Servers ist die Paper-Historie verloren. Nachrüstbar ohne Änderung an diesem Ablauf.
- **Stündliche Backups:** mehr Platz (die DB wächst mit Minutenrunden), Paper-Verlust von bis zu einem Tag ist
  vertretbar; vom Nutzer zugunsten täglich verworfen.
- **Gesund nur bei Ergebnis `ok` (ADR-0018):** rollt bei nächtlichen Marktdaten-Lücken (bis zu einer Stunde je
  Lücke) jedes Update fälschlich zurück; vom Nutzer am 2026-11-07 verworfen.
- **Daten-Rollback (Backup über die DB kopieren) bei einem fehlgeschlagenen Update:** würde Trades seit dem Update
  löschen und die Historie still zurücksetzen (verboten laut CLAUDE.md).
- **Unbegrenzte Neustarts auch bei Exit 3 (wie Arena):** zwei Schreiber würden sich abwechselnd die Sperre nehmen.

## Konsequenzen
- Positiv: Updates und Rollback sind ein Befehl von hier und atomar; die Daten bleiben unberührt; ein Neustart des
  Servers bringt den letzten aktiven Stand zurück; Backups sind geprüft und wiederherstellbar.
- Negativ / Preis: Backups liegen auf demselben Server; jedes Release hat eine eigene venv (Platz, Bauzeit, PyPI
  muss beim Update erreichbar sein); Schemaänderungen brauchen künftig eine eigene Migrations-Spec (dann ist kein
  automatischer Rollback mehr möglich).
- Preis der Lockerung: Ein Release, das zusätzlich zu einer Marktdaten-Lücke einen anderen Fehler hat, kann als
  gesund gelten; ebenso ein Fehler im Code, der fälschlich `MarketDataError` meldet.
- Neu bewerten, wenn: Echtgeld kommt (M2: Backups außerhalb des Servers), die DB oder die Releases den Platz knapp
  machen, oder eine Schemamigration ansteht.

## Code-Anker
- `deploy/release.sh` (`healthy`), `scripts/deploy.sh`, `deploy/llm-trading-bot-backup.service`/`.timer`
- `src/llm_trading_bot/ops/backup.py`
