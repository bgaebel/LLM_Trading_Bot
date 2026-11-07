# Spec 013: Weboberfläche öffentlich per HTTPS mit Login

- **Status:** fertig
- **Issue:** – · **Branch:** `feat/s013-oeffentlich-https` · **Meilenstein:** M1 (Nachtrag zu Nr. 7)
- **Erstellt:** 2026-11-07

## Ziel
Der Nutzer öffnet die Weboberfläche von jedem Gerät unter `https://<domain>/` ohne SSH-Tunnel und meldet sich mit
seinem Passwort an. Die App lauscht weiter nur auf `127.0.0.1`; davor steht Caddy als HTTPS-Vorbau mit
automatischem Let's-Encrypt-Zertifikat. Der Tunnel bleibt als zweiter Zugang. Weil die Login-Seite öffentlich ist,
wird sie gehärtet (ADR-0024, löst ADR-0005 ab).

## Festgelegte Werte (vom Nutzer übertragen; Begründung je Zeile)
| Punkt | Wert | Warum |
|---|---|---|
| Domain | nur auf dem Server (`/etc/llm-trading-bot/public-host`), im Repo `<domain>` | öffentliches Repo (CLAUDE.md) |
| `www.<domain>` | dauerhafte Weiterleitung auf `<domain>` | ein Ursprung für Cookies und CSRF |
| Zugangswege | Tunnel: `127.0.0.1:8787` (wie heute); Caddy: eigener Port `127.0.0.1:8788` | der Weg wird am Socket erkannt, nicht an Headern |
| Ports nach außen | 80 (Zertifikat, Weiterleitung) und 443/tcp; HTTP/3 aus | nur was nötig ist |
| Caddy | offizielles, signiertes Caddy-apt-Repo, in die automatischen Updates aufgenommen; Admin-API nur als Unix-Socket | Ubuntus Paket (universe) bekommt keine verlässlichen Sicherheitsupdates |
| Caddy-Grenzen | Header 5 s, Body 10 s, Body ≤ 64 KB, ≤ 16 Verbindungen zur App, Bodys gepuffert | langsame oder große Anfragen binden keine App-Threads |
| Web-Unit | `MemoryMax=256M`, `TasksMax=64`, `OOMScoreAdjust=500` | ein Überlauf trifft die Weboberfläche, nicht den Bot |
| Fehlversuche öffentlich | je Quell-IP (IPv6 je /64): 5 → 60 s, jede weitere Sperre doppelt (bis 1 h); dazu global höchstens 100 Fehlversuche je Stunde | Raten wird praktisch unmöglich |
| Bekanntes Gerät | nach erfolgreichem Login signiertes Geräte-Cookie (90 Tage); damit gelten nur die eigenen Fehlversuche dieses Geräts | ein Angreifer kann den Besitzer nicht aussperren |
| Fehlversuche per Tunnel | eigener Zähler wie bisher | der Tunnel bleibt immer erreichbar |
| Passwort öffentlich | mindestens 12 Zeichen und nicht in einer Liste häufiger Passwörter; per Tunnel weiter 8 (ADR-0015) | 5 Versuche je IP und Stunde machen ein kurzes Passwort sonst über Monate ratbar |
| Cookies öffentlich | `__Host-`-Namen, `Secure`, `HttpOnly`, `SameSite=Strict`; Sitzung 12 h | kein Unterschieben von Cookies anderer Subdomains |
| HSTS | `max-age=31536000` auf Domain und `www`, ohne `preload`; `Server`-Header entfernt | HTTPS erzwingen, kein Software-Fingerabdruck |
| Einrichtung | öffentlich nie; ohne Passwort zeigt die öffentliche Seite nur „Einrichtung per SSH-Tunnel“ | der Einrichtungscode steht im Journal |
| Zugriffslog | keines (keine `log`-Direktive) | keine IPs/Pfade dauerhaft gespeichert |

## Akzeptanzkriterien
- [x] AK1: Zwei Zugangswege, am Socket unterschieden: Port 8787 (Tunnel) nimmt nur `127.0.0.1`/`localhost` (Port
  frei) an; Port 8788 (Caddy, nur mit `--public-host`) nur genau den öffentlichen Host (ohne Port, Kleinschreibung
  egal); alles andere → 421. Auf 8787 führt ein `X-Forwarded-For`/`X-Forwarded-Host` zu 421. Ohne
  `--public-host` gibt es nur 8787 und alles verhält sich wie heute. — Nachweis:
  `tests/web/test_public.py::test_public_host_*`, bestehende Host-Tests grün
- [x] AK2: POSTs auf 8788 verlangen `Origin: https://<domain>` (oder kein Origin, dann `Sec-Fetch-Site` wie heute);
  `http://`, fremde und lokale Origins → 403; auf 8787 bleibt die heutige Regel. CSRF-Token wie bisher. —
  Nachweis: `test_public_origin_*`
- [x] AK3: Auf 8788 heißen alle Cookies `__Host-ltb_*` mit `Secure; HttpOnly; SameSite=Strict; Path=/`, ohne
  `Domain`; nur diese Namen werden dort gelesen (ein `ltb_session` wird ignoriert); der Logout löscht mit denselben
  Attributen. Auf 8787 unverändert `ltb_*` ohne `Secure`. — Nachweis: `test_public_cookies_*`
- [x] AK4: Fehlversuche auf 8788 je Quell-IP aus dem `X-Forwarded-For`, das Caddy setzt (letzter Eintrag; IPv6
  je /64): 5 → 60 s, danach jede Sperre doppelt (120, 240, …, 3600 s); jeder Fehlversuch während einer abgelaufenen
  Sperrstufe zählt neu ab 5; Erfolg setzt die IP zurück. Global höchstens 100 Fehlversuche je Stunde, dann 429 für
  unbekannte Geräte. Ein gültiges Geräte-Cookie (HMAC mit Schlüssel `web-device.key`, 600, im Datenverzeichnis;
  90 Tage) nimmt das Gerät von IP- und globaler Sperre aus, es zählt nur die eigenen Fehlversuche. Ein gesperrter
  öffentlicher Zugang sperrt den Tunnel nicht. Gilt für Login. — Nachweis: `tests/web/test_auth.py::test_backoff_*`,
  `test_device_*`, `tests/web/test_public.py::test_public_lockout_*` (Angreifer-IP gesperrt, andere IP nicht;
  global gesperrt, Gerät mit Cookie meldet sich an)
- [x] AK5: Öffentlich (8788) gibt es keine Einrichtung: Ohne Passwort antwortet jede öffentliche URL mit 503 und dem
  Hinweis „Einrichtung nur per SSH-Tunnel“, ohne Formular, Cookie oder Code. — Nachweis: `test_public_setup_*`
- [x] AK6: Öffentlich anmelden kann sich nur ein Passwort mit mindestens 12 Zeichen, das nicht in der eingebetteten
  Liste häufiger Passwörter steht (geprüft beim Login, da es dann im Klartext vorliegt); sonst freundliche Meldung
  „Für den Zugang aus dem Internet bitte ein längeres Passwort setzen (Einstellungen, per Tunnel oder hier nach dem
  Login)“ – ohne Hinweis, ob das Passwort stimmte. Neu: „Passwort ändern“ in den Einstellungen (altes Passwort +
  neues zweimal, CSRF; neue Passwörter immer ≥ 12 Zeichen und nicht häufig). — Nachweis:
  `tests/web/test_auth.py::test_password_policy_*`, `tests/web/test_public.py::test_public_password_*`,
  `test_change_password_*`
- [x] AK7: Server-Dateien: `deploy/Caddyfile` mit `__PUBLIC_HOST__` (genau die Adressen `__PUBLIC_HOST__` und
  `www.__PUBLIC_HOST__`, `reverse_proxy 127.0.0.1:8788` mit Grenzen und Puffer, `www`-Weiterleitung, HSTS und
  `-Server` in beiden Blöcken, `admin unix//run/caddy/admin.sock`, `protocols h1 h2`, keine `log`- und keine
  `header_up`-Direktive); die Web-Unit hat `__PUBLIC_HOST_ARG__` (leer oder `--public-host <domain>`) und die
  Grenzen. `release.sh`: liest `/etc/llm-trading-bot/public-host` (genau eine Zeile, `^[a-z0-9.-]+\.[a-z]{2,}$`,
  sonst Abbruch vor dem Stoppen); das Caddyfile ist je Release optional, wird nur mit dieser Datei erzeugt und vor
  dem Stoppen mit `caddy validate` geprüft; installiert und neu geladen wird es erst, wenn der neue Release gesund
  ist – ein Fehler dabei lässt das alte Caddyfile stehen und rollt den Bot nie zurück; ein Release vor Spec 013
  bleibt ein gültiger Rückfall, ein Rollback ändert Caddy nicht. CI prüft das Caddyfile mit `caddy validate` und die
  Web-Unit mit leerem und gesetztem `--public-host`. — Nachweis: `tests/deploy/test_release.py::test_public_*`,
  `tests/test_deploy_files.py::test_caddy*`, `scripts/check-units.sh`
- [x] AK8: `bash scripts/deploy.sh <alias> <dienst> public <domain>` (mit Deploy-Sperre): bricht ab, wenn der aktive
  Release das Caddyfile nicht kennt; sonst in dieser Reihenfolge: Caddy-Repo und Paket (`apt-get -o
  DPkg::Lock::Timeout=300`), Origin in die automatischen Updates, Domain-Datei, Units neu erzeugen, `ufw allow
  80/tcp` und `443/tcp`, Caddyfile prüfen und installieren, Caddy und Web-Unit neu starten. Erneuter Aufruf ändert
  nichts. `public off` macht alles rückgängig (Datei, Caddy aus, Regeln weg, Units neu). `status` nennt Caddy-Version
  und öffentlichen Host-Zustand. — Nachweis: `tests/deploy/test_deploy_script.py::test_public_*`, Sandbox-Tests in
  `tests/deploy/test_release.py` mit Fakes für apt, ufw und caddy
- [x] AK9: [manuell] Mit Zustimmung auf dem Server: AAAA-Einträge und Hetzner-Cloud-Firewall prüfen; Release 013
  deployen; `public <domain>`; danach `https://<domain>/login` 200 mit gültigem Zertifikat und HSTS, ohne
  App-`Server`-Header; `http://` → `https://`; `www` → Domain; Login (mit Passwort ≥ 12), Übersicht live,
  Einstellungen, Pausenschalter über HTTPS; Tunnel geht weiter; `ss` zeigt Bot-Ports nur auf `127.0.0.1`, keinen
  Port 2019.
- [x] AK10: Doku: ADR-0024 (löst ADR-0005 ab, ergänzt ADR-0015), INDEX, PROJEKT-Nicht-Ziel angepasst, CLAUDE.md
  („HTTP bindet nur an 127.0.0.1; von außen nur über Caddy“), `docs/betrieb/server.md` (Einrichten, Prüfen,
  Abschalten, Passwort zurücksetzen nur per Tunnel), ARCHITEKTUR; keine echte Domain im Repo; Lint, Format,
  `mypy src tests tools`, Shellcheck, CI grün.

## Abgrenzung
- Nicht Teil dieser Spec: Zwei-Faktor-Anmeldung, öffentliche Seite ohne Login, mehrere Benutzer, eigene Zertifikate,
  Cloudflare, Sperrzähler über Neustarts hinweg (Geräte-Cookie und IP-Zähler machen sie entbehrlich), Änderungen am
  Bot-Dienst. Echtgeld bleibt gesperrt (ADR-0004).

## Relevante Entscheidungen & Learnings
- ADR-0005 wird durch ADR-0024 abgelöst (Nutzerwunsch 2026-11-07); ADR-0015 gilt weiter, ergänzt um die
  Passwortregel für den öffentlichen Zugang.
- ADR-0016/0019/0020: lesender DB-Zugriff, Live-Skript, Deploy mit Rollback – unverändert; Caddy ist eigener Dienst.
- LEARNINGS #web: „Formular-POST hängt …“ (Server bleibt `ThreadingHTTPServer`; Caddy puffert).
- CLAUDE.md: keine Domain, IP, Benutzer oder Ports des echten Servers im Repo.

## Ansatz
- `web/app.py`: zweiter Listener für Caddy, Zugangsweg je Socket; Host-, Origin-, Cookie-, Setup- und
  Passwortregeln je Weg; Passwort ändern. `web/auth.py`: Sperre mit Verdopplung je Schlüssel (IP, Gerät, Tunnel),
  globale Obergrenze, Geräte-Cookie, Passwortregel mit eingebetteter Liste. `cli.py`: `web --public-host`,
  `--public-port`.
- `deploy/Caddyfile`, Web-Unit, `release.sh` (Platzhalter, Prüfung, Reihenfolge), `scripts/deploy.sh` (`public`,
  `public off`), `check-units.sh` und CI (`caddy validate`).
- Verworfen: App direkt öffentlich mit eigenem TLS; nginx + certbot; Cloudflare-Tunnel; Ubuntus Caddy-Paket;
  Tailscale (vom Nutzer verworfen).

## Plan
- [x] 1. Sperre je Schlüssel, Geräte-Cookie, Passwortregel und -änderung (AK4, AK6)
- [x] 2. Zweiter Listener, Host/Origin/Cookies/Setup je Weg, `--public-host` (AK1–AK3, AK5)
- [x] 3. Caddyfile, Unit, `release.sh`, CI-Prüfung (AK7)
- [x] 4. `deploy.sh public` / `public off` / `status` mit Sandbox-Tests (AK8)
- [x] 5. Doku, ADR, PROJEKT/CLAUDE.md; Prüfung (Workflow); PR (AK10)
- [x] 6. Mit Zustimmung: Deploy, `public <domain>`, Prüfung von außen (AK9)

## Risiken & offene Fragen
- **Öffentliche Angriffsfläche:** Login-Seite und statische Dateien sind erreichbar; alles andere verlangt eine
  Sitzung. Gegenmittel siehe Tabelle.
- **Let's Encrypt:** braucht Port 80 und korrekte DNS-Einträge; A-Einträge zeigen auf den Server (2026-11-07), AAAA
  und Hetzner-Cloud-Firewall prüft AK9 vorher. Bis zu 5 fehlgeschlagene Versuche je Stunde – deshalb Firewall vor
  Caddy-Start.
- **Passwort:** Ist das heutige Passwort kürzer als 12 Zeichen, geht der öffentliche Login erst nach einer Änderung
  (Einstellungen, über den Tunnel).

---

## Verlauf
- 2026-11-07: Entwurf gegengeprüft (Workflow, Sicherheit und Umsetzbarkeit; 22 Punkte, eingearbeitet): Domain aus
  dem Repo; Sperre je IP und Geräte-Cookie statt global; Zugangsweg am Socket; öffentlich keine Einrichtung;
  `__Host-`-Cookies; Caddy aus dem offiziellen Repo mit Grenzen und Admin-Socket; Caddyfile optional und vor dem
  Stoppen geprüft, Neuladen erst nach gesundem Release; Reihenfolge und `public off`.
- 2026-11-07: Server-Seite parallel in einem Worktree gebaut und gemergt. Review-Workflow (13 bestätigte Punkte),
  eingearbeitet: Geräte-Cookie bei jedem Login erneuert, Schlüssel nach Passwortwechsel neu (alte Geräte-Cookies
  ungültig), fehlerhafte Cookies kein Absturz; volle Sperrtabelle vergisst keine gesperrten Clients mehr
  (Neue teilen einen Überlauf-Schlüssel); `public` startet die Weboberfläche auch nach einem gescheiterten
  Caddy-Start neu; Caddy-Fehlerseiten mit HSTS und ohne `Server`; Fußzeile/Ticker; Doku-Befehle (`curl -D -`,
  AAAA für www); Tests für `serve()` und CLI-Verdrahtung.
- 2026-11-07: Auf dem Server mit Zustimmung: Release 850221e deployt, `public <domain>` (10 Änderungen), Zertifikat
  beim ersten Versuch. Das bisherige Passwort hatte 8 Zeichen: per Tunnel zurückgesetzt (Datei beiseitegelegt,
  Neustart, Einrichtungscode aus dem Journal) und neu gesetzt; danach öffentlicher Login des Nutzers.

## Ergebnis
- **Erreicht:** Die Weboberfläche ist unter `https://<domain>/` erreichbar (Caddy, Let's Encrypt, HSTS, `www` und
  `http://` leiten um), mit gehärtetem Login; die App bindet weiter nur an `127.0.0.1`, der SSH-Tunnel geht wie
  vorher. AK1–AK10 erfüllt.
- **Abweichungen vom Plan:** Server-Seite (Schritte 3–4) parallel in einem Worktree gebaut und gemergt. Nach dem
  Review zusätzlich: Geräte-Cookie bei jedem Login neu, Schlüsselwechsel nach Passwortänderung, Überlauf-Schlüssel
  statt Verdrängen gesperrter Clients, Caddy-Fehlerseiten mit HSTS, Web-Neustart nach gescheitertem Caddy-Start.
- **Nachweise:**
  - Lokal und CI grün (PR #20, 1822 Tests lokal plus Linux-Sandbox-Tests und `caddy validate` in der CI).
  - Server (2026-11-07, AK9): A-Einträge von Domain und `www` zeigen auf den Server, keine AAAA-Einträge.
    `https://<domain>/login` 200 mit Let's-Encrypt-Zertifikat, HSTS, ohne `Server`-Header; `http://` 308 und
    `www` 301 auf die Domain.
  - Von außen ohne Login: `/` 303 auf `/login`, POST ohne oder mit fremdem `Origin` 403, `/setup` 303,
    100-KB-Body 413 (mit HSTS), fremder Hostname ohne TLS-Handshake.
  - Tunnel: 200; mit `X-Forwarded-For` oder dem öffentlichen Host 421. `ss`: 8787/8788 nur auf `127.0.0.1`,
    80/443 Caddy, kein Port 2019.
  - Ohne Passwort zeigte die öffentliche Adresse 503 (AK5, live). Login des Nutzers öffentlich mit neuem
    Passwort ≥ 12 Zeichen; Pause/Fortsetzen über HTTPS schrieb `secrets.env` (zwischen zwei Runden, daher keine
    PAUSED-Runde im Journal; Wirkung der Pause selbst in Spec 011 auf dem Server belegt).
- **Offen / Folgearbeit:** Kosmetisch: Caddy nennt sich noch in `Via: 1.0 Caddy` und im `Server`-Header der
  automatischen Weiterleitung auf Port 80 (ohne Version). Optional: Hetzner-Cloud-Firewall als zweite Schicht
  (80/443/22).
- **Neue ADRs / Learnings:** ADR-0024 (löst ADR-0005 ab).
