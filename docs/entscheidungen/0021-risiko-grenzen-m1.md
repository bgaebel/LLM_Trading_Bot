# ADR-0021: Risiko in M1 – Drawdown-Stopp wie die Arena, Freigabe und Handelspause über die Weboberfläche, Kurse je Markt

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-07
- **Bereich:** #risiko #ausfuehrung #web #betrieb

## Kontext
Roadmap Nr. 4 verlangt Risiko-Grenzen und Fail-closed (veraltete Kurse, Feed-/LLM-Ausfall, BTC/ETH entkoppelt).
Bestand (Spec 004): Gewichtsgrenzen 45 % je Markt und 90 % gesamt, Mindestorder max(50 €, 2 % der Equity), Spread
≤ 50 bp, Kurse ≤ 20 s, `portfolios.halted` (gelesen, nie gesetzt) und `portfolios.peak` (Allzeithoch der Equity).
Die Arena (`mark_and_risk`) stoppt ein Depot, wenn `not halted and peak > 0 and eq < peak*(1-0.10)` (außer Buy &
Hold), verkauft dann alle Bestände zu `bid*(1-Slippage)` mit Gebühr auch unter der Mindestorder (`RISK_SELL`) und
gibt es nie wieder frei; sie kennt keine Tagesverlustgrenze, keinen Stop-Loss je Position, keinen Cooldown und
keinen Not-Aus, und sie koppelt die Kurse beider Märkte – entgegen der Projektregel „BTC-Fehler blockiert ETH
nicht“. ADR-0007 verlangt Arena-Gleichheit; ADR-0017 ließ den schnellen Risiko-Worker für Nr. 4 offen; eine
Schemaänderung kostet Migration und Deploy-Rollback (ADR-0020). Die Weboberfläche liest die DB nur (ADR-0016) und
schreibt nur `secrets.env` (ADR-0015), die der Dienst jede Runde liest (ADR-0017). Der Nutzer ist Trading-Laie und
hat die Festlegung der Grenzen übertragen (2026-11-07); er erreicht die Oberfläche per SSH-Tunnel, Befehle auf dem
Server sind für ihn umständlich.

## Entscheidung
- **Drawdown-Stopp als Port der Arena:** genau die Arena-Bedingung (`DRAWDOWN_KILL = 0.10` vom Allzeithoch, Bewertung
  zum Mittelkurs), Zwangsverkauf jedes Marktes mit Bestand > 0 als `RISK_SELL` ohne Entscheidung, danach Equity-Punkt.
  Abweichung: Statt einer Zeile in einer Log-Tabelle (gibt es hier nicht) loggt der Dienst einmal eine WARNING mit
  Equity vor dem Verkauf und Höchststand. Geprüft bei jeder Bewertung (jede Runde, nach jedem Trade), nur mit
  frischen Kursen.
- **Gestoppt ist kein Fehler:** Ein gestopptes Depot trifft keine Entscheidungen, fragt GPT nicht und zählt nicht als
  Rundenfehler (sonst wäre der Dienst dauerhaft `degraded` und jedes Deploy würde zurückgerollt).
- **Wiederfreigabe über die Weboberfläche:** Neben einem gestoppten Depot steht „Wieder freigeben“; das schreibt eine
  Freigabe-Anforderung mit Zeitpunkt in `secrets.env`. Der Dienst (einziger Schreiber) setzt in der nächsten Runde
  `halted = 0` und `peak` auf das Bargeld des Depots (nach dem Stopp hat es keine Bestände), wenn die Anforderung
  jünger als der letzte Stopp und höchstens 24 h alt ist. Nie automatisch ohne Anforderung.
- **Handelspause (Not-Aus) über die Weboberfläche:** Schalter „Handel pausieren“ → `BOT_PAUSED` in `secrets.env`,
  jede Runde gelesen und **fail-closed**: unlesbare Datei oder ungültiger Wert heißt Pause (mit Warnung). Während der
  Pause keine Trades außer `RISK_SELL`, keine GPT-Anfragen, kein Buy-&-Hold-Kauf; Bewertung läuft weiter. Der Dienst
  meldet den angewandten Zustand in der Statusdatei, die Übersicht zeigt ihn.
- **Kurse je Markt:** Ein ungültiger Kurs verwirft nur seinen Markt. Ein Markt ohne frischen Kurs bekommt keine
  Entscheidung (die Kerze bleibt offen, wie Spec 004); hat das Depot dort Bestand (> 0), entscheidet es auch im
  anderen Markt nicht. GPT fragt nur bei frischen Kursen aller Märkte der Anfrage und mit Bestand; fehlt nach der
  Antwort ein Kurs, ist nur dieser Markt `STALE_QUOTE`. Die Arena-Pfade (`record_decision` mit beiden Kursen,
  Buy & Hold nur mit beiden Kursen) bleiben bitgleich.
- **Unverändert:** Gewichtsgrenzen, Mindestorder, Spread, Kursalter, Takt 60 s und ein Prozess (ADR-0017), GPT-Regeln
  und Budget (ADR-0013), Strategien (ADR-0007). `secrets.env` bekommt zwei Felder (`BOT_PAUSED`, Freigaben) –
  Erweiterung der Feldliste aus ADR-0015.

## Verworfene Alternativen
- **Tagesverlustgrenze, Stop-Loss je Position, Trade-Cooldown:** nicht in der Arena; würden die Ergebnisse gegenüber
  Arena v0.3.1 verschieben, ohne dass Geld auf dem Spiel steht. Kandidaten für M2.
- **Strengerer Stopp (z. B. 5 %) oder Bezug auf einen Tageshöchststand:** stoppt bei normalen Krypto-Schwankungen;
  bricht die Vergleichbarkeit.
- **Schneller Kurs-/Risiko-Worker (Arena: 5 s):** zweiter Thread auf derselben Verbindung, mehr Abrufe gegen das
  Bitvavo-Limit; bei Paper-Geld nicht nötig. Neu bewerten mit M2.
- **Stopp ohne Zwangsverkauf (Abweichung von der Arena):** ließe das Risiko offen.
- **Wiederfreigabe per Befehl auf dem Server:** braucht SSH, gestoppten Dienst und Kurse; für den Nutzer zu
  umständlich. **Automatische Freigabe (z. B. am nächsten Tag):** ein gescheitertes Depot handelte unbemerkt weiter.
- **Schreibzugriff der Weboberfläche auf die DB:** widerspricht ADR-0016.
- **Handelspause, die bei ungültiger Einstellung die letzte gültige Konfiguration behält:** ein Not-Aus, der still
  weiterhandeln lässt.
- **Neue Spalten (Grund, Zeitpunkt des Stopps):** Schemaänderung; Zeitpunkt = letzter `RISK_SELL`, Grund im Journal.

## Konsequenzen
- Positiv: Verlust je Depot begrenzt; Not-Aus und Wiederfreigabe mit einem Klick; ein Kursproblem eines Marktes
  blockiert den anderen nicht mehr.
- Negativ / Preis: Der realisierte Verlust beim Stopp liegt über 10 % (Auslösung zum Mittel-, Verkauf zum Geldkurs,
  Prüfung je Minute). Bei ~90 % Investition stoppt ein Rückgang von gut 11 % vom Allzeithoch – das kommt bei ETH
  vor; gestoppte Depots fallen bis zur Freigabe aus dem Vergleich.
- Neu bewerten, wenn: Echtgeld kommt (M2: Tagesverlust, schneller Worker, Freigabe mit Bestätigung), oder Depots
  ständig stoppen.

## Code-Anker
- `paper/execution.py` (`mark`), `paper/cycle.py`, `llm/cycle.py`, `market/bitvavo.py` (`quotes`),
  `web/credentials.py`, `web/pages.py`, `service/run.py`, `service/status.py` – geplant, Spec 011
