# Spec 003: Indikatoren und klassische Strategien aus der Arena

- **Status:** in Arbeit
- **Issue:** – · **Branch:** `feat/s003-strategien` · **Meilenstein:** M1 (Nr. 2)
- **Erstellt:** 2026-11-06

## Ziel
Der Bot berechnet aus einem geprüften `MarketSnapshot` (Spec 001) die Indikatoren der Arena. Dazu gehören die
Signale aller klassischen Strategien (Trend, Breakout, Mean Reversion, Ensemble, Buy & Hold) und der Kontext, den
der LLM-Agent der Arena bekommt (`feature_payload`). Alles verhält sich **exakt wie in Arena v0.3.1**. Der Kontext
ist die Grundlage für die LLM-Strategie, die der Bot laut Nutzerentscheidung in M1 fahren soll. Sie kommt als
eigene Spec nach der Persistenz, weil sie die Budgetsperre in SQLite braucht. Diese Spec enthält nur reine Logik:
keine Orders, kein Netz, keine Persistenz.

## Akzeptanzkriterien
- [ ] AK1: `ema`, `rsi`, `atr`, `adx` und `indicators()` liefern auf den Golden-Fixtures dieselben Werte wie
  Arena@7563724. Gleitkommazahlen sind gleich bis auf relative Abweichung ≤ 1e-12, Texte (z. B. `regime`) exakt.
  — Nachweis: `uv run pytest tests/strategy/test_golden_indicators.py`
- [ ] AK2: Alle fünf Strategien liefern auf den Fixtures dieselben Ausgaben wie die Arena: `action`,
  `target_fraction`, `confidence` und `reason`, jeweils für `current_weight` 0, 0.25 und 0.45.
  — Nachweis: `uv run pytest tests/strategy/test_golden_signals.py`
- [ ] AK3: Die Fixtures decken jeden Zweig ab: BUY, SELL und HOLD je Strategie; die Marktphasen TREND, RANGE
  und TRANSITION beim Ensemble; zu kurze Reihen bei RSI/ATR/ADX (Rückfallwerte).
  — Nachweis: `uv run pytest tests/strategy/test_golden_coverage.py` (zählt die Zweige in den Fixtures)
- [ ] AK4: `feature_payload()` liefert für die Fixtures denselben Inhalt wie die Arena (Schlüssel und Werte), dazu
  `cost_roundtrip(spread, fee, slippage)` = 2·(fee+slippage) + Spread. Gebühr und Slippage sind Parameter, keine
  Umgebungsvariablen. — Nachweis: `uv run pytest tests/strategy/test_golden_payload.py`
- [ ] AK5: Ein Signal ist ein geprüfter Typ: `action` ∈ BUY/SELL/HOLD, `target_fraction` ∈ {0, 0.25, 0.45},
  `confidence` ∈ [0, 1], nicht leerer `reason`, dazu Strategiename und Strategieversion `v0.3.1`.
  Eine unbekannte Strategie ist ein Fehler. — Nachweis: `uv run pytest tests/strategy/test_signal.py`
- [ ] AK6: Die Fixtures sind reproduzierbar. `tools/golden/generate_strategy_golden.py <arena-pfad>` erzeugt sie aus
  dem Arena-Checkout neu; die Datei nennt Arena-Commit und Generator. Ohne diese Angabe gibt es zur Laufzeit keine
  Abhängigkeit zur Arena. — Nachweis: [manuell] Generator erneut ausführen, `git diff --exit-code tests/strategy/fixtures/`
- [ ] AK7: `uv run trading-bot signals` holt einmal Live-Daten und zeigt je Markt die Signale aller fünf Strategien
  mit Grund. Ist der Snapshot ungültig, zeigt es den Grund und endet mit Exit-Code ≠ 0.
  — Nachweis: [manuell] Befehl ausführen, Ausgabe in „Verlauf"
- [ ] AK8: Lint, Format, `mypy src tests` und CI sind grün.
  — Nachweis: Befehle aus `CLAUDE.md`, CI-Lauf im PR

## Abgrenzung
- Nicht Teil dieser Spec: LLM-Aufruf, Prompt, Antwortschema, Budget (eigene Spec nach M1 Nr. 3); virtuelle
  Ausführung, Positionen, Risiko-Limits, Persistenz (Nr. 3/4); Takt oder Worker (Nr. 5).
- Keine Änderung an Parametern oder Logik der Strategien, auch keine „offensichtlichen" Verbesserungen
  (ADR-0007). Die deutschen `reason`-Texte bleiben, weil sie Teil des Vertrags der Arena sind.
- Kein Backtest. Ergebnisse der Arena werden nicht ausgewertet.

## Relevante Entscheidungen & Learnings
- ADR-0003: Übernahme mit Herkunftsvermerk, keine Laufzeitabhängigkeit zur Arena (deshalb Fixtures statt Import).
- ADR-0007: Strategien verhalten sich identisch zu v0.3.1. Änderungen sind nur als neue
  Strategieversion mit eigenem Test zulässig.
- ADR-0002: mypy strict, Standardbibliothek (`statistics`, `math`).
- ADR-0004: Strategien erzeugen nur Signale; Ausführung ist nicht Teil davon.
- Spec 001 / `MarketSnapshot`: Vorbedingung geprüfter Zeilen ist erfüllt, weil die Bars aus dem Parser kommen.
- LEARNINGS #python #isolation: Fehler in einem Markt dürfen den anderen nicht blockieren. Die Strategien laufen je
  Snapshot, deshalb kommt das erst in Nr. 5 zum Tragen.

## Ansatz
Neues Unterpaket `src/llm_trading_bot/strategy/`:
- `indicators.py`: `ema`, `rsi`, `atr`, `adx`, `indicators(snapshot, roundtrip_cost_frac)`; 1:1 aus `strategies.py`,
  typisiert, **Rechenreihenfolge unverändert** (sonst weichen Gleitkommazahlen ab).
- `signals.py`: `Signal` (frozen dataclass, `validate`), `STRATEGIES`, `STRATEGY_VERSION = "v0.3.1"`,
  `run_strategy(name, snapshot, indicators, current_weight)`.
- `payload.py`: `feature_payload(...)`, `cost_roundtrip(spread_bps, fee_rate, slippage)`.
- `tools/golden/generate_strategy_golden.py`: lädt Arena-`strategies.py` aus einem übergebenen Pfad, erzeugt
  deterministische synthetische Snapshots (Szenarien: Aufwärts-/Abwärtstrend, Seitwärts, Ausbruch mit Volumen,
  überverkaufte Erholung, flach, zu kurz) und schreibt Ein- und Ausgaben nach `tests/strategy/fixtures/*.json`.

Verworfen: Arena-Datei unverändert kopieren (flache Imports, untypisiert, ADR-0002). Arena im Test importieren
(Laufzeit- bzw. Testabhängigkeit, widerspricht ADR-0003). Nur Stichproben-Tests ohne Fixtures (beweist keine
Gleichheit).

Kostenannahmen: Die Arena nutzt im Code `TAKER_FEE=0.0025` und `SLIPPAGE=0.0005`. In der Roadmap der Arena steht
0.0003 für die Slippage; maßgeblich ist der Code. Hier sind beide nur Parameter, die Werte legt Nr. 3 fest.

## Plan
- [x] 1. Generator + Fixtures (aus Arena@7563724) + `indicators.py` + Golden-Test der Indikatoren (AK1, AK6)
- [x] 2. `signals.py` mit `Signal`, fünf Strategien + Golden-Test der Signale + Zweigabdeckung (AK2, AK3, AK5)
- [x] 3. `payload.py` mit `feature_payload` und `cost_roundtrip` + Golden-Test (AK4)
- [x] 4. CLI `trading-bot signals` (AK7)
- [x] 5. Doku: ARCHITEKTUR.md (Modul strategy), README

## Risiken & offene Fragen
- Gleitkommazahlen: Jede Umformung (z. B. `sum` statt Schleife) kann die letzten Stellen ändern. Gegenmittel: 1:1
  übernehmen, Toleranz 1e-12 relativ, Abweichungen nie durch eine größere Toleranz „lösen".
- Synthetische Szenarien müssen jeden Zweig treffen (AK3). Ist ein Zweig schwer zu erreichen, wird das Szenario
  angepasst, nie die Strategie.
- Die Arena hat für ihre Strategien kaum eigene Tests. Die Fixtures beweisen Gleichheit, aber nicht, dass die
  Strategien gut sind (Nicht-Ziel: keine Gewinnbehauptung).
- Strategiewahl: Der Nutzer will in M1 die LLM-Strategie fahren. Die klassischen Strategien dienen dem LLM als
  Referenzsignale und später als Vergleich.

---

## Verlauf
- 2026-11-06: Die Fixtures speichern keine Kerzen, nur Szenario-Parameter (Seed, Drift, Volatilität, Sonderfall) und die erwarteten Ausgaben. `tests/golden_scenarios.py` erzeugt die Kerzen deterministisch (`random.Random(seed)`) und wird von Generator und Tests gemeinsam benutzt. 20 Grundszenarien plus 21 gezielt gesuchte für Zweige, die das Raster nicht trifft; 41 Szenarien, alle geforderten Zweige erreicht (`meta.branches_missing == []`), Datei rund 400 KB.
- 2026-11-06: Generator zweimal ausgeführt, identischer SHA-256 (AK6).
- 2026-11-06: Alle fünf Strategien und `feature_payload` stimmen auf Anhieb mit der Arena überein (41 Szenarien × 3 Positionsanteile).
- 2026-11-06 (23:51 UTC am 05.11.): `uv run trading-bot signals` live → Exit 0. BTC-EUR: Regime TREND, ADX 41,7, Trend SELL, Breakout/Mean Reversion/Ensemble HOLD (Score −0,60), Buy & Hold BUY; ETH-EUR: Regime TREND, ADX 42,4, alle HOLD außer Buy & Hold. Roundtrip-Kosten 0,60 %. Umlaute waren unter Windows zunächst kaputt (cp1252) → stdout auf UTF-8, Learning ergänzt.
- 2026-11-06 (Prüfer, Blocker): Die Fixtures bewiesen die Gleichheit nicht. Ein Mutationstest ließ 23 von 44 Änderungen an Schwellen/Gewichten unentdeckt (ADX-Werte der Szenarien nur in Clustern 13,9 / 22 / 36–37 / 75–100). Behoben: (1) **Grenzfälle** – `sample_indicators` in `tests/golden_scenarios.py` setzt je Fall eine Bedingung knapp unter/auf/über ihre Schwelle, alle anderen klar erfüllt; 6 Basen × 800 Seeds, Gewichte 0,02 / 0,0200001 / 0,45; die Arena rechnet die Erwartung, gespeichert wird nur SHA-256 der exakten Ausgabe. (2) Szenarien mit ADX in [17,18), [18,18,6), [21,4,22), [22,22,6) für die Regime-Schwellen. (3) Dip mit 1,9σ/2,1σ, Minuten mit Volumen 0, ETH-Szenario, Payload-Gewicht 0,123456789. (4) LLM-JSON wird als exakter String im Arena-Format verglichen (Reihenfolge, `0` vs `0.0`). (5) Generator nutzt jetzt `arena.Quote` und `engine03.cost_roundtrip` statt nachgebauter Formeln. Ergebnis `tools/golden/mutation_check.py`: 44/44 echte Mutationen erkannt; überlebt nur die No-op-Kontrolle und Ensemble 0,55→0,6, die nachweislich äquivalent ist (Score in [0,55; 0,6) nur bei TRANSITION mit Breakout- und Mean-Reversion-BUY, das ADX ≥ 19 und < 18 zugleich verlangt). Fixtures jetzt ~740 KB, 49 Szenarien.
- 2026-11-06 (Prüfer, Hinweise): CI prüft jetzt `mypy src tests tools`; Kommentar zu `market` = Enum-Name; `DEFAULT_MIN_BARS` gegen Arena-`MIN_BARS` getestet (damit ist der offene Punkt aus Spec 001 erledigt: die Strategie behält die Arena-Minima). Bewusste, harmlose Abweichung: Fehlertexte bei Programmierfehlern (`unknown strategy`, `EMA: no data`) sind englisch – sie sind keine Signal-Ausgabe.
- 2026-11-06 (Prüfer, 2. Runde: Blocker im Kern geschlossen, zwei Lücken): (1) alle Szenarien hatten Spread 4,0 bp → `round(spread_bps, 2)` ungeprüft; jetzt Szenarien mit 1,3412 und 2,6789 bp. (2) Fehler im Sampler: der Knopf `adx` des Trend-Modus fiel in den Abwärtstrend-Zweig, ADX = 20 kam nie mit Aufwärtstrend vor → eigener Zweig. Dazu: Dips mit effektiver Tiefe knapp unter/über 2σ (gesucht, da der Dip-Balken selbst in `prior` liegt) als zusätzliche Grenzfall-Basen; Szenarien mit RSI = 100 (int) und Streuung 0 (z20 = 0 int); `assert_same` unterscheidet jetzt int und float. 55 Szenarien, 8 Basen × 800 = 6400 Grenzfälle, 446 Tests. Die neuen Mutationen (`a >= 20`→`> 20`, 2σ→2,05σ, Spread-Rundung, RSI/z20 int→float) stehen in `mutation_check.py`. Unabhängig bestätigt: Ensemble 0,55→0,6 ist äquivalent.
- 2026-11-06: Kostenwerte der Arena (`ARENA_TAKER_FEE` 0,25 %, `ARENA_SLIPPAGE` 0,05 %) als Konstanten in `payload.py`, vorerst nur für die Diagnose; die Werte des Bots legt Nr. 3 fest.

## Ergebnis
- **Erreicht:** …
- **Abweichungen vom Plan:** …
- **Nachweise:** …
- **Offen / Folgearbeit:** …
- **Neue ADRs / Learnings:** …
