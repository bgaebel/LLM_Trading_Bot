# Spec 006: Weboberfläche mit Login für Zugangsdaten und Einstellungen

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s006-web` (baut auf `feat/s005-llm-strategie` auf) · **Meilenstein:** M1 (Nr. 7, Teil)
- **Erstellt:** 2026-11-06

## Ziel
Der Betreiber startet `trading-bot web --data-dir <dir>` und öffnet die Seite im Browser (lokal oder auf dem
Server per SSH-Tunnel). Beim ersten Start legt er mit einem Einrichtungscode aus dem Terminal sein Passwort fest,
danach loggt er sich ein und trägt den OpenAI-Key und die Einstellungen der GPT-Depots ein. `paper-cycle` nutzt
diese Werte. Keys verlassen den Rechner nicht, werden nie wieder vollständig angezeigt und stehen nie im Repo.

## Akzeptanzkriterien
- [ ] AK1: Der Server bindet nur an `127.0.0.1`; eine andere Adresse wird mit Fehler abgelehnt. — Nachweis:
  `uv run pytest tests/web/test_server.py`
- [ ] AK2: Ersteinrichtung nur mit dem beim Start gedruckten Einrichtungscode; Passwort ≥ 8 Zeichen (ADR-0015); danach ist
  `/setup` gesperrt. Gespeichert wird nur ein `scrypt`-Hash mit Salt (Datei 600). — Nachweis:
  `uv run pytest tests/web/test_auth.py tests/web/test_server.py`
- [ ] AK3: Login mit falschem Passwort scheitert; nach 5 Fehlversuchen 60 s Sperre; Sitzungs-Cookie `HttpOnly`,
  `SameSite=Strict`, 12 h; Logout beendet die Sitzung; ohne Sitzung leiten alle Seiten zum Login. — Nachweis:
  `uv run pytest tests/web/test_server.py`
- [ ] AK4: Formulare ohne gültiges CSRF-Token werden abgelehnt (403) – nach dem Login das Token der Sitzung, auf
  Login und Ersteinrichtung ein Token per Double-Submit-Cookie (`SameSite=Strict`); zusätzlich werden POSTs mit
  fremdem `Origin` oder `Sec-Fetch-Site: cross-site` abgelehnt, ohne als Fehlversuch zu zählen. — Nachweis:
  `uv run pytest tests/web/test_server.py`
- [ ] AK5: Einstellungen speichern: `OPENAI_API_KEY` und die Spec-005-Werte (`BOT_AGENTS`, `LLM_DAILY_BUDGET_EUR`,
  Anteile je Depot, `LLM_EFFORT`) werden wie in `load_config` geprüft, atomar in `secrets.env` (600) geschrieben;
  ein gespeicherter Key erscheint nie mehr vollständig im HTML (nur `…abcd`); leeres Key-Feld = unverändert;
  „Key löschen" entfernt ihn. Unbekannte Felder werden verworfen. — Nachweis: `uv run pytest tests/web/test_credentials.py
  tests/web/test_server.py`
- [ ] AK6: „Verbindung testen" ruft nur die kostenlose Modell-Abfrage auf und zeigt Erfolg oder den Fehlertyp, nie
  den Key. — Nachweis: `uv run pytest tests/web/test_server.py` (Fake-Client)
- [ ] AK7: `paper-cycle --secrets <datei>` (Standard: `secrets.env` neben der DB) nutzt die gespeicherten Werte;
  gesetzte Umgebungsvariablen gehen vor. — Nachweis: `uv run pytest tests/test_cli.py`
- [ ] AK8: Sicherheits-Header auf jeder Antwort (CSP `default-src 'self'`, kein Inline-Skript, `X-Frame-Options:
  DENY`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`). — Nachweis: `uv run pytest tests/web/test_server.py`
- [ ] AK9: [manuell] Seite im Browser: Ersteinrichtung, Login, Key eintragen, Verbindung testen, Logout.
- [ ] AK10: Lint, Format, `mypy src tests tools`, CI grün; kein Secret im Repo. — Nachweis: Befehle aus `CLAUDE.md`

## Abgrenzung
- Nicht Teil dieser Spec: Status-/Depot-Ansicht und Charts (Rest von Nr. 7), Starten/Stoppen des Bots über die
  Seite, Deployment als systemd-Dienst und SSH-Tunnel-Anleitung auf dem Server (Nr. 6), mehrere Nutzer, 2FA,
  Bitvavo-Keys (ADR-0004), Anthropic-Keys (kein Claude-Depot).

## Relevante Entscheidungen & Learnings
- ADR-0015 (löst ADR-0014 ab, Passwort ≥ 8 Zeichen): 127.0.0.1, Passwort-Login mit Einrichtungscode, `secrets.env` außerhalb des Repos.
- ADR-0005: Zugriff auf dem Server nur per SSH-Tunnel. ADR-0004: keine Bitvavo-Keys mit Handelsrecht vor M2.
- ADR-0006: Dienst ohne Root; darum nicht `/etc/llm-trading-bot/env` beschreiben.
- ADR-0012/0013: Einstellungen und Prüfregeln der GPT-Depots (`load_config`).
- CLAUDE.md: Secrets nie committen, loggen oder in Diffs zeigen; HTTP nur 127.0.0.1.
- LEARNINGS #windows #encoding: UTF-8 überall, Ersetzungen mit Nicht-ASCII per Edit.

## Ansatz
Paket `src/llm_trading_bot/web/`:
- `auth.py`: `PasswordStore` (scrypt, Datei 600), `LoginGuard` (Fehlversuche, Sperre), `Sessions`
  (Token, CSRF, Ablauf), Einrichtungscode.
- `credentials.py`: `SecretsFile` (Lesen/Schreiben im env-Format, atomar, 600), Feldliste, Prüfung über
  `load_config`, Maskierung.
- `app.py`: Request-Handler (`/setup`, `/login`, `/logout`, `/settings`, `/settings/test`), HTML aus
  `html.escape`-geschützten Vorlagen, Sicherheits-Header; `serve(data_dir, port)`.
- `cli.py`: Befehl `web`; `paper-cycle --secrets`.
Verworfen: Framework, öffentliche Erreichbarkeit, Keys in der DB (siehe ADR-0014/0015).

## Plan
- [x] 1. `auth.py` + Tests (AK2, AK3)
- [x] 2. `credentials.py` + Tests (AK5)
- [x] 3. `app.py` + `web`-Befehl + Server-Tests (AK1–AK6, AK8)
- [x] 4. `paper-cycle --secrets` (AK7)
- [x] 5. Doku (ARCHITEKTUR, README, `docs/betrieb/`), Seite im Browser zeigen – die Prüfung durch den Nutzer ist
  AK9 und noch offen

## Risiken & offene Fragen
- Freigabe: Der Nutzer hat am 2026-11-06 angewiesen, autonom weiterzuarbeiten, bis er die Webseite sieht; Spec und
  ADR-0014 wurden deshalb ohne vorherige Durchsicht als angenommen geführt. Der Nutzer hat danach die Passwortlänge
  auf 8 Zeichen geändert (ADR-0015, löst 0014 ab); der Rest gilt als bestätigt, bis er etwas anderes sagt.
- Unter Windows (lokal) setzen Dateirechte 600 nur teilweise durch; auf dem Server (Linux) gelten sie.
- Sitzungen liegen im Speicher: Neustart = neu einloggen (gewollt einfach).

---

## Verlauf
- 2026-11-06: Schritte 1–4 umgesetzt. Zusätzlich zur Spec: Prüfung des Host-Headers (Schutz gegen DNS-Rebinding),
  Größenlimit 16 KB für Formulare, keine Request-Logs (nichts von Cookies oder Formularen landet in Logs).
  `web/secrets.py` in `credentials.py` umbenannt (Verwechslung mit dem Standardmodul `secrets`).
  `RUF001` für `web/*.py` ausgenommen (deutsche UI-Texte mit Gedankenstrich).
- 2026-11-06: Seite lokal gestartet (`.claude/launch.json`, nur lokal, nicht im Repo) und im Browser geprüft:
  Ersteinrichtung wird angezeigt. Ersteinrichtung, Login und Key-Eingabe macht der Nutzer selbst (AK9).
- 2026-11-06: Nutzer: Passwort mindestens 8 statt 12 Zeichen → ADR-0015. Server neu gestartet (neuer Code).
- 2026-11-06: Nutzer meldete „Passwort festlegen tut nichts“: einfädiger `HTTPServer` blockierte auf einer leeren
  Reserve-Verbindung des Browsers → `ThreadingHTTPServer` mit Socket-Timeout und Lock, Regressionstest, Learning.
  Ein POST kam dennoch durch: `web-auth.json` existiert, das Passwort des Nutzers ist gesetzt.
- 2026-11-06: Unabhängige Prüfung (Workflow, 6 Blickwinkel, je Befund 3 Skeptiker): 17 bestätigte Befunde zu 006,
  kein Blocker. Behoben: Host-Prüfung nur noch auf den Hostnamen (Tunnel auf anderem lokalen Port bekam 421);
  Cookies tolerant selbst gelesen (fremde Cookies auf 127.0.0.1 machten die Sitzung unlesbar), eigene Namen
  `ltb_session`/`ltb_pre`; Login und Ersteinrichtung mit Double-Submit-Token und Origin/`Sec-Fetch-Site`-Prüfung
  (fremde Seiten konnten die Sperre auslösen oder raten); Sicherheits-Header auch auf Antworten von `http.server`
  selbst; `Content-Length` streng geprüft; `secrets.env` und `web-auth.json` in `.gitignore`; alle Unicode-
  Zeilenumbrüche und Steuerzeichen abgelehnt (sonst ließ sich eine zweite Zeile in `secrets.env` schmuggeln),
  GPT-Einstellungen auch bei rein klassischen Depots geprüft; alle Effort-Stufen auswählbar; belegter Port im
  `web`-Befehl als Meldung. Tests für jeden Befund ergänzt (Logout serverseitig, POST ohne Sitzung, `--secrets`
  mit Key bis zum Modell, `web --host`).

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
