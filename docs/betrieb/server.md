# Server: Einrichtung und Prüfung

Zielsystem laut ADR-0005: Hetzner Cloud CX23, Ubuntu 24.04 LTS. Zugangsmodell laut ADR-0006.
**Das Repo ist öffentlich:** IP, Hostname, Benutzernamen und SSH-Alias stehen nur lokal (`~/.ssh/config`),
hier nur Platzhalter: `<server>` (IP/Host), `<admin>` (Admin-Benutzer), `<dienst>` (Systembenutzer des Bots),
`<alias>` (lokaler SSH-Alias), `<key>` (Name des lokalen Keys).

## Was eingerichtet ist (Spec 002)
| Bereich | Zustand |
|---|---|
| SSH | nur Public Key; kein Root-Login, kein Passwort-Login (`/etc/ssh/sshd_config.d/01-hardening.conf`) |
| Benutzer | `<admin>` mit Key und `sudo` ohne Passwort; `<dienst>` als Systembenutzer ohne Shell und ohne sudo |
| Firewall | ufw: eingehend nur 22/tcp mit Rate-Limit (max. 6 Verbindungen in 30 s je Quell-IP), ausgehend frei |
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
- Hetzner-Backups oder ein Snapshot nach der Einrichtung.

## Bot-Betrieb (Spec 008/009, ADR-0017/0018)
Alles läuft von hier über `scripts/deploy.sh` (Git Bash) und nur mit Zustimmung des Nutzers. Das Skript schickt
nur einen Commit von `origin/main` mit grüner CI auf den Server (`git archive`) und führt dort `deploy/release.sh`
aus demselben Commit aus. `<dienst>` ist der Systembenutzer aus Spec 002.

| Pfad auf dem Server | Inhalt | Rechte |
|---|---|---|
| `/opt/llm-trading-bot/releases/<sha>/` | ein Release je Commit mit eigener `.venv` | root, 755 |
| `/opt/llm-trading-bot/current`, `…/previous` | Symlinks auf das aktive und das vorige Release | root |
| `/var/lib/llm-trading-bot/` | `bot.db`, `secrets.env`, `web-auth.json`, `service-status.json` | `<dienst>`, 700 / 600 |
| `/var/backups/llm-trading-bot/{daily,pre-update}/` | geprüfte DB-Kopien (14 bzw. 5) | `<dienst>`, 700 / 600 |
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
```bash
bash scripts/deploy.sh <alias> <dienst> rollback
```
```bash
bash scripts/deploy.sh <alias> <dienst> status
```

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
Hetzner-Oberfläche jederzeit zuschalten.
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
