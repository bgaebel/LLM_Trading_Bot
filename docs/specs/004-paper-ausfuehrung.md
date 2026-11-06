# Spec 004: Paper-Ausführung und SQLite-Persistenz

- **Status:** freigegeben
- **Issue:** – · **Branch:** `feat/s004-paper` · **Meilenstein:** M1 (Nr. 3)
- **Erstellt:** 2026-11-06

## Ziel
Der Bot setzt Signale in virtuelle Orders um und speichert alles dauerhaft in SQLite: Depot, Entscheidungen,
Trades und Equity. Ausführungsmodell und Positionslogik sind identisch zur Arena v0.3.1 (Bid/Ask, Gebühr,
Slippage, Grenzen je Coin und gesamt, Mindesttrade). Ein Neustart erzeugt nie doppelte Entscheidungen oder
Trades, ein zweiter Prozess kann nicht auf dieselbe DB schreiben, und bei fehlenden oder veralteten Kursen
wird nicht gehandelt. Ein Befehl führt einen Live-Zyklus im Paper-Modus aus. Das ist die Grundlage für den
Dienst (Nr. 5) und für die LLM-Strategie (3a, braucht DB für Budget und Provenienz).

## Akzeptanzkriterien
- [ ] AK1: `open_db(path)` legt das Schema an (WAL, `busy_timeout`), setzt `PRAGMA user_version = 1` und lehnt
  eine DB mit fehlender Tabelle, fehlendem UNIQUE-Index oder fremder Version ab. Es gibt keine automatische
  Migration. — Nachweis: `uv run pytest tests/paper/test_db.py`
- [ ] AK2: Single-Writer: Eine zweite Sperre auf dieselbe DB schlägt sofort fehl. Die Sperre ist
  betriebssystemeigen und wird bei Absturz frei (wie Arena `WriterLock`).
  — Nachweis: `uv run pytest tests/paper/test_lock.py` (zweiter Prozess per `subprocess`)
- [ ] AK3: Je Agent gibt es ein Depot mit 10.000 EUR Startkapital, das genau einmal angelegt wird. Erneutes
  Öffnen setzt nichts zurück. — Nachweis: `uv run pytest tests/paper/test_portfolio.py`
- [ ] AK4: `record_decision` ist idempotent über `(agent, symbol, bar_ts)`. Ein zweiter Aufruf, auch nach
  neuem Öffnen der DB, erzeugt weder Entscheidung noch Trade. Entscheidung, Trade und Depot ändern sich in
  **einer** Transaktion (`BEGIN IMMEDIATE`); ein Fehler mittendrin rollt alles zurück.
  — Nachweis: `uv run pytest tests/paper/test_decisions.py`
- [ ] AK5: Ausführung identisch zur Arena (`trade_to_target`, `record_decision`, `initialize_buyhold`, Stand
  7563724). Golden-Fixtures entstehen, indem der Generator Signal- und Kursfolgen in einer In-Memory-DB der
  Arena abspielt. Verglichen werden Depot, Trades und Entscheidungen nach jedem Schritt, mit Grenzfällen an
  45 %, 90 %, 50 EUR und 2 % Equity sowie bei begrenzter Verkaufsmenge.
  — Nachweis: `uv run pytest tests/paper/test_golden_execution.py` + `tools/golden/mutation_check.py`
  (neue Mutationen für die Ausführung werden erkannt)
- [ ] AK6: Fail closed: Ohne frische Quotes beider Märkte (älter als 20 s oder einer fehlt) wird die
  Entscheidung als `STALE_QUOTE` gespeichert, ohne Trade. — Nachweis: `uv run pytest tests/paper/test_fail_closed.py`
- [ ] AK7: Der Buy-&-Hold-Vergleich kauft je Markt genau einmal auf frischen Quotes, auch über Neustarts hinweg.
  — Nachweis: `uv run pytest tests/paper/test_buyhold.py`
- [ ] AK8: Gebühr und Slippage kommen aus der Konfiguration, Standard 0,25 % bzw. 0,05 % je Seite (Arena-Code).
  Werte außerhalb von [0, 0,05] werden abgelehnt. Jeder Trade speichert die angewandte Slippage.
  — Nachweis: `uv run pytest tests/paper/test_costs.py`
- [ ] AK9: `uv run trading-bot paper-cycle --db <pfad>` führt einen Live-Zyklus aus: Snapshots holen, Signal
  des aktiven Agenten, Entscheidung und ggf. virtueller Trade, Buy & Hold initialisieren, Equity speichern,
  Zusammenfassung ausgeben. Ein zweiter Lauf im selben Bar erzeugt nichts Neues.
  — Nachweis: [manuell] zweimal ausführen, Ausgabe im „Verlauf"; Testfassung mit Fakes in `tests/test_cli.py`
- [ ] AK10: Lint, Format, `mypy src tests tools`, CI grün; keine DB-Datei im Repo.
  — Nachweis: Befehle aus `CLAUDE.md`, CI im PR, `git ls-files '*.db*'` leer

## Abgrenzung
- Nicht Teil dieser Spec: Drawdown-Stopp und weitere Risiko-Grenzen (`mark_and_risk`, Nr. 4) – die Spalte
  `halted` existiert, wird hier aber nur gelesen. Dazu kommen nicht: Worker-Schleife, Takt, Health (Nr. 5),
  LLM (3a), Backups und Deployment (Nr. 6), Dashboard (Nr. 7), Echtgeld (M2).
- Keine Übernahme der Arena-Historie. Der Bot startet mit einer eigenen, leeren DB.
- Kein `market_ticks`/`llm_calls`/`logs` – nur was Nr. 3 braucht (Logs kommen mit dem Dienst).

## Relevante Entscheidungen & Learnings
- ADR-0004: nur virtuelle Orders; `TRADING_MODE=live` bleibt gesperrt.
- ADR-0007: Ausführung und Kosten sind Teil der Versuchsanordnung, deshalb verhaltensgleich zur Arena, belegt
  durch Golden-Fixtures **mit Grenzfällen** und einen Mutationstest (Learning „Golden-Fixtures beweisen
  Gleichheit nur mit Grenzfällen").
- ADR-0008 (angenommen): eigenes Schema mit Versionsnummer, ein aktiver Agent plus Buy & Hold.
- ADR-0003: Herkunftsvermerke; ADR-0002: Standardbibliothek (`sqlite3`).
- CLAUDE.md: Historie nie still zurücksetzen; nie zwei Prozesse auf derselben DB; DBs nie committen.
- LEARNINGS #windows #pfade: DB-Pfade über `pathlib`; Tests mit `tmp_path`.

## Ansatz
Neues Unterpaket `src/llm_trading_bot/paper/`:
- `db.py`: `open_db`, Schema `portfolios`, `decisions` (UNIQUE agent/symbol/bar_ts), `trades`, `equity`
  (UNIQUE ts/agent), `market` (letzte Quotes mit Zeitstempel, für den 20-s-Test), `user_version = 1`,
  `check_schema`.
- `lock.py`: `WriterLock` (aus Arena `operations.py`; `msvcrt` unter Windows, `fcntl` unter Linux).
- `execution.py`: `trade_to_target`, `record_decision`, `initialize_buyhold`, `record_quotes`,
  `latest_quotes`, `value`, `current_weight` – 1:1 aus `arena.py`/`engine03.py`, typisiert. Kosten und Limits als
  Parameter bzw. Konstanten (`MAX_SYMBOL_WEIGHT` 0,45, `MAX_TOTAL_WEIGHT` 0,90, `MIN_TRADE_EUR` 50,
  `START_CASH` 10.000).
- `cycle.py`: `paper_cycle(db, client, agent, clock)` verbindet Snapshots (Spec 001), Signal (Spec 003) und
  Ausführung. Die Entscheidung gilt pro abgeschlossenem Bar des Strategie-Zeitrahmens (`STRATEGY_TIMEFRAME`
  der Arena: trend 60, breakout/meanrev/ensemble 15).
- Generator: `tools/golden/generate_execution_golden.py` spielt dieselben Folgen gegen Arena-`arena.py`/
  `engine03.py` in einer In-Memory-DB ab.

Aktiver Agent bis zur LLM-Strategie: konfigurierbar (`BOT_AGENT`, Standard `trend`, die Baseline aller
Arena-Experimente). 3a ersetzt ihn durch den LLM-Agenten; Buy & Hold läuft immer als Vergleich.

Verworfen: Arena-Schema 1:1 mit allen sieben Agenten (der Bot ist kein Vergleichsaufbau; das bleibt die Arena).
ORM/SQLAlchemy (neue Abhängigkeit für vier Tabellen). Migrationen (es gibt keine Bestands-DB).

## Plan
- [ ] 1. `db.py` + `lock.py` + Tests (AK1, AK2, AK3)
- [ ] 2. Generator für Ausführungs-Fixtures (Folgen + Grenzfälle) gegen die Arena
- [ ] 3. `execution.py`: `trade_to_target`, `record_decision`, Kosten + Golden-Tests (AK4, AK5, AK8)
- [ ] 4. Quotes/Fail-closed + Buy & Hold (AK6, AK7)
- [ ] 5. `cycle.py` + CLI `paper-cycle` (AK9)
- [ ] 6. Mutationstest um Ausführung erweitern; Doku (ARCHITEKTUR, README)

## Risiken & offene Fragen
- `msvcrt.locking` (Windows) und `fcntl.flock` (Linux) verhalten sich unterschiedlich; der Test mit zweitem
  Prozess läuft in CI unter Linux, lokal unter Windows.
- Die Ausführungslogik der Arena ruft `fee_rate()`/`slip_rate()` aus Umgebungsvariablen auf. Der Generator setzt
  sie, der Bot nimmt Parameter.
- Annahmen, bei der Freigabe am 2026-11-06 bestätigt: Startkapital
  10.000 EUR, Slippage 0,05 % (Arena-Code, nicht 0,03 % aus der Arena-Roadmap), aktiver Agent `trend` bis 3a.

---

## Verlauf

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
