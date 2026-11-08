# Strategie-Labor

Das Labor prüft auf dem PC, was die klassischen Strategien v0.3.1 und Buy & Hold über die Bitvavo-Historie gebracht
hätten. Es rechnet mit genau dem Code, der live läuft: Der unveränderte `paper_cycle` bekommt statt der Börse einen
historischen Kursgeber (ADR-0026, ADR-0029, Spec 016). Das Labor läuft nur auf dem PC, nie auf dem Server, und
handelt nie (ADR-0004).

## Ordner

Alle Labordaten liegen außerhalb von Repo und Dropbox:

| System | Ordner |
|---|---|
| Windows | `%LOCALAPPDATA%\llm-trading-bot-lab` |
| Linux/macOS | `$XDG_DATA_HOME/llm-trading-bot-lab`, sonst `~/.local/share/llm-trading-bot-lab` |
| anderer Ort | Umgebungsvariable `LTB_LAB_DIR` oder `--dir <ordner>` bei jedem Befehl |

| Datei | Inhalt |
|---|---|
| `candles.db` | Kerzenarchiv (1m, 15m, 1h, 4h; BTC-EUR, ETH-EUR) und Spread-Stichproben |
| `runs.db` | Prüfregister: jeder Backtest-Lauf mit Ergebnis, Code-Stand und Prüfsummen |
| `register.jsonl` | fortlaufende Kette mit Prüfsummen zum Register (liegt jedem Bericht bei) |
| `reports/<lauf>/` | Berichte: `report.md`, `report.html`, Kopie der Kette, ggf. `fidelity.json` |

## Befehle in dieser Reihenfolge

Alle Befehle mit `uv run trading-bot lab <befehl>`; `--help` zeigt die Optionen. Ausgaben sind deutsch.

1. **`fetch`** – lädt öffentliche Kerzen ohne Schlüssel (Standard ab 2020-07-01 bis jetzt). Fortsetzbar:
   Ein abgebrochener Lauf macht beim nächsten Aufruf weiter, fertige Fenster werden nie überschrieben. Bei
   Rate-Limit bremst der Abruf, bei 429 hört er auf und nennt die Uhrzeit, ab der es weitergeht.
   `--start`, `--end`, `--max-requests` begrenzen einen Lauf.
2. **`check`** – vergleicht ohne Netz die 1m-Kerzen mit 15m/1h/4h (Summen gleich?) und berichtet Abweichungen.
3. **`spread`** – schätzt den Spread (Abstand Kauf-/Verkaufskurs) je Quartal aus Stichproben öffentlicher Trades
   (zwei zufällige Stunden je Woche, fortsetzbar). Ohne Stichprobe gilt ein vorsichtiger Standardwert.
4. **`backtest`** – rechnet je Quartal (Standard 2021Q2 bis 2025Q3) und trägt jeden Lauf ins Prüfregister ein.
   `--variant` wählt, was läuft:
   - `base`: Live-Kosten (0,25 % Gebühr + 0,05 % Slippage je Seite) – der Basislauf;
   - `cost-grid`: dieselben Strategien mit 0,3 / 0,9 / 1,2 % je Round-Trip (0,6 % ist `base`);
   - `diagnostic`: Lücken-Toleranz in höheren Intervallen (zeigt, was die Sperrzeiten kosten; keine Strategie);
   - `usdc`: die USDC-Depots, BTC über USDC (Abschnitt unten);
   - `all`: alles davon.
   `--workers` setzt, wie viele Quartale gleichzeitig rechnen (Standard: Prozessorkerne minus 1).
5. **`fidelity`** – Treue-Test: baut aufgezeichnete Live-Entscheidungen aus dem Archiv nach und vergleicht sie.
   `--bot <kopie von bot.db>` und, wenn vorhanden, `--obs <kopie von observations.db>`. Nur Kopien, nie die
   Dateien des laufenden Dienstes; eine Kopie vom Server nur mit Zustimmung. Ergebnis als
   `reports/fidelity-<zeit>.json`.
6. **`report`** – schreibt den Basisbericht aus dem Register, mit `--fidelity <json>` samt Treue-Ergebnis. Jeder
   Bericht bekommt einen eigenen Ordner; nichts wird überschrieben.

## So liest man den Bericht

- **Kurzfazit** zuerst: wie viele Quartale gewertet sind und je Strategie die Übertrendite gegenüber Buy & Hold mit
  gleicher Investitionsquote, mit Band.
- **Eckige Klammern** sind das 95-%-Band: der Bereich, in dem das Ergebnis bei anderen, ebenso plausiblen
  Marktverläufen liegen könnte. „Mit Zufall vereinbar: ja“ heißt, das Band enthält null – das Ergebnis kann Glück
  oder Pech sein.
- **Einstufung** nach Round-Trips (ein Kauf und der zugehörige Verkauf): unter 100 „kein Urteil“, 100 bis 300
  „indikativ“, darüber „belastbar“. Ein schönes Band mit wenigen Trades zählt nicht.
- **Quartale:** gewertet wird ein Quartal erst, wenn je Markt mindestens 90 % der fälligen Entscheidungen fielen
  (Datenlücken sperren den Bot wie live); die anderen stehen mit Grund da.
- **Kosten:** Gleichstand bei festen Entscheidungen (bei welchen Kosten wäre das Ergebnis null?) und das
  Kostenraster als echte Neuläufe.
- **Ausführung über USDC:** die USDC-Depots neben ihren v0.3.1-Zwillingen, nur über Quartale, die in beiden Läufen
  gewertet sind.
- **Datenqualität, Diagnose, Treue, Annahmen, Zahl der Versuche, Laufzeit** – die Zahl der Versuche ist wichtig:
  Wer viele Varianten prüft, findet zufällig eine gute.

## Treue-Test: so liest man das Ergebnis

Der Treue-Test prüft den Code, nicht die Rendite: Bekommt das Labor aus denselben Kerzen dieselbe Entscheidung wie
live? Die Regel (AK5, vom Nutzer am 2026-11-08 freigegeben):

- **Aktion und Ziel** müssen in allen Vergleichen gleich sein – auch dort, wo der Eingang verschieden war.
- **Indikatoren** müssen nur dort exakt gleich sein, wo die Aufzeichnung (`observations.db`, Spec 015) belegt, dass
  live dieselben Kerzen hatte wie das Archiv (gleiche Prüfsumme der Fenster).
- **Jede andere Abweichung** braucht eine belegte Ursache. Eine Abweichung bei belegt gleichem Eingang bleibt
  „ungeklärt“ und lässt den Test scheitern – ein Fehler im Code wird nie wegerklärt.
- **„Treue belegt“** erst ab 300 exakt gleichen Snapshots mit belegt gleichem Eingang je Markt. Darunter steht
  „Treue nicht belegt“ mit Grund, z. B. „zu wenige exakt gleiche Snapshots: 55 < 300“ – nie „100 %“.

Je Markt zeigen Konsole und Bericht: Vergleiche, Aktion gleich, exakt gleich bei belegt gleichem Eingang (davon mit
gekürztem Live-Fenster), Eingang verschieden (nach Ursache), ohne Aufzeichnung nicht prüfbar und das Ergebnis mit
Grund. „Indikatoren gleich“ steht nur noch zur Information da.

Zwei Eigenheiten von Bitvavo machen den Eingang oft verschieden (`docs/LEARNINGS.md`, „Bitvavo-Kerzen sind kurz nach
Schluss vorläufig“):

1. **Kerze live vorläufig (späte Trades):** Die letzte geschlossene Kerze, 5 s nach Schluss abgefragt, hatte live
   weniger Volumen als später im Archiv – späte Trades werden nachgetragen, nie abgezogen. Mehr Volumen live zählt
   deshalb nicht als späte Kerze, sondern als „Live-Fenster anders als Archiv“.
2. **Gekürztes Live-Fenster:** Seit etwa 2026-11-08 13:00 UTC liefert `/candles?limit=500` bei ETH-1m weniger
   Zeilen. Hat das Archiv im Live-Fenster genau dieselben Zeilen (Anzahl und Prüfsumme), ist der Eingang gleich: Der
   Test baut den Snapshot dann aus dem gekürzten Fenster nach und verlangt exakte Gleichheit.

Ohne aufgezeichnete Runde (ältere Live-Daten, Ausfälle der Aufzeichnung) ist kein Eingang belegt gleich: Weicht
eine Eingangskerze ab, heißt die Ursache „Eingangskerze anders als Archiv (ohne Aufzeichnung)“, sonst sind die
Indikatoren „ohne Aufzeichnung nicht prüfbar“. Solche Snapshots zählen nie als exakt gleich; nur Aktion und Ziel
werden dort streng geprüft.

Der erste Lauf gegen eine Live-Kopie (2026-11-08, 987 Vergleiche) erreichte 55 (BTC-EUR) und 71 (ETH-EUR) exakt
gleiche Snapshots, Aktionen überall gleich, keine Abweichung ohne Ursache. Für 300 je Markt braucht es eine spätere
Kopie mit mehr aufgezeichneten Runden (eine Kopie vom Server nur mit Zustimmung).

## Grenzen

- Kerzen haben keine Kauf- und Verkaufskurse: Kurs = Schluss der letzten Minute vor der Runde, Spread angenommen.
- Ob live die laufende Kerze schon in der Antwort war, weiß das Archiv nicht (Näherung; der Treue-Test erklärt die
  Abweichungen, mit Beobachtungen ist er exakt).
- Live entscheidet auf vorläufigen Kerzen (späte Trades fehlen noch), der Backtest auf endgültigen.
- Tageswerte und Drawdown beruhen auf dem Tagesende; Einbrüche innerhalb eines Tages fehlen darin.
- GPT-Depots fehlen: GPT wird nur live bewertet, nie auf historischen Kursen (ADR-0026).
- Vor 2021Q2 wird nichts gewertet (BTC-EUR hat erst ab 2021-02-20 15m-Kerzen).
- Der Bericht ist eine Auswertung, keine Empfehlung: Auch ein Band über null ist noch kein Beleg.

## Versiegelter Zeitraum ab 2025-10-01

Alles ab **2025-10-01** ist der unberührte Prüfzeitraum für spätere Verbesserungen (Spec 018, ADR-0026). Das Labor
verweigert Backtests, Spread-Stichproben und Berichtszahlen daraus; ein Quartal, das hineinreicht, wird nur als
„verweigert“ ins Register geschrieben, ohne eine einzige Zahl. Wer dort einmal hineinschaut, kann den Zeitraum nie
wieder als unabhängige Prüfung benutzen – deshalb die harte Sperre. Der Treue-Test bewertet keine Renditen und darf
deshalb auch jüngere Live-Daten nachbauen.

## Prüfregister: warum es nie löscht

Jeder Lauf ist ein Versuch (Konfiguration × Quartal × Variante), auch abgebrochene und nicht gewertete.
Wiederholungen mit gleichem Ergebnis werden zusammengefasst. Das Register löscht und überschreibt nie, und die Kette
in `register.jsonl` macht jedes nachträgliche Entfernen sichtbar. Der Grund: Die Zahl der Versuche gehört zu jedem
Ergebnis. Könnte man schlechte Läufe löschen, sähe die beste übrig gebliebene Variante besser aus, als sie ist. Ein
Bericht wird verweigert, wenn die Kette nicht stimmt.

## Variante `usdc`: Ausführung über USDC

Die USDC-Depots (Spec 020, ADR-0029/0030) haben dieselben Regeln wie v0.3.1, kaufen BTC aber über USDC
(Euro → USDC → BTC und zurück, Version `v0.3.1-usdc`); ETH handeln sie wie die Basis auf ETH-EUR.

- Der Lauf rechnet `trend-usdc`, `breakout-usdc`, `meanrev-usdc`, `ensemble-usdc` und `buyhold-usdc` über dasselbe
  Archiv wie `base`. Buy & Hold läuft wie live immer mit und ist mit dem des Basislaufs identisch, auch in den
  Kennzahlen: Es kauft BTC auf BTC-EUR und zahlt deren Kosten und Spread. Die Kosten des Wegs gelten je Depot, nur
  für das BTC der `*-usdc`-Depots.
- Im Register ist `usdc` eine eigene Variante und zählt als eigener Versuch. Die Läufe `base`, Kostenraster und
  Diagnose rechnen weiter genau die v0.3.1-Depots, ausdrücklich ohne USDC-Depots – unabhängig von
  `BOT_USDC_AGENTS`.
- **Annahmen:** BTC-USDC gibt es erst ab 2024-07-01. Beide Kurse des Wegs werden deshalb aus der BTC-EUR-Mitte
  gebildet: USDC-EUR = 1,0 mit 1,1 Basispunkten Spread (gemessen), BTC-USDC = BTC-EUR-Mitte / 1,0 mit
  3 Basispunkten Spread (gemessen 1,5 bis 4,2, Median 2; bewusst vorsichtig). Der Wechselkurs hebt sich in Euro auf;
  es zählen Gebühren (0,05 % BTC-USDC + 0,10 % USDC-EUR je Seite), Slippage und die beiden Spreads.
  Kursabweichungen zwischen BTC-USDC und BTC-EUR fehlen.
- Im Bericht steht die Variante im Abschnitt „Ausführung über USDC“ neben dem Basislauf, nur über dieselben Quartale.
- Der Treue-Test baut auch die Entscheidungen der USDC-Depots nach; für ihre BTC-Entscheidungen braucht er die
  aufgezeichneten BTC-USDC- und USDC-EUR-Kurse aus `observations.db`. Ohne sie werden diese Entscheidungen mit Grund
  als übersprungen aufgeführt, nie stillschweigend weggelassen.
