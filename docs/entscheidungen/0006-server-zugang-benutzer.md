# ADR-0006: Admin-Benutzer mit Key und sudo NOPASSWD; Dienst unter eigenem Systembenutzer

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-05
- **Bereich:** #betrieb #hetzner #sicherheit

## Kontext
Der Server hat anfangs nur `root` mit Key, Passwort-Login ist an. Einrichtung, Deployment und Updates sollen
nicht interaktiv laufen können, auch aus Claude Code heraus und nur mit Zustimmung des Nutzers.
Auf dem Server liegen später API-Keys und die Handelshistorie.

## Entscheidung
- Ein **Admin-Benutzer** (Name nur lokal, nicht im Repo) meldet sich per SSH-Key an und hat `sudo` **ohne Passwort**
  (`/etc/sudoers.d/`-Datei, Rechte 440, mit `visudo -c` geprüft). Er hat kein Passwort.
- Root-Login und Passwort-Login sind abgeschaltet (Spec 002).
- Der Bot läuft unter einem eigenen **Systembenutzer** ohne Login-Shell und ohne sudo. Nur dieser Benutzer
  darf Daten und `.env` lesen.

## Verworfene Alternativen
- **Weiter als root arbeiten** – jeder Fehler und jeder kompromittierte Prozess hat volle Rechte.
- **sudo mit Passwort** – schließt nicht interaktives Deployment aus; ein Passwort, das man irgendwo ablegt, ist
  schwächer als der Key allein.
- **Bot läuft unter dem Admin-Benutzer** – eine Lücke im Bot gäbe sudo-Rechte.

## Konsequenzen
- Positiv: Deployment ist automatisierbar; der Dienst ist von der Administration getrennt.
- Negativ: Wer den privaten Key hat, ist praktisch root. Der Key muss daher auf dem PC geschützt bleiben.
- Neu bewerten, wenn: weitere Personen Zugang brauchen oder der Key auf weiteren Geräten liegen soll.

## Code-Anker
- `deploy/setup-server.sh` (Spec 002)
