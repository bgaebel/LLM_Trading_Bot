# Server: Einrichtung und Prüfung

Zielsystem laut ADR-0005 (abgelöst durch ADR-0024, Betrieb unverändert): Hetzner Cloud CX23, Ubuntu 24.04 LTS.
Zugangsmodell laut ADR-0006.
**Das Repo ist öffentlich:** IP, Hostname, Benutzernamen und SSH-Alias stehen nur lokal (`~/.ssh/config`),
hier nur Platzhalter: `<server>` (IP/Host), `<admin>` (Admin-Benutzer), `<dienst>` (Systembenutzer des Bots),
`<alias>` (lokaler SSH-Alias), `<key>` (Name des lokalen Keys).

## Was eingerichtet ist (Spec 002)
| Bereich | Zustand |
|---|---|
| SSH | nur Public Key; kein Root-Login, kein Passwort-Login (`/etc/ssh/sshd_config.d/01-hardening.conf`) |
| Benutzer | `<admin>` mit Key und `sudo` ohne Passwort; `<dienst>` als Systembenutzer ohne Shell und ohne sudo |
| Firewall | ufw: eingehend 22/tcp mit Rate-Limit (max. 6 Verbindungen in 30 s je Quell-IP), ausgehend frei; mit Spec 013 zusätzlich 80/tcp und 443/tcp für Caddy (Abschnitt „Weboberfläche öffentlich“) |
| Updates | unattended-upgrades: Sicherheitsupdates automatisch, Neustart bei Bedarf um 04:00 UTC |
| Zeit | UTC, NTP über systemd-timesyncd |

## Neu einrichten (frischer Server)
Voraussetzung: Der Server wurde mit dem lokalen Key `<key>` für root angelegt.

1. Lokal den alten Host-Key entfernen, falls die IP vorher anders vergeben war: `ssh-keygen -R <server>`.
   Bei einer Warnung „REMOTE HOST IDENTIFICATION HAS CHANGED" nur weitermachen, wenn sicher ist, dass der Server
   neu ist (Fingerprint in der Hetzner-Konsole prüfen: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`).
2. Phase 1 (Root- und Passwort-Login bleiben noch offen):
   ```bash
   ssh root@<server> "bash -s -- prepare <admin> <dienst> '$(cat ~/.ssh/<key>.pub)'" < deploy/setup-server.sh
   ```
3. In einer **neuen** Sitzung prüfen, sonst nicht weitermachen:
   ```bash
   ssh <admin>@<server> sudo -n true && echo OK
   ```
4. Lokalen Alias in `~/.ssh/config` anlegen (`Host <alias>`, `HostName <server>`, `User <admin>`,
   `IdentityFile ~/.ssh/<key>`, `IdentitiesOnly yes`).
5. Phase 2 (sperrt Root und Passwörter, prüft die wirksame sshd-Konfiguration):
   ```bash
   ssh <alias> "sudo bash -s -- lock <admin>" < deploy/setup-server.sh
   ```

Beide Phasen sind idempotent: Ein erneuter Lauf meldet `unchanged` und `changes: 0`. `changes` zählt nur die
Konfiguration; `prepare` spielt zusätzlich anstehende Paket-Updates ein (`apt-get upgrade`).

## Prüfbefehle
```bash
ssh <alias> 'sudo -n sshd -T | grep -E "^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication) "'
ssh <alias> 'sudo -n ufw status verbose'
ssh <alias> 'timedatectl show -p Timezone -p NTPSynchronized'
ssh <alias> 'sudo -n unattended-upgrade --dry-run >/dev/null && echo OK'
ssh root@<server> true    # muss mit "Permission denied (publickey)" scheitern
```

## Notzugang
Bei Aussperrung (Key verloren, sshd-Fehler): Hetzner Cloud Console → Server → **Konsole** (Login direkt am
Bildschirm). Root hat dort kein Passwort; in der Hetzner-Oberfläche „Root-Passwort zurücksetzen" nutzen,
danach `/etc/ssh/sshd_config.d/01-hardening.conf` prüfen bzw. `authorized_keys` reparieren.

## Fallen
- **ufw-Rate-Limit:** Viele SSH-Verbindungen kurz hintereinander (Skripte, Schleifen) werden nach 6 in 30 s
  verworfen und sehen wie ein Ausfall aus. Ein paar Sekunden warten, Befehle in einer Sitzung bündeln.
- **cloud-init:** Viele Ubuntu-Images setzen `PasswordAuthentication yes` in `50-cloud-init.conf` (unser Hetzner-Image nicht). sshd übernimmt den
  ersten Wert, deshalb heißt der eigene Drop-in `01-…`. Immer mit `sshd -T` prüfen, nicht mit der Datei.
- **Argumente über ssh:** `ssh` fügt die entfernten Argumente zu einer Kommandozeile zusammen. Werte mit
  Leerzeichen (Public Key) brauchen innere einfache Anführungszeichen, wie oben.

## Optional (manuell in der Hetzner-Oberfläche)
- Hetzner-Cloud-Firewall mit denselben Regeln als zweite Schicht vor dem Server.
- Hetzner-Backups oder ein Snapshot nach der Einrichtung – **erst nach Anpassung der Datenschutzerklärung** (Spec 014, ADR-0025): Sie enthalten die Journal-Dateien mit IP-Adressen und verlängern deren Speicherdauer.

## Bot-Betrieb (Spec 008/009, ADR-0017/0018)
Alles läuft von hier über `scripts/deploy.sh` (Git Bash) und nur mit Zustimmung des Nutzers. Das Skript schickt
nur einen Commit von `origin/main` mit grüner CI auf den Server (`git archive`) und führt dort `deploy/release.sh`
aus demselben Commit aus. `<dienst>` ist der Systembenutzer aus Spec 002.

| Pfad auf dem Server | Inhalt | Rechte |
|---|---|---|
| `/opt/llm-trading-bot/releases/<sha>/` | ein Release je Commit mit eigener `.venv` | root, 755 |
| `/opt/llm-trading-bot/current`, `…/previous` | Symlinks auf das aktive und das vorige Release | root |
| `/var/lib/llm-trading-bot/` | `bot.db`, `observations.db` (ab Spec 015), `secrets.env`, `web-auth.json`, `service-status.json` | `<dienst>`, 700 / 600 |
| `/var/backups/llm-trading-bot/{daily,pre-update}/` | geprüfte Kopien von `bot.db` (14 bzw. 5) | `<dienst>`, 700 / 600 |
| `/var/backups/llm-trading-bot/observations/{daily,pre-update}/` | geprüfte Kopien von `observations.db` (7 bzw. 3; wächst etwa 2 MB am Tag) | `<dienst>`, 700 / 600 |
| `/usr/local/bin/uv` | uv in fester Version (SHA-256 geprüft) | root |

Units: `llm-trading-bot` (Runde je Minute), `llm-trading-bot-web` (127.0.0.1:8787),
`llm-trading-bot-backup.timer` (täglich 03:30 UTC). Exit-Codes von `run`: 0 gestoppt, 1 Einstellungen/Schema
ungültig (Neustart nach 15 s, damit eine korrigierte Einstellung greift), 3 andere Schreibsperre (**kein**
Neustart, ADR-0018), 4 Runde hing bzw. Stopp-Frist überschritten (Neustart).

### Erstinstallation
```bash
bash scripts/deploy.sh <alias> <dienst> install
```
```bash
bash scripts/deploy.sh <alias> <dienst> deploy
```
Ohne `secrets.env` startet `deploy` nur die Weboberfläche und sagt das. Einrichtungscode aus dem Journal holen,
Tunnel öffnen (Abschnitt „Weboberfläche per SSH-Tunnel"), Passwort, Key und Depots speichern, dann `deploy` noch
einmal – jetzt startet der Bot mit Gesundheitscheck:
```bash
ssh <alias> 'sudo journalctl --namespace=llm-trading-bot -u llm-trading-bot-web -n 20 --no-pager'
```

### Update, Rollback, Status
```bash
bash scripts/deploy.sh <alias> <dienst> deploy
```
Ohne Commit-Angabe gilt `origin/main`; ein bestimmter Commit als viertes Argument. Ablauf: Release bauen und die
DB lesend prüfen, während der alte Dienst läuft → stoppen → Backup nach `pre-update/` → Units installieren →
umschalten → starten → bis zu 3 min auf eine Runde mit Ergebnis `ok` (oder `incomplete` nur wegen Marktdaten:
Bitvavo lässt Minuten ohne Handel aus, ETH-EUR ist nachts dann bis zu einer Stunde ohne Entscheidung). Sonst schaltet das Skript automatisch auf das
vorige Release zurück (Code und Units, **nie** die Datenbank) und endet mit Fehler.
Die Beobachtungs-DB (`observations.db`, Spec 015) wird direkt nach `bot.db` nach `observations/pre-update/`
gesichert (3 bleiben). Scheitert das, steht `WARNING: the backup of observations.db before the update failed` im
Ablauf – der Deploy geht trotzdem weiter. Fehlt die Datei noch (vor der ersten Runde eines Releases ab 015), steht
dort nur `no observations.db yet`.
```bash
bash scripts/deploy.sh <alias> <dienst> rollback
```
Ein Rollback auf ein Release vor Spec 015 lässt `observations.db` und ihre Sicherungen unverändert liegen
(`release <sha> is from before Spec 015 …`); das alte Release liest und sichert sie nicht, die Aufzeichnung ruht
bis zum nächsten Deploy ab 015.
Ein Rollback auf ein Release vor Spec 020 braucht keine Vorbereitung: Die Auswahl der USDC-Depots steht in
`BOT_USDC_AGENTS`, das alte Release ignoriert sie und liest `BOT_AGENTS` wie bisher (Abschnitt „USDC-Depots“).
```bash
bash scripts/deploy.sh <alias> <dienst> status
```
`status` zeigt für die Aufzeichnung drei Zeilen: `observations: written <n>, failed <n>, last error: …` aus
`service-status.json` (bei einem Release vor 015 `no counters in the status file`), `observations.db: <Bytes> B`
(oder `missing`) und `latest observations backup: observations/<daily|pre-update>/observations-<UTC>.db`.
Gesund ist: `written` steigt von Runde zu Runde, `failed` bleibt 0.

### Risikostopp, Freigabe und Handelspause (Spec 011, ADR-0021)
Alles über die Weboberfläche, Seite **Einstellungen**, Bereich „Handel & Risikostopp“ – kein Befehl auf dem Server.

- **Risikostopp:** Fällt ein Depot 10 % unter seinen Höchststand, verkauft der Bot dessen Coins (`RISK_SELL`) und
  stoppt es. Die Übersicht zeigt je Depot den Abstand zum Hoch und „Risikostopp seit …“. Im Journal steht genau
  eine Zeile `WARNING risk stop <depot>: equity … below 90 % of its peak …`. Ein gestopptes Depot ist kein Fehler,
  der Dienst bleibt `running`.
- **Wieder freigeben:** Knopf neben dem gestoppten Depot. Der Dienst gibt es in der nächsten Runde frei (Journal:
  `risk stop <depot> released on request`); sein Bargeld wird der neue Höchststand. Die Anforderung gilt 24 h und
  nur für den Stopp, nach dem sie gestellt wurde.
- **Handel pausieren:** Schalter als Not-Aus. Ab der nächsten Runde (≤ 1 min) kauft und verkauft kein Depot mehr,
  GPT wird nicht gefragt; nur der Risikostopp verkauft weiter, bewertet wird weiter. Die Übersicht zeigt „Handel
  pausiert“. Im Zweifel gilt Pause: Ist `secrets.env` unlesbar oder `BOT_PAUSED` weder `1` noch `0`, pausiert der
  Dienst mit Warnung. Ein `Environment=BOT_PAUSED=…` im Drop-in gewinnt über den Schalter (die Seite sagt das).
- Vor dem ersten Deploy mit Spec 011 prüfen, ob ein Depot schon > 10 % unter seinem Hoch liegt – es würde in der
  ersten Runde verkauft und gestoppt:
```bash
ssh <alias> 'sudo runuser -u <dienst> -- python3 -c "import sqlite3; c=sqlite3.connect(\"file:/var/lib/llm-trading-bot/bot.db?mode=ro\", uri=True); [print(a, round((v/p-1)*100, 2)) for a, p, v in c.execute(\"SELECT p.agent, p.peak, (SELECT value FROM equity e WHERE e.agent=p.agent ORDER BY ts DESC LIMIT 1) FROM portfolios p\") if v]"'
```

### USDC-Depots (Spec 020, ADR-0030)
Standardmäßig an: Der Deploy allein startet sie. Gewählt werden sie in einem eigenen Feld `BOT_USDC_AGENTS` in
`secrets.env`, nie in `BOT_AGENTS` (dort steht weiter nur die bisherige Auswahl). Fehlt das Feld, laufen alle vier
(`trend-usdc`, `breakout-usdc`, `meanrev-usdc`, `ensemble-usdc`); ab dem ersten Speichern der Seite **Einstellungen**
steht dort die Auswahl der Kacheln mit Kennzeichen **USDC**, auch „keins“ (leeres Feld). Kein Befehl auf dem Server,
kein Neustart (der Dienst liest die Einstellungen jede Runde).

- In der ersten Runde des neuen Release legt der Bot die vier Depots und `buyhold-usdc` mit je 10.000 € an. Journal
  (beim Start): `service started: db bot.db, depots …, trend-usdc, breakout-usdc, meanrev-usdc, ensemble-usdc, …`
  und einmal
  `trade buyhold-usdc bought 2 market(s) (benchmark)`. Die Einstellungen zeigen die USDC-Kacheln angekreuzt.
- Mindestens ein Depot außer den USDC-Depots muss gewählt bleiben (sonst „BOT_AGENTS must name at least one depot
  besides the USDC depots“): Ein Release vor Spec 020 braucht nach einem Rollback ein nicht leeres `BOT_AGENTS`.
- Die Übersicht zeigt die Depots mit Kennzeichen „USDC“, ihre BTC-Trades mit Markt BTC-USDC und im Laufband
  BTC-USDC und USDC-EUR. Die übrigen Depots und ihre Historie ändern sich nicht.
- Fehlt BTC-USDC oder USDC-EUR in der Antwort von `/ticker/book`, steht im Journal z. B.
  `quotes: MarketDataError: BTC-USDC: missing`. Ohne frischen Kurs (≤ 20 s) beider Bücher entscheidet das BTC
  eines USDC-Depots nicht, und ein USDC-Depot mit BTC-Bestand bekommt keinen Equity-Punkt (fail closed); alle
  anderen Depots laufen weiter.
- Abschalten: Kacheln abwählen und speichern (gespeichert wird `BOT_USDC_AGENTS=` – „keins“, damit der Standard sie
  nicht wieder einschaltet). Die Depots handeln ab der nächsten Runde nicht mehr; ihre Historie bleibt in der DB.
  BTC-USDC und USDC-EUR verschwinden aus Lebenszeichen und Laufband, sobald ihr letzter Kurs älter als 1 h ist
  (der Bot fragt sie dann nicht mehr ab; kein Marktdatenfehler).
- Rollback auf ein Release vor Spec 020: startet mit derselben `secrets.env` (es liest `BOT_AGENTS` und ignoriert
  `BOT_USDC_AGENTS`); die USDC-Depots ruhen, ihre Historie bleibt. Speichert man in der alten Weboberfläche die
  Einstellungen, entfernt sie `BOT_USDC_AGENTS`; nach dem nächsten Deploy ab 020 laufen dann wieder alle vier
  (Standard) – bei Bedarf erneut abwählen.

### Variablen außerhalb von `secrets.env`
In `secrets.env` stehen nur Key, Depots, Effort, Budgets sowie Handelspause und Freigaben (Weboberfläche). Gebühren, Takt-Parameter oder
`LOG_LEVEL` setzt man per Drop-in; ein Update ändert Drop-ins nie (es prüft das sogar):
```bash
ssh -t <alias> 'sudo systemctl edit llm-trading-bot'
```
Darin z. B. `[Service]` und `Environment=LOG_LEVEL=DEBUG`; danach `sudo systemctl restart llm-trading-bot`.

### Backup und Wiederherstellung
Der Timer sichert täglich nach `daily/` (14 bleiben), jedes Update vorher nach `pre-update/` (5 bleiben). Jede
Kopie ist geprüft (`integrity_check`, Schema, Zeilenzahlen) und hat Rechte 600. `secrets.env` und `web-auth.json`
werden nicht gesichert. Die Backups liegen **nur auf diesem Server** (ADR-0018); Hetzner-Backups lassen sich in der
Hetzner-Oberfläche zuschalten – erst nach Anpassung der Datenschutzerklärung (Spec 014): Sie kopieren auch die
Journal-Dateien mit IP-Adressen.
```bash
ssh <alias> 'sudo systemctl start llm-trading-bot-backup && sudo ls -l /var/backups/llm-trading-bot/daily'
```
Probe (ändert nichts am Betrieb):
```bash
ssh <alias> 'sudo runuser -u <dienst> -- /opt/llm-trading-bot/current/.venv/bin/trading-bot restore --backup /var/backups/llm-trading-bot/daily/<datei> --db /var/lib/llm-trading-bot/restore-test/bot.db'
```
Echte Wiederherstellung – die alte DB wird beiseitegelegt, nie gelöscht, und `restore` schreibt nur in einen
Pfad, den es noch nicht gibt:
1. `sudo systemctl stop llm-trading-bot` (beim Stoppen schreibt SQLite die WAL-Datei in die DB zurück).
2. `bot.db` sowie ggf. `bot.db-wal`/`bot.db-shm` in `/var/lib/llm-trading-bot/` nach `replaced-<datum>/`
   verschieben (als `<dienst>`).
3. `trading-bot restore --backup <datei> --db /var/lib/llm-trading-bot/bot.db` als `<dienst>` (wie oben).
4. `sudo systemctl start llm-trading-bot`, dann `deploy.sh … status`.

`observations.db` bleibt dabei liegen (nicht beiseitelegen, nicht zurücksetzen): Sie ist nur anhängend und gehört
zu keinem bestimmten Stand von `bot.db`. Nach der Wiederherstellung vergibt `bot.db` Entscheidungs-Bars und
GPT-Aufruf-IDs (`llm_call_id`) ab dem Stand der Sicherung neu; dieselben Schlüssel können in den Beobachtungen also
ein zweites Mal vorkommen. Solche Zeilen werden nicht doppelt geschrieben, sondern gezählt –
`observations_last_error` zeigt dann `N decision/attempt rows ignored (duplicate keys) - bot.db restored?`, die
Aufzeichnung läuft weiter. Auswertungen (Labor) verbinden Beobachtungen und `bot.db` deshalb immer zusätzlich über
die Zeit (`ts`), nie über die ID allein.

**Beobachtungs-DB (Spec 015, ADR-0027):** eigenes Profil mit eigenem Schema und eigenen Namen
(`observations-<UTC>.db`), im selben Timer als zweiter, eigener Aufruf nach `bot.db`. Scheitert er (z. B.
beschädigte Datei), ist die Sicherung von `bot.db` schon fertig und die Unit trotzdem erfolgreich; der Fehler steht im
Journal (`backup failed: …`). Fehlt die Datei, steht dort `backup skipped: no database at …`. Prüfen und
wiederherstellen wie oben, immer mit `--profile observations` und nur in einen neuen Pfad, z. B.:
```bash
ssh <alias> 'sudo runuser -u <dienst> -- /opt/llm-trading-bot/current/.venv/bin/trading-bot restore --profile observations --backup /var/backups/llm-trading-bot/observations/daily/<datei> --db /var/lib/llm-trading-bot/restore-test/observations.db'
```
Echte Wiederherstellung wie bei `bot.db` (Dienst stoppen, `observations.db` mit `-wal`/`-shm` beiseitelegen,
`restore --profile observations … --db /var/lib/llm-trading-bot/observations.db`, Dienst starten). Ohne diesen Schritt
legt der Dienst eine fehlende Datei neu und leer an; eine beschädigte ersetzt er nie.

### Logs und Prüfbefehle
```bash
ssh <alias> 'systemctl status llm-trading-bot llm-trading-bot-web llm-trading-bot-backup.timer --no-pager'
```
```bash
ssh <alias> 'sudo journalctl --namespace=llm-trading-bot -u llm-trading-bot -n 50 --no-pager'
```
```bash
ssh <alias> 'sudo runuser -u <dienst> -- /opt/llm-trading-bot/current/.venv/bin/trading-bot verify-db --db /var/lib/llm-trading-bot/bot.db'
```

## Weboberfläche per SSH-Tunnel (Spec 006)
Die Weboberfläche bindet nur an `127.0.0.1` (ADR-0015) und läuft als Unit `llm-trading-bot-web`. Vom eigenen
Rechner einen Tunnel öffnen und `http://127.0.0.1:8787/` im Browser aufrufen:

```bash
ssh -N -L 8787:127.0.0.1:8787 <alias>
```

Der lokale Port ist frei wählbar (z. B. `-L 8899:127.0.0.1:8787`, wenn lokal schon die Arena auf 8787 läuft) – die
Seite prüft nur den Hostnamen `127.0.0.1`/`localhost`, nicht den Port.
Beim ersten Start steht der einmalige Einrichtungscode im Terminal bzw. Journal des Web-Prozesses.

**Passwort vergessen:** Web-Prozess stoppen, `web-auth.json` im Datenverzeichnis beiseitelegen, Web-Prozess neu
starten – erst beim Start entsteht ein neuer Einrichtungscode (im Terminal bzw. Journal). Die Datei bei laufendem
Prozess zu entfernen reicht nicht: Ohne Neustart gibt es keinen Code, jeder Versuch zählt als Fehlversuch.
`secrets.env` bleibt dabei unberührt. Die Seite prüft beim Absenden von Formularen, dass `Origin` denselben Port
nennt wie der Host, den der Browser aufruft – das passt bei jedem Tunnel-Port.

## Weboberfläche öffentlich per HTTPS (Spec 013, ADR-0024)
Von außen nur über Caddy (offizielles, signiertes apt-Repo, automatische Updates): `https://<domain>/` mit
Let's-Encrypt-Zertifikat, `www.<domain>` leitet dauerhaft auf `<domain>` weiter, `http://` auf `https://`. Die App
bindet weiter nur an `127.0.0.1` – Port 8787 für den Tunnel (bleibt), Port 8788 nur für Caddy. `<domain>` steht nur
auf dem Server in `/etc/llm-trading-bot/public-host` (eine Zeile, Kleinbuchstaben), nie im Repo.

| Was | Wo | Wer |
|---|---|---|
| Domain | `/etc/llm-trading-bot/public-host` | root, 644 |
| Caddy-Konfiguration | `/etc/caddy/Caddyfile` (aus `deploy/Caddyfile` des aktiven Release) | root, 644 |
| Admin-API von Caddy | Unix-Socket `/run/caddy/admin.sock` (Drop-in `caddy.service.d/10-llm-trading-bot.conf`), kein Port 2019 | caddy |
| Paketquelle | `/etc/apt/sources.list.d/caddy-stable.list`, Schlüssel `/usr/share/keyrings/caddy-stable-archive-keyring.gpg` | root |
| Automatische Updates | `/etc/apt/apt.conf.d/53-llm-trading-bot-caddy` (Origin `cloudsmith/caddy/stable`) | root |
| Firewall | ufw `allow 80/tcp` und `allow 443/tcp` (ohne Rate-Limit), SSH unverändert | – |

### Voraussetzungen (manuell, vorher prüfen)
1. **DNS:** A-Eintrag von `<domain>` und `www.<domain>` zeigt auf `<server>`. Gibt es AAAA-Einträge, müssen sie auf
   die IPv6-Adresse des Servers zeigen – sonst scheitert das Zertifikat über IPv6. Prüfen:
   ```bash
   nslookup -type=A <domain>; nslookup -type=AAAA <domain>; nslookup -type=A www.<domain>; nslookup -type=AAAA www.<domain>
   ```
2. **Hetzner-Cloud-Firewall** (falls eingerichtet): eingehend 80/tcp und 443/tcp erlauben. Let's Encrypt erlaubt
   nur wenige Fehlversuche je Stunde – deshalb erst Firewall und DNS, dann `public`.
3. **Release 013 aktiv:** `public` bricht ab, wenn der aktive Release kein `deploy/Caddyfile` kennt. Erst deployen:
   ```bash
   bash scripts/deploy.sh <alias> <dienst> deploy
   ```
4. **Passwort ≥ 12 Zeichen:** Der öffentliche Login nimmt nur Passwörter mit mindestens 12 Zeichen, die nicht in der
   Liste häufiger Passwörter stehen. Ein kürzeres vorher über den Tunnel ändern (Einstellungen → „Passwort ändern“).

### Einschalten
```bash
bash scripts/deploy.sh <alias> <dienst> public <domain>
```
Eine SSH-Verbindung, mit Deploy-Sperre, in dieser Reihenfolge: Caddy-Repo mit Schlüssel und Paket
(`apt-get -o DPkg::Lock::Timeout=300`), Origin in die automatischen Updates, Domain-Datei, Units neu erzeugen
(Web-Unit mit `--public-host <domain> --public-port 8788`), `ufw allow 80/tcp` und `443/tcp`, Caddyfile installieren,
Caddy und Web-Unit neu starten. Das Caddyfile prüft `caddy validate` gleich nach der Paketinstallation, bevor
Domain, Units oder Firewall geändert werden. Ein erneuter Aufruf meldet `changes: 0` und startet nichts neu.

Danach rendert jeder `deploy` das Caddyfile des neuen Release mit der Domain, prüft es vor dem Stoppen
(`caddy validate`; ungültig → nichts geändert) und lädt Caddy erst neu, wenn der neue Release gesund ist. Scheitert
das Neuladen, bleibt das alte Caddyfile, der Deploy endet mit Fehler, der Bot wird **nicht** zurückgerollt. Ein
Rollback (automatisch oder `rollback`) ändert Caddy nie; ein Release vor Spec 013 bleibt ein gültiger Rückfall
(dann antwortet nur der Tunnel, die öffentliche Adresse liefert einen Fehler, bis wieder ein 013-Release läuft).

### Prüfen (von außen)
```bash
curl -s -D - -o /dev/null https://<domain>/login
```
Erwartet: `HTTP/2 200`, `strict-transport-security: max-age=31536000`, kein `server`-Header.
```bash
curl -s -D - -o /dev/null http://<domain>/login
```
```bash
curl -s -D - -o /dev/null https://www.<domain>/login
```
Erwartet: `308`/`301` auf `https://<domain>/login`. Seit Spec 019 (ADR-0028) ist die Übersicht ohne Login
lesbar, alles andere nicht:
```bash
curl -s -D - -o /dev/null https://<domain>/
```
```bash
curl -s -D - -o /dev/null https://<domain>/settings
```
Erwartet: `/` mit `200`, `x-robots-tag: noindex, nofollow` und **ohne** `set-cookie`; `/settings` mit `303` auf
`/login`. Abschalten ohne Deploy: Einstellungen → „Übersicht ohne Login“ → „Nur mit Login“.
Auf dem Server – Bot-Ports nur auf `127.0.0.1`, kein Port 2019:
```bash
ssh <alias> 'sudo ss -ltnp | grep -E ":(80|443|2019|8787|8788) "'
```
```bash
bash scripts/deploy.sh <alias> <dienst> status
```
`status` nennt zusätzlich `public host: <domain>` (oder `off`) und `caddy: <version>, active`. Logs von Caddy (es
gibt bewusst kein Zugriffslog, nur Start-, Zertifikats- und Fehlermeldungen). Seit Spec 014 schreibt Caddy in den
Journal-Namensraum des Bots:
```bash
ssh <alias> 'sudo journalctl --namespace=llm-trading-bot -u caddy -n 50 --no-pager'
```
Was systemd über die Unit meldet (Start, Stopp, Absturz), steht im System-Journal: `sudo journalctl -u caddy -n 20`.

### Abschalten
```bash
bash scripts/deploy.sh <alias> <dienst> public off
```
Entfernt die Domain-Datei, stoppt und deaktiviert Caddy, löscht die ufw-Regeln für 80/443, erzeugt die Units ohne
öffentlichen Listener neu und startet die Web-Unit neu. Das Caddy-Paket bleibt installiert. Der Tunnel geht weiter.
Die Protokoll-Grenzen aus Spec 014 (Drop-ins, `ufw logging off`) bleiben stehen.

### Passwort zurücksetzen
Nur per Tunnel, wie oben unter „Passwort vergessen“: Öffentlich gibt es keine Einrichtung – ohne Passwort zeigt
`https://<domain>/` nur „Einrichtung nur per SSH-Tunnel“ (503). Der Einrichtungscode steht im Journal.

## Impressum und Datenschutz (Spec 014, ADR-0025)
Jede Seite verlinkt `/impressum` und `/datenschutz`, beide ohne Login (öffentlich und per Tunnel). Die
Datenschutzerklärung kommt aus dem Repo; das **Impressum** (Name, Anschrift, Kontakt) liegt nur auf dem Server und
lokal außerhalb des Repos – nie im Repo, nie in einem Commit.

| Was | Wo | Wer |
|---|---|---|
| Impressum | `/etc/llm-trading-bot/impressum.md`; die Web-Unit übergibt den Pfad fest mit `--impressum` | root, 644 |
| Vorlage | `deploy/impressum.example.md` (nur Platzhalter) | Repo |
| Caddy-Log | Drop-in `caddy.service.d/10-llm-trading-bot.conf` mit `LogNamespace=llm-trading-bot` | root, 644 |
| Namensraum-Journal | `/etc/systemd/journald@llm-trading-bot.conf` (aus jedem Release): 200 MB, `MaxRetentionSec=14day`, `MaxFileSec=1day`, `ForwardToSyslog=no`; dieselben Grenzen zusätzlich als Drop-in `journald@llm-trading-bot.conf.d/50-llm-trading-bot.conf` von `public`, damit ein Rollback auf einen Release vor 014 sie nicht lockert | root, 644 |
| System-Journal | Drop-in `/etc/systemd/journald.conf.d/50-llm-trading-bot.conf`: `MaxRetentionSec=14day`, `MaxFileSec=1day` | root, 644 |
| Firewall-Log | `ufw logging off` | – |

### Impressum hochladen und ändern
1. Vorlage kopieren – **außerhalb** des Repo-Ordners, z. B. nach `~/llm-trading-bot/impressum.md` – und die
   Platzhalter (`__OPERATOR_NAME__`, `__CO_ADDRESS__`, `__CO_STREET__`, `__CO_POSTCODE_CITY__`, `kontakt@<domain>`)
   ausfüllen. Auszeichnung: `# `/`## ` Überschriften, `- ` Listen, Leerzeile trennt Absätze, E-Mail-Adressen werden
   zu Links.
2. Hochladen (mit Zustimmung):
   ```bash
   bash scripts/deploy.sh <alias> <dienst> impressum <datei>
   ```
   Lokal geprüft: UTF-8, nicht leer, kein NUL-Byte, höchstens 16384 Byte, keine Platzhalter mehr; BOM und CRLF
   werden vorher entfernt bzw. zu LF. Verbindung 1 lädt die Datei nach `incoming/impressum-<sha256>.md`,
   Verbindung 2 führt `release.sh impressum <sha256>` des neuesten grünen Commits unter der Deploy-Sperre aus:
   Hash prüfen, erneut prüfen, `/etc/llm-trading-bot` anlegen (755), Datei per Umbenennen austauschen, eingehende
   Kopie löschen. Gleicher Inhalt → `changes: 0`. Der Inhalt steht nie in der Ausgabe oder in `deploy.log`.
3. Wirkt ab dem nächsten Abruf, kein Neustart. Ändern = Datei lokal ändern, Schritt 2 wiederholen.

`status` meldet `impressum: ok (<Bytes> B, sha256 <12 Zeichen>)`, `missing` oder `INVALID (<Grund>)` und mit
`web unit:`, ob die aktive Web-Unit `--impressum` übergibt. Fehlt die Datei oder ist sie ungültig, antwortet
`/impressum` mit 503 „Impressum nicht hinterlegt“, alles andere läuft weiter. Ein Rückfall auf ein Release vor
Spec 014 nimmt die Rechtstexte offline (die Datei bleibt liegen) und setzt das Namensraum-Journal auf dessen alte
Datei zurück – dann zügig wieder vorwärts deployen oder `public off`.

### Protokolle und Fristen
Besucherdaten (IP-Adresse, aufgerufene Adresse, Browserkennung) stehen nur in Warnungen von Caddy; es gibt kein
Zugriffslog. `public <domain>` richtet die Grenzen ein (idempotent, beim zweiten Lauf `changes: 0`):
- Caddy loggt in den Namensraum `llm-trading-bot` (eigene Meldungen:
  `journalctl --namespace=llm-trading-bot -u caddy`), nicht mehr ins System-Journal und nicht nach `/var/log/syslog`.
- Namensraum und System-Journal löschen ganze Dateien nach 14 Tagen, jede Datei umfasst höchstens einen Tag:
  Einträge sind höchstens 15 Tage alt. Das gilt auch für SSH- und Kernel-Meldungen im System-Journal.
- systemd-journald startet nur neu, wenn sich sein Drop-in geändert hat; Caddy nur, wenn sich etwas geändert hat.
- `ufw logging off`: Die Firewall schreibt keine abgewiesenen Verbindungen mehr (`kern.log`, `ufw.log`, `syslog`).

Prüfen:
```bash
ssh <alias> 'systemctl show caddy -p LogNamespace; sudo ufw status verbose | grep Logging'
```
```bash
ssh <alias> 'sudo journalctl --namespace=llm-trading-bot --disk-usage; sudo journalctl --disk-usage'
```

### Einmalig aufräumen (nur mit Zustimmung)
Vor Spec 014 standen Caddy-Warnungen und Firewall-Meldungen ohne passende Frist im System-Journal und in
`/var/log`. Einmalig nach `public <domain>` mit 014 – **nur mit ausdrücklicher Zustimmung**, gelöscht ist gelöscht:
```bash
ssh <alias> 'sudo journalctl --rotate && sudo journalctl --vacuum-time=1s'
```
Leert das System-Journal (auch alte SSH- und Systemmeldungen), der Namensraum des Bots bleibt.
```bash
ssh <alias> 'ls /var/log/ufw.log* /var/log/kern.log* /var/log/syslog*'
```
```bash
ssh <alias> 'sudo truncate -s 0 /var/log/ufw.log* /var/log/kern.log* /var/log/syslog*'
```
Erst ansehen, dann leeren: Firewall-, Kernel- und Syslog-Dateien samt Rotationen (`/var/log` ist lesbar, die
Muster löst schon die Shell des Admins auf). Die Dateien bleiben bestehen, logrotate rotiert weiter.

### Reihenfolge beim ersten Einrichten (Spec 014 AK12, alles mit Zustimmung)
1. Nutzer: Postfach `kontakt@<domain>` bei Migadu eingerichtet und erreichbar, AVV mit Hetzner und Migadu
   geprüft, Impressum und Datenschutzerklärung gelesen und freigegeben.
2. `bash scripts/deploy.sh <alias> <dienst> impressum <datei>`, danach `status`.
3. Deploy des Release mit Spec 014 (`deploy`).
4. `bash scripts/deploy.sh <alias> <dienst> public <domain>` erneut (Caddy-Drop-in, Journal-Grenzen, Logging aus).
5. Einmalig aufräumen (oben), nur mit Zustimmung.
6. Prüfen:
   - Von außen `/impressum` und `/datenschutz` mit 200 ohne Login, Links im Fuß der Login-Seite:
     `curl -s -o /dev/null -w '%{http_code}\n' https://<domain>/impressum`
   - `systemctl show caddy -p LogNamespace` zeigt `LogNamespace=llm-trading-bot`.
   - Eine neue Caddy-Meldung steht nur im Namensraum, ein Cookie darin geschwärzt.
   - `ufw status verbose` zeigt `Logging: off`.
