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

Beide Phasen sind idempotent: Ein erneuter Lauf meldet `unchanged` und `changes: 0`.

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
- **cloud-init:** Ubuntu-Images setzen `PasswordAuthentication yes` in `50-cloud-init.conf`. sshd übernimmt den
  ersten Wert, deshalb heißt der eigene Drop-in `01-…`. Immer mit `sshd -T` prüfen, nicht mit der Datei.
- **Argumente über ssh:** `ssh` fügt die entfernten Argumente zu einer Kommandozeile zusammen. Werte mit
  Leerzeichen (Public Key) brauchen innere einfache Anführungszeichen, wie oben.

## Optional (manuell in der Hetzner-Oberfläche)
- Hetzner-Cloud-Firewall mit denselben Regeln als zweite Schicht vor dem Server.
- Hetzner-Backups oder ein Snapshot nach der Einrichtung.
