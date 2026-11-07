# Spec 014: Impressum und Datenschutzerklärung

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s014-rechtstexte` · **Meilenstein:** M1 (Nachtrag zu Nr. 7)
- **Erstellt:** 2026-11-07

## Ziel
Jede Seite der Weboberfläche verlinkt ein Impressum und eine Datenschutzerklärung, die ohne Login erreichbar sind –
öffentlich unter `https://<domain>/impressum` und `/datenschutz`, ebenso per Tunnel. Die Datenschutzerklärung
beschreibt, was der Server tatsächlich verarbeitet, und der Server hält die genannten Fristen ein; Tests binden
Text und Code aneinander. Name und Anschrift des Betreibers stehen nur auf dem Server, nie im öffentlichen Repo.

## Akzeptanzkriterien
- [ ] AK1: `GET /impressum` und `GET /datenschutz` antworten auf beiden Wegen (Tunnel 8787, öffentlich 8788) ohne
  Session und ohne Set-Cookie, auch bevor ein Passwort eingerichtet ist (öffentlich sonst 503). Die Routen liegen in
  `WebApp.handle` vor der Einrichtungs-Weiche; Host-Prüfung, Proxy-Header-Sperre und Origin-Prüfung in
  `make_handler` gelten unverändert. `POST` auf beide → 405 mit `Allow: GET`; andere Methoden bleiben 501 wie heute
  (kein HEAD). Gleiche Sicherheits-Header, CSP und `Cache-Control: no-store`; kein Inline-Stil oder -Skript. —
  Nachweis: `tests/web/test_legal.py::test_legal_pages_*`, `tests/web/test_pages.py` (Inline-Prüfung erweitert)
- [ ] AK2: Fußzeile mit Links „Impressum“ und „Datenschutz“ auf diesen Seiten: Login (200/401/403/429), Einrichtung
  (Tunnel), Einrichtung geschlossen (503), Übersicht, Einstellungen, 404 der App, die beiden Rechtstexte und die
  Impressum-503. Ausgenommen: Text-Antworten der App (400/403/413/421), Fehler von `http.server` und von Caddy. —
  Nachweis: `tests/web/test_legal.py::test_footer_links_on_every_html_page`
- [ ] AK3: Das Impressum kommt aus `/etc/llm-trading-bot/impressum.md` (root, 644); die Web-Unit übergibt den Pfad
  mit `--impressum`. Gelesen bei jedem Abruf mit `O_NOFOLLOW|O_NONBLOCK`, nur reguläre Datei, höchstens 16 KB, UTF-8;
  jeder `OSError`/`UnicodeDecodeError`, eine zu große oder leere Datei → `/impressum` 503 „Impressum nicht
  hinterlegt“ ohne Pfad oder Fehlertext. — Nachweis: `tests/web/test_legal.py::test_impressum_*`
- [ ] AK4: Renderer `web/legal.py::render`: Blöcke durch Leerzeilen; `# `/`## ` Überschriften, `- ` Listen, sonst
  Absatz, einfache Zeilenumbrüche im Absatz → `<br>`; BOM entfernt, CRLF wie LF. Jeder Text wird zuerst mit
  `html.escape(quote=True)` maskiert; danach werden nur Treffer von `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`
  zu `mailto:`-Links (kein anderes Schema, keine Parameter). — Nachweis: `tests/web/test_legal_render.py` (u. a.
  `<script>`, `<meta http-equiv>`, `a"onmouseover=x@y.de`, `javascript:`, `&amp;` ohne Doppelmaskierung, BOM, CRLF)
- [ ] AK5: Die Datenschutzerklärung liegt im Repo als Python-Modul (`web/datenschutz.py`, wie `style.py`/`script.py`
  – kein Datenfile im Wheel), ohne Namen, Anschrift, Domain oder Server-Details; Verantwortlicher „siehe Impressum“.
  Inhalt nach Art. 13 DSGVO, je Punkt mit Zweck, Rechtsgrundlage und Dauer:
  - Hosting: Hetzner Online GmbH, Rechenzentrum in Deutschland, AVV.
  - Caddy: TLS; kein Zugriffslog; Fehler- und Warnmeldungen mit Zeitpunkt, IP-Adresse, aufgerufener Adresse samt
    Parametern, Browserkennung und weiteren Anfrage-Headern, Cookies geschwärzt; höchstens 15 Tage; Art. 6 Abs. 1
    lit. f.
  - Firewall: protokolliert keine Verbindungen.
  - Schutz vor Passwort-Raten: IP-Adresse (IPv6 je /64) bzw. pseudonyme Geräte-ID nur im Arbeitsspeicher, höchstens
    24 Stunden nach dem letzten Fehlversuch bzw. Ende der Sperre.
  - Cookies der öffentlichen Seite mit Name, Zweck und Dauer; HSTS-Hinweis im Browser (1 Jahr); kein Banner nötig
    (§ 25 Abs. 2 Nr. 2 TDDDG).
  - Keine Tracker, keine externen Inhalte oder Schriften, keine automatisierte Entscheidung; keine Besucherdaten an
    OpenAI oder Bitvavo; Let's Encrypt erhält nur den Domainnamen (öffentliche CT-Logs), keine Besucherdaten.
  - E-Mail an `kontakt@` (Migadu, Schweiz, Angemessenheitsbeschluss, AVV), gelöscht nach Erledigung; Rechte der
    Betroffenen inkl. Widerspruch; Beschwerderecht; Stand-Datum.
  — Nachweis: `tests/web/test_legal.py::test_datenschutz_*`
- [ ] AK6: Tests binden den Text an den Code:
  - Cookies aus echten Set-Cookie-Antworten des öffentlichen Wegs (Login-Seite, erfolgreicher Login, Logout): genau
    diese Namen (`__Host-ltb_*`) mit ihrer Max-Age stehen im Text, kein anderer `ltb_`-Name; dafür werden die
    Literale 43200/3600 in `app.py` zu Konstanten.
  - Sperr-Zahlen (5, 60 s, 1 h, 100/h) und die neue Vergessensfrist aus `auth.py`.
  - HSTS-`max-age` aus `deploy/Caddyfile`.
  - Die 15 Tage = `MaxRetentionSec` + `MaxFileSec` der Journal-Datei.
  - Caddy loggt in diesen Namensraum (`CADDY_DROPIN_TEXT`) und hat kein Zugriffslog.
  - Kein `email`/`acme_ca` im Caddyfile.
  - `ufw logging off` in `release.sh public`.

  — Nachweis: `tests/web/test_legal.py::test_datenschutz_matches_*`
- [ ] AK7: Vergessen im Login-Schutz: Ein Eintrag in `PublicGuard` verfällt 24 h nach dem letzten Fehlversuch bzw.
  nach Ende seiner Sperre (bei jedem Zugriff geprüft); eine laufende Sperre wird nie vorzeitig vergessen. —
  Nachweis: `tests/web/test_auth.py::test_public_guard_forgets_*`
- [ ] AK8: Server-Protokolle halten die Frist ein, eingerichtet von `release.sh public` (idempotent, `changes: 0`
  beim zweiten Lauf):
  - Caddy-Drop-in mit `LogNamespace=llm-trading-bot`; Caddy-Neustart nur bei Änderung.
  - Namensraum-Journal mit `MaxFileSec=1day` und `ForwardToSyslog=no`.
  - System-Journal-Drop-in `/etc/systemd/journald.conf.d/50-llm-trading-bot.conf` mit `MaxRetentionSec=14day` und
    `MaxFileSec=1day`, damit alte Einträge mit IP-Adressen von selbst verfallen.
  - `ufw logging off`.
  - Hinweise in `release.sh` und `server.md` nennen `journalctl --namespace=llm-trading-bot -u caddy`.
  - `check-units.sh` prüft den Caddy-Drop-in mit `systemd-analyze verify`.
  - `public off` lässt Drop-ins und Logging-Einstellung stehen.

  — Nachweis: `tests/deploy/test_release.py::test_public_sets_log_limits_*`, `tests/test_deploy_files.py::test_journal_*`
- [ ] AK9: Hochladen:
  - `bash scripts/deploy.sh <alias> <dienst> impressum <datei>` prüft lokal (UTF-8, ≤ 16 KB, nicht leer, kein NUL;
    BOM weg, CRLF → LF).
  - Verbindung 1 lädt nach `incoming/impressum-<sha256>.md` (außerhalb der Sperre); Verbindung 2 führt
    `release.sh impressum <sha256>` mit Deploy-Sperre aus: Hash prüfen, erneut validieren, `/etc/llm-trading-bot` anlegen
    (755), temporäre Datei `install -m 644 -o root`, `mv -f`, eingehende Datei löschen.
  - Gleicher Inhalt → `changes: 0`. Der Modus nutzt `release.sh` des neuesten grünen Commits (wie `status`).
  - `status` meldet `impressum: ok (<Bytes>, sha256 <kurz>) | fehlt | UNGÜLTIG (<Grund>)` und ob die aktive Web-Unit
    `--impressum` übergibt.
  - Der Inhalt erscheint nie in der Ausgabe oder im Log.

  — Nachweis: `tests/deploy/test_release.py::test_impressum_*`, `tests/deploy/test_deploy_script.py::test_impressum_*`
- [ ] AK10: Units und Rückfall:
  - Die Web-Unit trägt fest `--impressum /etc/llm-trading-bot/impressum.md`. Ein Test rendert beide `ExecStart`-Varianten
    wie `release.sh` und lässt sie vom echten CLI-Parser lesen.
  - Die Sandbox bekommt eine Variante „vor 014“: Deploy alt → 014 → Rollback → `ExecStart` ohne `--impressum`,
    Impressum-Datei unberührt.
  - Angepasste Prüfungen: `test_deploy_files.py:115`, `test_release.py` (ExecStart-Enden), `check-units.sh`.

  — Nachweis: `tests/deploy/test_release.py::test_rollback_before_014_*`, `tests/test_cli.py::test_web_unit_execstart_*`
- [ ] AK11: Im Repo kein Impressum mit echten Daten: nur `deploy/impressum.example.md` mit Platzhaltern; ein Test prüft,
  dass kein anderes `impressum*.md` versioniert ist. Die echte Datei liegt lokal außerhalb des Repos. — Nachweis:
  `tests/test_deploy_files.py::test_impressum_only_example_in_repo`
- [ ] AK12: [manuell] Mit Zustimmung, in dieser Reihenfolge:
  1. Nutzer: Postfach `kontakt@<domain>` bei Migadu, AVV mit Hetzner und Migadu geprüft, beide Texte gelesen und
     freigegeben.
  2. `impressum <datei>`, `status`.
  3. Deploy 014.
  4. `public <domain>` erneut (Drop-ins, Logging).
  5. Mit Zustimmung einmalig aufräumen: System-Journal `--rotate` + `--vacuum-time=1s`; `ufw.log*`, `kern.log*`,
     `syslog*` mit UFW-/Caddy-Zeilen leeren.
  6. Prüfen:
     - Von außen `/impressum`, `/datenschutz` 200 ohne Login, Links im Fuß der Login-Seite.
     - `systemctl show caddy -p LogNamespace`.
     - Neue Caddy-Meldung nur im Namensraum, Cookie darin geschwärzt.
     - `ufw status verbose` zeigt Logging aus.
- [ ] AK13: Doku: ADR-0025, ARCHITEKTUR, `docs/betrieb/server.md` (Impressum hochladen/ändern, Protokolle, Aufräumen),
  STAND. — Nachweis: `scripts/docs-check.sh`, Review

## Abgrenzung
- Keine Rechtsberatung: Texte nach bestem Wissen aus den Vorlagen des Nutzers (validachs.de, lageluchs.de), an diese
  Seite angepasst; der Nutzer liest und verantwortet sie.
- Kein Cookie-Banner, keine englische Fassung, keine AGB (kein Angebot an Dritte).
- Die Cookies des Tunnels (`ltb_session`, `ltb_pre`) betreffen nur den Betreiber und stehen nicht im Text.
- SSH-Zugriffe auf den Server sind nicht Teil der Website (das System-Journal ist ab AK8 trotzdem auf 15 Tage begrenzt).
- Hetzner-Backups/Snapshots bleiben aus; werden sie eingeschaltet, ist die Datenschutzerklärung anzupassen.

## Relevante Entscheidungen & Learnings
- ADR-0024 – öffentlicher Weg: Host-Prüfung, Proxy-Header-Sperre, keine Einrichtung über das Internet, kein Zugriffslog.
  Rechtstexte sind neben `/login`, `/app.css`, `/app.js` die einzigen Seiten ohne Login.
- ADR-0015 / ADR-0019 – Sicherheits-Header, CSP `script-src 'self'`, keine Inline-Skripte; Server rendert.
- ADR-0017 – Journal-Namensraum `llm-trading-bot`; ADR-0020 / Spec 013 – Units aus dem Release, Rückfall auf alte
  Releases, Prüfen vor dem Stoppen, Caddy-Drop-in gehört `public`.
- CLAUDE.md – Repo öffentlich; Befehle auf dem Server nur mit Zustimmung; Löschen von Logs nur mit Zustimmung.
- LEARNINGS #web – „Formular-POST hängt“ (Handler schlank), „Sich selbst neu ladende Seite“ (nichts Teures je Abruf).

## Ansatz
- **web:** `web/legal.py` (Renderer, `read_impressum(path)`), `web/datenschutz.py` (Text als Konstante), Routen in
  `handle` vor der Einrichtungs-Weiche, Seiten über `pages._page` ohne Abmelde-Knopf, Fußzeile mit beiden Links.
  `WebApp(..., impressum=None)`, CLI `web --impressum PATH`. `auth.PublicGuard` bekommt eine Vergessensfrist.
- **deploy:** Web-Unit mit festem `--impressum` (die Datei darf fehlen). Caddy-Drop-in, Journal-Grenzen und `ufw
  logging off` gehören zu `release.sh public` (wie der Drop-in heute); `deploy` ändert daran nichts. Neuer Modus
  `impressum` nach dem Muster des Release-Uploads (zwei Verbindungen, Hash, Sperre nur für die Installation).
- **Texte:** Datenschutz nach der Struktur von validachs.de; Impressum aus der Vorlage (Name, c/o-Anschrift,
  `kontakt@<domain>`, § 5 DDG, § 18 Abs. 2 MStV, Haftung für Inhalte/Links) als lokale Datei außerhalb des Repos.
- **Geprüfte Orte von Besucherdaten (2026-11-07, Server lesend und Code):** Caddy (Warnungen im System-Journal und
  per Weiterleitung in `/var/log/syslog`), ufw (Kernel-Log → System-Journal, `ufw.log`, `kern.log`, `syslog`),
  `PublicGuard` (Arbeitsspeicher), Cookies. Ohne Besucherdaten: Web-Journal (keine Client-IPs), Bot-DB und
  DB-Backups, Statusdatei, `deploy.log`.
- **Verworfen:** Impressum im Repo (Personendaten und Domain in der öffentlichen Git-Historie); Datenschutz nur auf
  dem Server (kein Test bemerkt Abweichungen); längere Fristen nur benennen statt begrenzen (unnötig viele
  IP-Adressen); Caddy-Logs abschalten (Zertifikatsfehler blind); eigener Journal-Namensraum für Caddy (geteilte
  200 MB reichen, Warnungen sind selten).

## Plan
- [x] 1. `web/legal.py` (Renderer, Lesen) mit Tests (AK3, AK4)
- [x] 2. Routen, Fußzeile, `--impressum`, Cookie-Konstanten, Vergessensfrist, `datenschutz.py` mit Bindungstests
  (AK1, AK2, AK5–AK7)
- [x] 3. Web-Unit mit `--impressum`, Sandbox „vor 014“, ExecStart-Parser-Test (AK10)
- [x] 4. `release.sh public`: Drop-ins, Journal-Grenzen, `ufw logging off`; `check-units.sh` (AK8)
- [x] 5. `impressum`-Modus in `release.sh`/`deploy.sh`, `status`, Beispieldatei (AK9, AK11)
- [ ] 6. Doku, ADR, Prüfung (Workflow), PR (AK13); Impressum-Datei lokal erzeugen
- [ ] 7. Mit Zustimmung: Server-Schritte (AK12)

## Risiken & offene Fragen
- **Rechtliche Richtigkeit:** keine Rechtsberatung; Freigabe durch den Nutzer (AK12).
- **Postfach:** `kontakt@<domain>` muss vor dem Veröffentlichen erreichbar sein; bis dahin bleibt die Seite ohne
  Impressum-Datei (503) – deshalb Reihenfolge in AK12.
- **Rückfall vor 014:** nimmt die Rechtstexte offline; dann schnell vorwärts deployen oder `public off`.
- **System-Journal 15 Tage:** gilt für alle System-Logs (auch SSH); für einen Ein-Personen-Server ausreichend.
- **Caddy teilt sich den Namensraum** (200 MB, 14 Tage) mit dem Bot; Angriffs-Rauschen kann Bot-Logs früher
  verdrängen – akzeptiert, `status` zeigt die Journal-Größe.

---

## Verlauf
- 2026-11-07: Entwurf von drei Prüfern gegengelesen (Datenschutz-Fakten, Web-Sicherheit, Deploy; 38 Punkte, davon
  3 Blocker), eingearbeitet: Fristen werden auf dem Server durchgesetzt statt nur behauptet (System-Journal,
  `MaxFileSec`, kein Syslog-Weiterleiten, `ufw logging off`, einmaliges Aufräumen); Upload in zwei Verbindungen;
  Vergessensfrist im Login-Schutz; Cookie-Bindung über echte Antworten; Datenschutz als Python-Modul; 405/501
  festgelegt; Renderer-Regeln; BOM/CRLF; Rückfall-Test „vor 014“; Reihenfolge auf dem Server.

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
