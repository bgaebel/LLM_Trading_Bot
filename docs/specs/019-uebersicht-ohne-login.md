# Spec 019: Übersicht ohne Login

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s019-oeffentliche-uebersicht` · **Meilenstein:** M1 (Nachtrag zu Specs 007/013)
- **Erstellt:** 2026-11-08

## Ziel
Wer die Adresse kennt, sieht die Übersicht des Bots ohne Passwort – nur lesend. Einstellungen, Pause, Freigaben und
der OpenAI-Key bleiben hinter dem Login (ADR-0028). Nebenbei zeigt die Tabelle „Letzte Signale“ nur noch Kauf- und
Verkaufssignale, keine Halte-Entscheidungen (Wunsch des Nutzers 2026-11-08).

## Akzeptanzkriterien
- [ ] AK1: **Lese-Ansicht:** `GET /` ohne gültige Sitzung liefert (Tunnel und öffentlich) 200 mit der Übersicht:
  kein `<form>`, kein CSRF-Token, keine Reiter, kein `Set-Cookie`, ein Link „Anmelden“ auf `/login`; das Live-Skript
  läuft weiter. — Nachweis: `tests/web/test_public_overview.py::test_anonymous_*`
- [ ] AK2: **Ausgeblendet ohne Login:** GPT-Fehlertexte, Datenbank-Fehlerdetails, Hinweise auf `secrets.env` und
  Kommandos; angemeldet bleibt die Übersicht wie bisher. — Nachweis: `::test_hidden_*`, `::test_owner_*`
- [ ] AK3: **Alles andere verlangt den Login:** jede andere Route und jede POST-Aktion ohne Sitzung wie bisher
  (Umleitung auf `/login`, Login-Sperre unverändert); vor der Einrichtung bleibt es bei Einrichtung bzw. 503.
  — Nachweis: `::test_every_other_route_*`, `::test_before_setup_*`
- [ ] AK4: **Last:** Ohne Login liest die Ansicht die Datenbank höchstens einmal je 10 s, egal wie viele Abrufe;
  die Serverzeit der Seite ist je Abruf aktuell. — Nachweis: `::test_cache_*`
- [ ] AK5: **Keine Suchmaschinen:** jede Antwort trägt `X-Robots-Tag: noindex, nofollow`; `/robots.txt` liefert
  `Disallow: /` ohne Sitzung und ohne Cookie. — Nachweis: `::test_robots_*`
- [ ] AK6: **Schalter:** In den Einstellungen „Übersicht ohne Login“ an/aus (POST mit CSRF), gespeichert als
  `WEB_PUBLIC_OVERVIEW` (`0` = aus, fehlt = an); aus → `/` leitet wieder auf `/login`; `secrets.env` unlesbar →
  Login (fail closed); der Bot liest den Eintrag nicht. — Nachweis: `::test_switch_*`, `tests/web/test_credentials.py`
- [ ] AK7: **Nur Paper:** ein Test schlägt fehl, sobald `parse_mode("live")` nicht mehr abgelehnt wird (dann neues
  ADR nötig). — Nachweis: `::test_public_view_needs_a_new_adr_before_live`
- [ ] AK8: **Datenschutz:** Überblick (Übersicht ohne Login lesbar, keine Besucherdaten) und Cookies (beim Abruf
  der Übersicht ohne Login keine Cookies) angepasst, Stand-Datum neu; die Bindungstests prüfen, dass die Lese-Ansicht
  und `/robots.txt` kein Cookie setzen. — Nachweis: `tests/web/test_legal.py`
- [ ] AK9: **Signale:** Die Tabelle zeigt je Depot und Markt das letzte BUY/SELL der neuesten 50 000 Entscheidungen,
  keine HOLD; die Abfrage liest einen rowid-Bereich, nie die ganze Tabelle. — Nachweis:
  `tests/web/test_overview.py::test_decisions_*`
- [ ] AK10: Doku: ADR-0028 (löst 0024 ab), INDEX, ARCHITEKTUR, `docs/betrieb/server.md` (Prüfung nach Deploy).
  — Nachweis: `scripts/docs-check.sh`
- [ ] AK11: [manuell] Mit Zustimmung: Deploy; `https://<domain>/` zeigt ohne Login die Übersicht, `/settings`
  leitet auf `/login`, die Antwort-Header zeigen `X-Robots-Tag` und kein `Set-Cookie`.

## Abgrenzung
- Nicht Teil dieser Spec: Echtgeld-Daten, Ratenbegrenzung in Caddy, eine eigene öffentliche Seite mit anderem
  Inhalt, Auffindbarkeit in Suchmaschinen.

## Relevante Entscheidungen & Learnings
- ADR-0024 → ADR-0028: öffentlicher Weg, gehärteter Login; jetzt Lese-Ansicht ohne Login.
- ADR-0016: Web liest `bot.db` nur lesend; ADR-0019: Live-Skript holt `/` alle 5 s.
- ADR-0025: Datenschutztext an Code gebunden – Cookies und Fristen per Test.
- ADR-0004: kein Echtgeld; die Lese-Ansicht gilt nur im Paper-Modus.
- LEARNING #performance „Sich selbst neu ladende Seite darf nicht die ganze Historie lesen“: Signale-Abfrage begrenzt.
- LEARNING #web „Formular-POST hängt“: eine App-Sperre für alle Anfragen – die Lese-Ansicht muss billig sein.

## Ansatz
In `WebApp.handle` nach der Einrichtungs-Sperre: ohne Sitzung und bei `GET /` mit eingeschaltetem Schalter die
Lese-Ansicht. Die Teile der Übersicht (DB-Schnappschuss, Lebenszeichen, Pausenhinweis) liegen 10 s im Speicher;
`overview_page(..., public=True)` rendert ohne Fehlerdetails und Konfigurationshinweise, `pages._page` ohne Reiter,
mit „Anmelden“. Der Schalter liegt als `WEB_PUBLIC_OVERVIEW` in `secrets.env` (wie `BOT_PAUSED`, eigenes Formular).
Verworfen: eigener Pfad, fertiges HTML im Speicher, Caddy-Ratenbegrenzung (siehe ADR-0028).

## Plan
- [x] 1. Signale ohne HOLD, begrenzte Abfrage (AK9)
- [ ] 2. Lese-Ansicht, Ausblendungen, Zwischenspeicher, robots (AK1–AK5, AK7)
- [ ] 3. Schalter in den Einstellungen und `secrets.env` (AK6)
- [ ] 4. Datenschutz und Bindungstests (AK8)
- [ ] 5. Doku, Prüfung (Workflow), PR (AK10)
- [ ] 6. Mit Zustimmung: Deploy, Prüfung von außen (AK11)

## Risiken & offene Fragen
- **M1-Uhr:** der Deploy startet die 7 Tage neu (Nutzer 2026-11-08); gebündelt mit dem Signal-Filter und
  am selben Tag wie der Deploy von 015.
- **Öffentlich sichtbar:** GPT-Begründungen sind freier Text des Modells – escaped, aber öffentlich.

---

## Verlauf
- 2026-11-08: Nutzer: „machst du auch gleich die Übersichtsseite ohne Login erreichbar?“ – Freigabe des Vorschlags
  (Lese-Ansicht, Ausblendungen, Zwischenspeicher, noindex, Schalter standardmäßig an, nur Paper).

## Ergebnis
<!-- Von /abschluss ausgefüllt. -->
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
