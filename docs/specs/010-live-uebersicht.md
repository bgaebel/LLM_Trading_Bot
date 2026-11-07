# Spec 010: Live-Übersicht ohne Neuladen

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s010-live-uebersicht` · **Meilenstein:** M1 (Nr. 7)
- **Erstellt:** 2026-11-07

## Ziel
Die Übersicht zeigt Änderungen live, ohne dass die Seite neu lädt: Das Alter von „Letzter Zyklus“, Kursen und Dienst
zählt jede Sekunde, eine neue Runde (Equity, Entscheidungen, Trades, GPT-Kosten, Banner) erscheint binnen 5 s. Dafür
lädt die Seite ein eigenes Skript vom selben Server (ADR-0019); ohne Skript bleibt das Neuladen alle 15 s.

## Akzeptanzkriterien
- [x] AK1: CSP `script-src 'self'`, sonst unverändert; auf jeder Antwort. Nur die Übersicht bindet `/app.js` ein
  (`<script src="/app.js" defer>`), keine Seite enthält Inline-Skript oder `on…=`-Attribute. `/app.js` enthält
  keine Daten und wird wie `/app.css` ohne Anmeldung ausgeliefert, mit `Content-Type: text/javascript` und
  `Cache-Control: no-store`. — Nachweis: `tests/web/test_server.py::test_*csp*`, `test_app_js_*`,
  `tests/web/test_pages.py::test_*no_inline*`
- [x] AK2: `app.js` enthält kein `eval`, kein `new Function`, keinen String als Argument für `setTimeout`/
  `setInterval`, kein `document.write`, keine fremde URL; es fragt nur Pfade derselben Herkunft ab. — Nachweis:
  `tests/web/test_app_js.py` (Prüfung des Quelltexts)
- [x] AK3: Alle 5 s holt das Skript `/` (gleiche Herkunft, mit Cookies) und ersetzt die aktualisierbaren Bereiche
  (Kennzahlen, Lebenszeichen, GPT heute, Depots, Equity-Chart, Entscheidungen, Trades, Banner) durch die neue
  Fassung; Scrollposition bleibt, keine Einblend-Animation, der Hintergrund-Chart läuft weiter. Jedes Alter
  („vor N s/min/h“) hat ein `data-at` (Epoche) und zählt im Browser jede Sekunde hoch, mit denselben Stufen wie der
  Server. — Nachweis: `tests/web/test_pages.py::test_ages_carry_their_epoch`, [manuell] AK6
- [x] AK4: Fehler: Scheitert eine Abfrage, zeigt die Seite einen Hinweis „Verbindung unterbrochen“ und versucht es
  mit wachsendem Abstand (5 s bis 60 s) weiter; kommt die Verbindung wieder, verschwindet der Hinweis. Ist die
  Sitzung abgelaufen (Antwort ist die Anmeldeseite), wechselt der Browser zur Anmeldeseite. — Nachweis: [manuell]
  AK6 (Tunnel trennen und wieder verbinden; abgelaufene Sitzung durch Neustart der Weboberfläche)
- [x] AK5: Ohne Skript: `<noscript><meta http-equiv="refresh" content="15"></noscript>` auf der Übersicht; die
  Einstellungsseite lädt weder Skript noch Refresh. — Nachweis: `tests/web/test_pages.py::test_*refresh*`
- [ ] AK6: [manuell] Im Browser (lokal mit Test-DB und auf dem Server per Tunnel): Alter zählt sekündlich, nach einer
  Runde ändern sich Werte ohne Neuladen, Konsole ohne CSP-Verstöße, Verhalten bei Verbindungsabbruch wie AK4.
- [x] AK7: Lint, Format, `mypy src tests tools`, CI grün; Deploy auf den Server gesund (mit Zustimmung).

## Abgrenzung
- Nicht Teil dieser Spec: Live-Daten auf der Einstellungsseite, Server-Sent Events/WebSocket, JSON-API,
  Fremdbibliotheken, Benachrichtigungen.

## Relevante Entscheidungen & Learnings
- ADR-0019 (angenommen mit dieser Spec): eigenes Skript, `script-src 'self'`, Server rendert, Skript tauscht aus.
- ADR-0014/0015: nur 127.0.0.1, Login, Sicherheits-Header, keine fremden Quellen, kein Inline-Skript.
- ADR-0016: je Abfrage ein kurzer Lese-Snapshot – bei 5 s gemessen unkritisch (Spec 007: 0,043 s auf einer Jahres-DB).
- LEARNINGS „Formular-POST hängt: einfädiger HTTPServer …“: Abfragen kommen jetzt alle 5 s; `ThreadingHTTPServer`
  mit Sperre bleibt.
- LEARNINGS „Sich selbst neu ladende Seite darf nicht die ganze Historie lesen“: gilt erst recht bei 5 s.
- Spec 007 AK9 (Meta-Refresh, „kein Skript“) wird damit abgelöst; Ergebnis dort vermerken.

## Ansatz
- `web/app.js` als Paketdatei, ausgeliefert unter `/app.js`. Ablauf: `fetch("/", {credentials: "same-origin"})`,
  `DOMParser`, die markierten Bereiche (`data-live="…"`) per `replaceWith` tauschen; ein Sekundentakt setzt die
  Alterstexte aus `data-at` neu (gleiche Stufen wie `overview_page.age`). Antwort mit Anmeldeseite (Titel bzw.
  `response.url`) → `location.assign("/login")`.
- `overview_page`: Bereiche mit `data-live`, Alter mit `data-at`, `<script src="/app.js" defer>`, Meta-Refresh in
  `<noscript>`. `app.py`: Route `/app.js`, CSP-Zeile.
- Verworfen: siehe ADR-0019.

## Plan
- [x] 1. ADR-0019 annehmen; CSP, Route `/app.js`, Markup (`data-live`, `data-at`, `noscript`) mit Tests (AK1, AK3
  Markup, AK5)
- [x] 2. `app.js` (Abfrage, Austausch, Sekundentakt, Fehler und Sitzungsende) mit Quelltext-Prüfung (AK2–AK4)
- [x] 3. Browser-Prüfung lokal mit Test-DB (AK6 lokal); Doku (ARCHITEKTUR, Spec 007 Ergebnis-Hinweis)
- [x] 4. Mit Zustimmung: PR, Merge, Deploy auf den Server, Browser-Prüfung per Tunnel (AK6, AK7)

## Risiken & offene Fragen
- Erstmals JavaScript: jede Änderung an `app.js` ist sicherheitsrelevant; die Quelltext-Prüfung (AK2) und die CSP
  begrenzen das.
- Hintergrund-Tabs drosseln Timer: Werte holen dann seltener auf – beim Zurückkehren sofort eine Abfrage.
- Die Uhr des Browsers kann abweichen: Das Alter rechnet mit dem Versatz zur Serverzeit (Server liefert seine Zeit
  im Markup mit).

---

## Verlauf
- 2026-11-07: Schritte 1–2 in einem Commit (Markup und Skript gehören zusammen). Das Skript liegt als Python-Modul
  `web/script.py` (wie das Stylesheet), damit es ohne Build-Schritt im Paket landet. Altersstufen identisch zu
  `overview_page.age` (Python rundet halb auf gerade – im Skript nachgebaut, per Node-Test verglichen).
- 2026-11-07: Browser-Prüfung lokal (Kopie der Test-DB, `trading-bot web` auf 127.0.0.1): eine neue Runde per
  `paper-cycle` erschien binnen 5 s ohne Neuladen (Markierung im Fenster blieb), Alter zählten sekündlich, keine
  Konsolenfehler. Gefunden: Wurde der Tab wieder sichtbar, während eine Abfrage lief, startete eine zweite Kette –
  der Abstand nach Fehlern verdoppelte sich mehrfach („60 s“ nach 7 s). Jetzt immer nur eine Abfrage; Node-Tests
  für dieses Verhalten und für das Sitzungsende (Wechsel zur Anmeldung, keine weiteren Abfragen). Den Wechsel zur
  Anmeldung im echten Browser konnte die Prüfung nicht zeigen (das Werkzeug lädt beim Neustart selbst neu).
- 2026-11-07: Prüfung (Workflow, Sicherheit und Verhalten, je Befund Gegenprüfung): Sicherheit ohne Befund; drei
  Verhaltensbefunde bestätigt und behoben – eine hängende Abfrage (halb toter Tunnel) hielt die Schleife ohne
  Hinweis an (jetzt Frist 15 s je Abfrage); der Austausch alle 5 s setzte die waagrechte Scrollposition breiter
  Tabellen zurück und löschte Textauswahl und Suchtreffer (jetzt nur Austausch bei geänderter Seite, also etwa
  einmal je Runde, nie während einer Auswahl, `scrollLeft` bleibt). Im Browser bestätigt: ohne neue Runde bleibt der
  Inhalt unangetastet, eine neue Runde erscheint binnen 5 s, keine CSP-Verstöße.
- 2026-11-07: PR #16 gemergt (c69ea86, CI grün inkl. Node-Tests, 1179 passed), auf dem Server gesund deployt;
  `/app.js` kommt über den Tunnel mit `text/javascript` und CSP `script-src 'self'`. Offen: AK6 auf dem Server –
  Durchsicht durch den Nutzer nach dem Login.

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
