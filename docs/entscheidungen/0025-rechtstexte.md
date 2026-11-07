# ADR-0025: Impressum nur auf dem Server, Datenschutzerklärung im Repo an den Code gebunden

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-07
- **Bereich:** #web #betrieb #datenschutz

## Kontext
Die Weboberfläche ist seit Spec 013 öffentlich (ADR-0024). Der Nutzer will Impressum und Datenschutzerklärung wie
auf seinen anderen Seiten (2026-11-07). Das Repo ist öffentlich: Domain, Server-Details und Personendaten gehören
nicht hinein (CLAUDE.md). Eine lesende Prüfung des Servers zeigte, dass Caddy Warnungen mit IP-Adresse und
Browserkennung ins System-Journal schreibt (von dort auch nach `/var/log/syslog`) und die Firewall abgewiesene
Verbindungen mit Quell-IP protokolliert – beides ohne passende Altersgrenze.

## Entscheidung
- **Impressum** (Name, Anschrift, Kontakt) liegt nur auf dem Server in `/etc/llm-trading-bot/impressum.md`, wird mit
  `deploy.sh impressum <datei>` hochgeladen und bei jedem Abruf gelesen. Im Repo nur eine Vorlage mit Platzhaltern.
- **Datenschutzerklärung** liegt im Repo als Python-Modul, ohne Personendaten und Domain; Tests prüfen, dass
  Cookies (aus echten Antworten), Fristen und Grenzen darin mit Code und Server-Dateien übereinstimmen. Eine Änderung
  an Cookies oder Fristen ohne Textänderung bricht die Tests.
- **Beide Seiten ohne Login** auf beiden Wegen, auch vor der Einrichtung; sonst gelten Host-Prüfung, Header und CSP
  wie überall (ADR-0024).
- **Fristen werden durchgesetzt, nicht nur beschrieben:** Caddy loggt in den Journal-Namensraum des Bots (ohne
  Syslog-Weiterleitung), Namensraum und System-Journal behalten höchstens 15 Tage (`MaxRetentionSec=14day`,
  `MaxFileSec=1day`), die Firewall protokolliert nicht (`ufw logging off`), der Login-Schutz vergisst Adressen
  24 h nach dem letzten Fehlversuch bzw. dem Ende der Sperre. Weiter kein Zugriffslog. Eingerichtet von
  `release.sh public`.

## Verworfene Alternativen
- **Impressum im Repo:** Personendaten und Domain dauerhaft in der öffentlichen Git-Historie.
- **Datenschutzerklärung nur auf dem Server:** kein Test bemerkt, wenn Text und Code auseinanderlaufen.
- **Caddy-Logs abschalten:** Zertifikats- und Verbindungsprobleme wären nicht mehr zu sehen.
- **Längere Fristen nur benennen** (Syslog-Rotation, unbegrenztes System-Journal): mehr IP-Adressen als nötig.
- **Eigener Journal-Namensraum für Caddy:** mehr Konfiguration; Warnungen sind selten.

## Konsequenzen
- Positiv: keine Personendaten im Repo; die Datenschutzerklärung bleibt nachweislich aktuell; IP-Adressen in Logs
  höchstens 15 Tage, im Speicher höchstens 24 h nach dem letzten Fehlversuch.
- Negativ / Preis: Das Impressum ist ein zusätzlicher Server-Zustand (in `status` sichtbar); Cookie- oder
  Friständerungen brauchen eine Textänderung; alle System-Logs (auch SSH) nur noch 15 Tage; keine Firewall-Logs;
  Caddy teilt sich die 200 MB des Bot-Namensraums.
- Neu bewerten, wenn: weitere Personen die Seite nutzen, Echtgeld kommt (M2) oder neue Dienste Besucherdaten sehen.

## Code-Anker
- `src/llm_trading_bot/web/legal.py`, `web/datenschutz.py`, `web/auth.py`, `deploy/release.sh`, `scripts/deploy.sh` – geplant, Spec 014
