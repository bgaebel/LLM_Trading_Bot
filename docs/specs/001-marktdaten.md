# Spec 001: Marktdaten aus der Arena übernehmen

- **Status:** fertig
- **Issue:** – · **Branch:** `feat/s001-marktdaten` · **Meilenstein:** M1
- **Erstellt:** 2026-11-05

## Ziel
Der Bot kann von Bitvavo öffentliche Kurse (Bid/Ask) und abgeschlossene Kerzen für BTC/EUR und ETH/EUR
abrufen und daraus je Markt einen geprüften Snapshot bilden. Ungültige, lückenhafte oder veraltete Daten
werden abgelehnt (fail closed), und ein Fehler bei einem Markt blockiert den anderen nicht. Die Logik
stammt aus der Arena und ist dort erprobt.

## Akzeptanzkriterien
- [x] AK1: Quotes für beide Märkte kommen aus **einem** Aufruf von `/ticker/book`. Abgelehnt werden: fehlender Markt,
  widersprüchliche Duplikate, Bid ≤ 0 / Bid > Ask / nicht endliche Werte, Spread > 50 bp.
  — Nachweis: `uv run pytest tests/market/test_bitvavo_quotes.py`
- [x] AK2: `closed_candles` liefert nur abgeschlossene Kerzen, aufsteigend sortiert, identische Duplikate
  zusammengefasst. Laufende oder zukünftige Kerzen werden ignoriert. Abgelehnt werden: widersprüchliche Duplikate,
  Millisekunden-Zeitstempel mit Bruchteil oder schief zum Intervall, OHLC außerhalb des Bereichs, Preise ≤ 0,
  negatives oder nicht endliches Volumen, unvollständige Zeilen. Nur Intervalle 1/15/60/240 min.
  — Nachweis: `uv run pytest tests/market/test_bitvavo_candles.py`
- [x] AK3: Lückenfüllung wie in der Arena: Bei 1-min-Kerzen werden höchstens 2 fehlende Kerzen in Folge mit
  dem vorherigen Schlusskurs und Volumen 0 ergänzt. Größere Lücken, Lücken bei anderen Intervallen und
  Lücken am Ende werden nicht gefüllt. Die Anzahl der ergänzten Kerzen wird zurückgegeben.
  — Nachweis: `uv run pytest tests/market/test_gaps.py`
- [x] AK4: Nach HTTP 429 macht der Client keine weitere Anfrage, bis die Pause vorbei ist. Die Pause gilt für
  Quotes und Kerzen gemeinsam. Ihre Dauer kommt aus `Retry-After` bzw. `bitvavo-ratelimit-resetat`;
  ohne diese Header 900 s, begrenzt auf 1–3600 s. Fehler enthalten Status und Restdauer, aber keinen Antworttext.
  — Nachweis: `uv run pytest tests/market/test_rate_limit.py`
- [x] AK5: Der Snapshot je Markt wird abgelehnt bei: zu wenigen Kerzen (Mindestanzahl je Intervall als
  Parameter), Lücken in den letzten 60 Kerzen, veralteten oder zukünftigen Daten
  (Alter < −90 s oder > Intervall + 90 s), Uhrzeitabweichung > 120 s, Quote älter als 20 s.
  Ein Fehler bei BTC liefert trotzdem einen gültigen ETH-Snapshot und umgekehrt (Rückmeldung per `on_error`).
  — Nachweis: `uv run pytest tests/market/test_snapshot.py`
- [x] AK6: Der Marktdaten-Code kann keine Order auslösen (ADR-0004): Der Client sendet nur `GET` an
  `/v2/ticker/book` und `/v2/<markt>/candles`, ohne Auth-Header und ohne Request-Body.
  — Nachweis: `uv run pytest tests/market/test_public_only.py`
- [x] AK7: `uv run trading-bot market` holt einmal Live-Daten und zeigt je Markt Bid/Ask/Spread,
  die letzte abgeschlossene Kerze je Intervall und ob der Snapshot gültig ist (sonst den Grund).
  Bei Fehler ist der Exit-Code ≠ 0. — Nachweis: [manuell] Befehl lokal ausführen, Ausgabe in „Verlauf" festhalten
- [x] AK8: Tests gegen die echte Bitvavo-API haben den Marker `net` und laufen in CI nicht mit.
  — Nachweis: [manuell] `uv run pytest -m net` grün; CI-Log zeigt `-m "not net"`
- [ ] AK9: Lint, Format, mypy strict und CI sind grün.
  — Nachweis: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`, CI-Lauf im PR

## Abgrenzung
- Nicht Teil dieser Spec: Speichern in SQLite, Worker-Schleife oder Polling-Takt (M1 Nr. 3/5), Indikatoren
  und Strategien (Nr. 2), WebSocket-Feed, weitere Märkte oder Börsen, private oder Trading-Endpunkte von Bitvavo.
- Die Mindestanzahl Kerzen je Intervall legt die Strategie in Nr. 2 fest; hier ist sie nur ein Parameter
  (Standard: Werte der Arena 45/110/110/55).

## Relevante Entscheidungen & Learnings
- ADR-0003: Code aus `LLM-Trading-Arena@7563724` übernehmen (`apps/arena/bitvavo.py`, Teile von `arena.py`
  und `strategies.py`), Herkunftsvermerk im Dateikopf, an Typen und pytest anpassen, keine Abhängigkeit zur Arena.
- ADR-0004: nur öffentliche Lesezugriffe; kein Order-Pfad, keine Keys (→ AK6).
- ADR-0002: mypy strict, pytest, Standardbibliothek zuerst (`urllib`, keine neue Abhängigkeit).
- LEARNINGS #encoding: Antworten explizit als UTF-8 dekodieren. #windows #pfade: Testdaten über `tmp_path` bzw. relativ zu `__file__`.
- Arena-Roadmap P2: Dort wurden die Bitvavo-Endpunkte nie mit Live-Daten verifiziert → AK7/AK8 holen das nach.

## Ansatz
Neues Unterpaket `src/llm_trading_bot/market/`:
- `models.py` – `Symbol` (BTCEUR/ETHEUR → `BTC-EUR`/`ETH-EUR`), `Quote` (mid, spread_bps, validate), `Candle`.
- `http.py` – schmale Transport-Schnittstelle (`Protocol`) und `UrllibTransport`. `HTTPFailure` liest die
  Retry-Header aus (aus `arena.http_json`, nur der GET-Teil).
- `bitvavo.py` – `BitvavoClient(transport, monotonic=…)` mit `quotes()`, `closed_candles()` und der
  429-Pause; `fill_tiny_gaps()`.
- `snapshot.py` – `MarketSnapshot` mit Prüfung (aus `strategies.MultiSnapshot.validate`) und
  `build_snapshots(...)` mit Fehlerisolation je Markt (aus `engine03.live_snapshots`, ohne Cache-Logik
  des Workers).

Abweichung von der Arena: Die 429-Pause gehört zur Client-Instanz statt zu globalen Modulvariablen, damit sie
testbar ist und kein versteckter Zustand entsteht. Der Bot nutzt genau eine Instanz für alle Abrufe. Uhr und
Transport werden injiziert, damit die Tests ohne Netz und ohne Schlafen laufen.
Verworfen: `httpx`/`requests` – bringt für zwei GET-Endpunkte nur eine Abhängigkeit mehr. Arena-Datei
unverändert kopieren – flache Imports und globaler Zustand widersprechen ADR-0002.

## Plan
- [x] 1. `market/models.py` + Tests (Quote-Prüfung, Spread, Symbole)
- [x] 2. `market/http.py` + Tests (Header-Auswertung Retry-After/resetat, Begrenzung, kein Antworttext in Fehlern)
- [x] 3. `BitvavoClient.quotes()` + 429-Pause + Test „nur öffentliche GETs" (AK1, AK4, AK6)
- [x] 4. `closed_candles()` + `fill_tiny_gaps()` mit den Arena-Testfällen (AK2, AK3)
- [x] 5. `market/snapshot.py` + Tests inkl. Fehlerisolation BTC/ETH (AK5)
- [x] 6. CLI `trading-bot market`, Marker `net` registrieren, Live-Test, CI auf `-m "not net"` (AK7, AK8)
- [x] 7. Doku: ARCHITEKTUR.md (Modul market), README (Befehl)

## Risiken & offene Fragen
- Bitvavo kann Format oder Rate-Limits geändert haben (in der Arena nie live geprüft) → AK7/AK8 decken es auf.
- Die lokale Uhr kann abweichen. Die 120-s-Prüfung vergleicht nur Snapshot-Zeit und Prüfzeit derselben Uhr (erkennt alte Snapshots, nicht eine falsch gehende Uhr); eine falsche Uhr fällt höchstens über die Frische-Prüfung der Kerzen (±90 s) auf. Auf dem Server sorgt NTP für die Uhr (Spec 002).
- Umfang „Client + Snapshot" und Vorführung per CLI: mit der Freigabe am 2026-11-05 bestätigt.

---

## Verlauf
- 2026-11-06: Quotes kommen wie in der Arena aus **einer** Anfrage für beide Märkte. Fällt `/ticker/book` aus oder fehlt ein Markt darin, sind beide Märkte betroffen. Die Isolation aus AK5 gilt für Kerzen und für die Prüfung je Markt (auch Spread, fehlender Quote). Quotes werden erst nach den Kerzen geholt, weil sie am schnellsten veralten.
- 2026-11-06: `build_snapshots` fängt nur Datenfehler (`MarketDataError`, `HTTPFailure`, `OSError`, `ValueError`), nicht `Exception` wie die Arena, damit Programmierfehler sichtbar bleiben.
- 2026-11-06: Die Prüfung der Kerzen auf plausible OHLC-Werte steckt im Parser (`_parse_candle`) und wird im Snapshot nicht wiederholt.
- 2026-11-06: Test-Hilfsmodul `tests/fakes.py`; dafür `pythonpath = ["tests"]` (pytest) und `mypy_path = "src,tests"`.
- 2026-11-06 (23:18 UTC am 05.11.): erster Live-Abruf, damit erstmals gegen echte Bitvavo-Daten geprüft (in der Arena nie geschehen). `uv run trading-bot market` → beide Snapshots OK, Exit 0; BTC-EUR Spread 1,34 bp, ETH-EUR 2,68 bp; 1m 500 Kerzen, 15m/60m/240m je 499, 0 gefüllte Lücken. `uv run pytest -m net` → 4 passed. Das Format der Antworten entspricht dem, was der Arena-Code erwartet.
- 2026-11-06: Die CLI holt Kerzen und Quotes selbst und prüft mit `MarketSnapshot.validate`, statt `build_snapshots` aufzurufen. So kann sie auch bei ungültigem Snapshot die Daten zeigen. Exit-Codes: 0 OK, 1 mindestens ein Markt ungültig, 2 Quotes nicht abrufbar.
- 2026-11-06 (Prüfer, Blocker): Die eingeengte Fehlerliste `DATA_ERRORS` ließ echte Feed-Fehler durch – `http.client.IncompleteRead`/`BadStatusLine` (urllib verpackt sie nicht in `OSError`) und `OverflowError` bei JSON-`Infinity` im Zeitstempel. Folge: Ein kaputter BTC-Abruf kostete ETH den Snapshot. Behoben: Transport wandelt `HTTPException` in `TransportError`, Parser fängt `OverflowError`, `DATA_ERRORS` ergänzt; Ende-zu-Ende-Test `tests/market/test_isolation_e2e.py` über den echten urllib-Pfad.
- 2026-11-06 (Prüfer, Hinweise): Laufende/zukünftige Kerzen werden wieder **vor** den Wertprüfungen verworfen (wie Arena und AK2); eine kaputte laufende Kerze verwirft nicht mehr das Intervall. `min_bars` ohne ein Intervall → `KeyError` statt still 1. `HTTPError` ohne Header → `HTTPFailure` statt `AttributeError`. Vorbedingung von `MarketSnapshot` (Zeilen vom Parser geprüft) im Docstring – wichtig, wenn Kerzen später aus SQLite kommen. Herkunftsvermerke exakt im ADR-0003-Format, auch in den aus Arena-Tests abgeleiteten Testdateien.
- 2026-11-06 (zweite Prüfung: bereit): Restliche Hinweise umgesetzt – `RecursionError` beim JSON-Parsen → `TransportError`, `assert` zur Typverengung durch explizite Prüfung ersetzt, `TransportError`/`DATA_ERRORS` in ARCHITEKTUR.md.

## Ergebnis
- **Erreicht:** Unterpaket `market` (Modelle, GET-only-Transport, Bitvavo-Client, Snapshot-Prüfung) aus der Arena
  übernommen und an ADR-0002 angepasst; CLI `trading-bot market`; Bitvavo-Endpunkte erstmals live geprüft.
- **Abweichungen vom Plan / von der Arena:** 429-Pause je Client-Instanz statt global; `build_snapshots` fängt nur
  `DATA_ERRORS` = `MarketDataError`, `HTTPFailure`, `TransportError`, `OSError`, `ValueError`, `OverflowError`
  (Arena: `Exception`) – Transport wandelt `http.client.HTTPException` und `RecursionError` in `TransportError`;
  Quotes nach den Kerzen; OHLC-Plausibilität nur im Parser (Vorbedingung im Docstring von `MarketSnapshot`);
  CLI prüft selbst mit `MarketSnapshot.validate`, um auch ungültige Daten zu zeigen.
- **Nachweise:** `uv run pytest` 123 passed, 4 deselected; `uv run pytest -m net` 4 passed; ruff, format,
  `mypy src tests`, shellcheck grün; `uv run trading-bot market` → 2× snapshot: OK, Exit 0 (BTC 1,34 bp, ETH 2,68 bp).
  Unabhängiger Prüfer: erst ein Blocker (Isolation bei `IncompleteRead`/`OverflowError`), nach `c82a61a` bereit;
  neue Tests schlagen gegen den alten Stand fehl. AK9: CI-Lauf im PR.
- **Offen / Folgearbeit:** Speichern und Worker-Takt (M1 Nr. 3/5); bei Kerzen aus SQLite die Zeilenprüfung
  wiederholen (Vorbedingung `MarketSnapshot`); Strategie legt `min_bars` fest (Nr. 2).
- **Neue ADRs / Learnings:** keine neuen ADRs. Erkenntnis aus der Prüfung im Verlauf: eine eingeengte
  Fehlerliste muss alle realen Transportfehler abdecken, sonst bricht die Marktisolation.
