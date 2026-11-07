# ADR-0005: Betrieb auf Hetzner Cloud CX23 mit Ubuntu und systemd; Status nur per SSH-Tunnel

- **Status:** abgelöst durch [ADR-0024](0024-weboberflaeche-oeffentlich-https.md)
- **Löst ab:** –
- **Datum:** 2026-11-05
- **Bereich:** #betrieb #hetzner #sicherheit

## Kontext
Die Arena zielte auf einen Raspberry Pi 5 zu Hause. Der Bot soll 24/7 und unabhängig vom Heimnetz und
Stromausfällen laufen. Vorgabe des Nutzers: Hetzner Cloud Server CX23 (x86-64) mit Ubuntu.
Ein öffentlich erreichbarer Server ist dauerhaft Angriffen ausgesetzt, und auf ihm liegen API-Keys.

## Entscheidung
- Zielsystem: **Hetzner Cloud CX23**, Ubuntu LTS (geplant 24.04), Dienst unter **systemd** (Neustart bei
  Absturz, sauberer Shutdown). Bot läuft unter eigenem, unprivilegiertem Linux-Benutzer.
- Server-Zugang nur per SSH-Key (kein Passwort-Login, kein Root-Login); Firewall lässt eingehend nur SSH zu.
- Status-/Dashboard-HTTP bindet ausschließlich an `127.0.0.1`; Zugriff vom Desktop per
  `ssh -L <port>:127.0.0.1:<port> <user>@<server>`.
- Secrets nur in `.env` auf dem Server (Rechte 600), nie im Repo, in Logs oder in Diffs.
- Daten (SQLite) und Backups außerhalb des Code-Verzeichnisses; nie zwei Prozesse auf derselben DB.

## Verworfene Alternativen
- **Raspberry Pi zu Hause (wie Arena)** – abhängig von Heimnetz und Strom.
- **Hetzner Storage Box allein** – kann keinen Dienst ausführen; bleibt Kandidat als externes Backup-Ziel.
- **ARM-Server CAX** – Nutzer hat sich für CX23 entschieden.
- **Dashboard öffentlich mit Login** – zusätzliche Angriffsfläche ohne Bedarf.
- **Docker** – für einen einzelnen Python-Dienst unnötige Schicht; systemd reicht.

## Konsequenzen
- Positiv: stabile 24/7-Umgebung, reproduzierbar per Anleitung.
- Negativ: laufende Serverkosten; Härtung und Updates des Servers sind eigene Aufgabe.
- Neu bewerten, wenn: Ressourcen des CX23 nicht reichen oder ein externer Zugriff (z. B. VPN) nötig wird.

## Code-Anker
- – (systemd-Unit und Deploy-Skripte folgen in M1 Nr. 6)
