# Spec 001: Marktdaten aus der Arena übernehmen

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s001-marktdaten` · **Meilenstein:** M1
- **Erstellt:** 2026-11-05

## Ziel
Der Bot kann von Bitvavo öffentliche Kurse (Bid/Ask) und abgeschlossene Kerzen für BTC/EUR und ETH/EUR
abrufen und daraus je Markt einen geprüften Snapshot bilden. Ungültige, lückenhafte oder veraltete Daten
werden abgelehnt (fail closed), und ein Fehler bei einem Markt blockiert den anderen nicht. Die Logik
stammt aus der Arena und ist dort erprobt.

## Akzeptanzkriterien
- [ ] AK1: Quotes für beide Märkte kommen aus **einem** Aufruf von `/ticker/book`. Abgelehnt werden: fehlender Markt,
  widersprüchliche Duplikate, Bid ≤ 0 / Bid > Ask / nicht endliche Werte, Spread > 50 bp.
  — Nachweis: `uv run pytest tests/market/test_bitvavo_quotes.py`
- [ ] AK2: `closed_candles` liefert nur abgeschlossene Kerzen, aufsteigend sortiert, identische Duplikate
  zusammengefasst. Laufende oder zukünftige Kerzen werden ignoriert. Abgelehnt werden: widersprüchliche Duplikate,
  Millisekunden-Zeitstempel mit Bruchteil oder schief zum Intervall, OHLC außerhalb des Bereichs, Preise ≤ 0,
  negatives oder nicht endliches Volumen, unvollständige Zeilen. Nur Intervalle 1/15/60/240 min.
  — Nachweis: `uv run pytest tests/market/test_bitvavo_candles.py`
- [ ] AK3: Lückenfüllung wie in der Arena: Bei 1-min-Kerzen werden höchstens 2 fehlende Kerzen in Folge mit
  dem vorherigen Schlusskurs und Volumen 0 ergänzt. Größere Lücken, Lücken bei anderen Intervallen und
  Lücken am Ende werden nicht gefüllt. Die Anzahl der ergänzten Kerzen wird zurückgegeben.
  — Nachweis: `uv run pytest tests/market/test_gaps.py`
- [ ] AK4: Nach HTTP 429 macht der Client keine weitere Anfrage, bis die Pause vorbei ist. Die Pause gilt für
  Quotes und Kerzen gemeinsam. Ihre Dauer kommt aus `Retry-After` bzw. `bitvavo-ratelimit-resetat`;
  ohne diese Header 900 s, begrenzt auf 1–3600 s. Fehler enthalten Status und Restdauer, aber keinen Antworttext.
  — Nachweis: `uv run pytest tests/market/test_rate_limit.py`
- [ ] AK5: Der Snapshot je Markt wird abgelehnt bei: zu wenigen Kerzen (Mindestanzahl je Intervall als
  Parameter), Lücken in den letzten 60 Kerzen, veralteten oder zukünftigen Daten
  (Alter < −90 s oder > Intervall + 90 s), Uhrzeitabweichung > 120 s, Quote älter als 20 s.
  Ein Fehler bei BTC liefert trotzdem einen gültigen ETH-Snapshot und umgekehrt (Rückmeldung per `on_error`).
  — Nachweis: `uv run pytest tests/market/test_snapshot.py`
- [ ] AK6: Der Marktdaten-Code kann keine Order auslösen (ADR-0004): Der Client sendet nur `GET` an
  `/v2/ticker/book` und `/v2/<markt>/candles`, ohne Auth-Header und ohne Request-Body.
  — Nachweis: `uv run pytest tests/market/test_public_only.py`
- [ ] AK7: `uv run trading-bot market` holt einmal Live-Daten und zeigt je Markt Bid/Ask/Spread,
  die letzte abgeschlossene Kerze je Intervall und ob der Snapshot gültig ist (sonst den Grund).
  Bei Fehler ist der Exit-Code ≠ 0. — Nachweis: [manuell] Befehl lokal ausführen, Ausgabe in „Verlauf" festhalten
- [ ] AK8: Tests gegen die echte Bitvavo-API haben den Marker `net` und laufen in CI nicht mit.
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
- [ ] 2. `market/http.py` + Tests (Header-Auswertung Retry-After/resetat, Begrenzung, kein Antworttext in Fehlern)
- [ ] 3. `BitvavoClient.quotes()` + 429-Pause + Test „nur öffentliche GETs" (AK1, AK4, AK6)
- [ ] 4. `closed_candles()` + `fill_tiny_gaps()` mit den Arena-Testfällen (AK2, AK3)
- [ ] 5. `market/snapshot.py` + Tests inkl. Fehlerisolation BTC/ETH (AK5)
- [ ] 6. CLI `trading-bot market`, Marker `net` registrieren, Live-Test, CI auf `-m "not net"` (AK7, AK8)
- [ ] 7. Doku: ARCHITEKTUR.md (Modul market), README (Befehl)

## Risiken & offene Fragen
- Bitvavo kann Format oder Rate-Limits geändert haben (in der Arena nie live geprüft) → AK7/AK8 decken es auf.
- Die lokale Windows-Uhr kann abweichen → Uhrzeitprüfung (120 s) schlägt in AK7 eventuell an; dann ist der Grund sichtbar, nicht still.
- Umfang „Client + Snapshot" und Vorführung per CLI: mit der Freigabe am 2026-11-05 bestätigt.

---

## Verlauf

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
