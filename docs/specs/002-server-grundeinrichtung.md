# Spec 002: Server-Grundeinrichtung (Härtung)

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s002-server-setup` · **Meilenstein:** M1 (Teil von Nr. 6)
- **Erstellt:** 2026-11-05

## Ziel
Der Hetzner-CX23 ist gegen die üblichen Angriffe gehärtet und für das spätere Deployment vorbereitet.
Anmeldung nur noch per SSH-Key über einen Admin-Benutzer, kein Root- oder Passwort-Login, Firewall aktiv,
Sicherheitsupdates laufen automatisch. Die Einrichtung ist als Skript im Repo reproduzierbar, ohne dass
IP, Hostname oder Benutzernamen im öffentlichen Repo stehen.

Ausgangslage (2026-11-05, lesend geprüft): Ubuntu 24.04.4 LTS, x86_64, 2 vCPU, 3,7 GB RAM, 38 GB.
UTC, NTP synchron, Python 3.12.3. Nur `root` mit Key; `PasswordAuthentication yes`, ufw inaktiv,
unattended-upgrades installiert, offene Ports nur 22.

## Akzeptanzkriterien
- [ ] AK1: `deploy/setup-server.sh` ist idempotent (zweiter Lauf ändert nichts und endet mit 0). Benutzername und
  Public Key kommen als Parameter. Im Skript stehen keine IP, kein Hostname und kein Benutzername.
  — Nachweis: CI-Job `bash -n` + `shellcheck deploy/*.sh`; [manuell] zweiter Lauf auf dem Server ohne Änderungen
- [ ] AK2: Ein Admin-Benutzer meldet sich per Key an und hat sudo. Ein Systembenutzer für den späteren Dienst
  existiert ohne Login-Shell und ohne sudo.
  — Nachweis: [manuell] `ssh <admin>@<server> sudo -n true` → Exit 0; `getent passwd <dienst>` zeigt `/usr/sbin/nologin`
- [ ] AK3: Kein Root-Login und kein Passwort-Login mehr.
  — Nachweis: [manuell] `sshd -T` zeigt `permitrootlogin no`, `passwordauthentication no`,
  `kbdinteractiveauthentication no`; `ssh root@<server>` wird abgewiesen
- [ ] AK4: Firewall aktiv: eingehend alles verboten außer SSH (Port 22, mit Rate-Limit), ausgehend erlaubt.
  — Nachweis: [manuell] `ufw status verbose`
- [ ] AK5: Sicherheitsupdates werden automatisch installiert, Neustart bei Bedarf um 04:00 UTC.
  — Nachweis: [manuell] `/etc/apt/apt.conf.d/52-llm-trading-bot` vorhanden, `unattended-upgrade --dry-run` ohne Fehler
- [ ] AK6: Uhr bleibt UTC und NTP-synchron (Voraussetzung für die Zeitprüfungen aus Spec 001).
  — Nachweis: [manuell] `timedatectl show -p Timezone -p NTPSynchronized` → `Etc/UTC`, `yes`
- [ ] AK7: Lokal gibt es einen SSH-Alias für den Server, nur in `~/.ssh/config`, nicht im Repo.
  — Nachweis: [manuell] `ssh <alias> true` ohne weitere Angaben
- [ ] AK8: `docs/betrieb/server.md` beschreibt Einrichtung, Notzugang (Hetzner-Konsole) und Prüfbefehle,
  nur mit Platzhaltern (`<server>`, `<admin>`). — Nachweis: `bash scripts/docs-check.sh`; Durchsicht im PR

## Abgrenzung
- Nicht Teil dieser Spec: Installation des Bots, systemd-Unit, Verzeichnisse für Code und Daten, `.env`,
  Backups, Update/Rollback (kommen mit dem Deployment in M1 Nr. 6, wenn es einen Dienst gibt).
- Kein fail2ban (bei reinem Key-Login reicht das ufw-Rate-Limit), kein anderer SSH-Port, kein VPN.
- Hetzner-Cloud-Firewall und Hetzner-Backups/Snapshots: nur als optionale manuelle Empfehlung in der Doku.

## Relevante Entscheidungen & Learnings
- ADR-0005: CX23, Ubuntu, systemd; Zugang nur per SSH-Key, Firewall nur SSH, Status nur per Tunnel.
- ADR-0006: Admin-Benutzer mit `sudo NOPASSWD` und Key; eigener Systembenutzer für den Dienst.
- CLAUDE.md: Repo ist öffentlich → IP, Hostname und Benutzernamen nur als Platzhalter; Server-Befehle nur mit Zustimmung.
- LEARNINGS #git #windows (CRLF): `*.sh` muss LF haben – `.gitattributes` regelt das bereits.

## Ansatz
Ein Bash-Skript `deploy/setup-server.sh`, das als root auf dem Server läuft, in zwei Phasen:
1. **Vorbereiten** (Standard): Pakete aktualisieren, Admin-Benutzer mit Key und sudoers-Datei anlegen,
   Systembenutzer für den Dienst anlegen, ufw konfigurieren und aktivieren (SSH vorher erlauben),
   unattended-upgrades konfigurieren. Root- und Passwort-Login bleiben noch offen.
2. **Absperren** (`--lock`): sshd-Drop-in `/etc/ssh/sshd_config.d/01-hardening.conf`, `sshd -t`, dann neu laden.
   Läuft nur, wenn es den Admin-Benutzer mit `authorized_keys` gibt.

Zwischen den Phasen wird in einer **zweiten** SSH-Sitzung geprüft, dass Admin-Login und sudo funktionieren.
Erst danach wird abgesperrt, damit man sich nicht aussperrt. Ausgeführt wird per
`ssh root@<server> 'bash -s' -- … < deploy/setup-server.sh`; auf dem Server bleibt keine Kopie liegen.
Verworfen: Ansible (für einen Server zu schwer), cloud-init (der Server existiert schon), Befehle nur von Hand
(nicht reproduzierbar).

## Plan
- [x] 1. `deploy/setup-server.sh` (beide Phasen, idempotent) + CI-Job `shellcheck`/`bash -n` (AK1)
- [x] 2. Phase 1 auf dem Server ausführen, mit Zustimmung (AK2, AK4, AK5, AK6)
- [x] 3. In zweiter Sitzung Admin-Login + `sudo -n true` prüfen, lokalen SSH-Alias anlegen (AK2, AK7)
- [x] 4. Phase 2 (`--lock`) ausführen, mit Zustimmung; Root- und Passwort-Login gegenprüfen (AK3)
- [x] 5. Zweiter Lauf zur Prüfung der Idempotenz; `docs/betrieb/server.md`, ARCHITEKTUR.md „Deployment" (AK1, AK8)

## Risiken & offene Fragen
- Aussperren: Wird abgesperrt, bevor der Admin-Login klappt, hilft nur noch die Hetzner-Konsole → Zwei-Phasen-Ablauf, Gegenprobe in zweiter Sitzung.
- Ubuntu-Cloud-Images setzen in `/etc/ssh/sshd_config.d/50-cloud-init.conf` `PasswordAuthentication yes`.
  sshd übernimmt den **ersten** Wert, deshalb heißt der Drop-in `01-…`. AK3 prüft mit `sshd -T`, nicht nur die Datei.
- Automatischer Neustart um 04:00 UTC: Solange es keinen Dienst gibt, ist das egal. Später muss der Bot einen
  Neustart vertragen (M1-Kriterium), dann prüft ihn das sogar regelmäßig.
- Der Login-Key wird schon für einen anderen Server genutzt. Ein eigener Key bringt wenig,
  solange beide auf demselben PC liegen; tauschbar über einen neuen Lauf mit anderem Public Key.

---

## Verlauf
- 2026-11-05: Host-Key des Servers wich vom alten `known_hosts`-Eintrag ab (IP vorher anders vergeben). Der Nutzer hat bestätigt, dass der Server neu ist; der Eintrag wurde ersetzt.
- 2026-11-05: Die Beispielaufrufe im Skriptkopf waren falsch: `ssh` fügt die entfernten Argumente zu einer Zeile zusammen, dadurch zerfiel der Public Key. Jetzt mit inneren einfachen Anführungszeichen.
- 2026-11-05: Phase 1 ausgeführt (7 Änderungen; `apt upgrade` hat doch Updates gefunden → Neustart nötig). Gegenprobe in neuer Sitzung: Admin-Login, `sudo -n true`, Dienstbenutzer mit `nologin`, ufw `LIMIT 22/tcp` (v4+v6), UTC/NTP, `unattended-upgrade --dry-run` – alles OK. SSH-Alias lokal angelegt und getestet.
- 2026-11-05: Phase 2 über den Admin-Benutzer mit sudo ausgeführt (1 Änderung). `sshd -T`: `permitrootlogin no`, `passwordauthentication no`, `kbdinteractiveauthentication no`, `pubkeyauthentication yes`. `ssh root@<server>` → „Permission denied (publickey)"; mit Passwort angefordert bietet der Server nur `publickey` an.
- 2026-11-05: Zusätzlich (nicht im Plan) neu gestartet, weil die Updates es verlangten. Danach: Kernel 6.8.0-138, kein weiterer Neustart nötig, ufw aktiv, sshd-Härtung wirksam. Zweiter Lauf beider Phasen: `changes: 0` (AK1).

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
