# ADR-0032: Nur noch USDC-Depots – GPT handelt BTC über USDC, Euro-Depots aus und ihre Historie einmalig gelöscht

- **Status:** angenommen
- **Löst ab:** [ADR-0031](0031-euro-buyhold-abgeschaltet.md)
- **Datum:** 2026-11-09
- **Bereich:** #paper #llm #web #referenz

## Kontext
Seit Spec 020 (ADR-0030) laufen neben den Euro-Depots USDC-Depots, die BTC über BTC-USDC handeln und in Euro
bewertet werden; seit Spec 021 (ADR-0031) ist Buy & Hold USDC die einzige Referenz. Der Nutzer will am 2026-11-09
nur noch die USDC-Paare berücksichtigen: „wir berücksichtigen nur noch USDC-Paare! und alles was vor dem Wechsel
auf USDC war kann im grunde auch weg“. Auf Rückfrage wählte er „GPT auf USDC umstellen“ (gpt-luna und gpt-sol
handeln BTC künftig über BTC/USDC, GPT bekommt die Kosten des Wegs, neue Depots mit 10 000 €, GPT-Kosten bleiben
gleich, Euro-GPT-Depots aus) und „Wirklich löschen“ (alte Depots mit Entscheidungen, Trades und Equity aus `bot.db`
entfernen; nur aus Backups wiederherstellbar). Er wurde darauf hingewiesen, dass der Treue-Test (Spec 016) damit
seine Live-Daten der Euro-Depots verliert und dass das ein neues ADR braucht.

## Entscheidung
- **Im Live-Bot laufen nur die USDC-Depots:** `trend-usdc`, `breakout-usdc`, `meanrev-usdc`, `ensemble-usdc`
  (Auswahl `BOT_USDC_AGENTS`, fehlt = alle), `gpt-luna-usdc`, `gpt-sol-usdc` und `buyhold-usdc` (Referenz, läuft,
  sobald ein USDC-Depot läuft).
- **Der Weg aus ADR-0030 bleibt:** Signale aus den BTC/EUR- und ETH/EUR-Kerzen; BTC über USDC-EUR und BTC-USDC
  mit deren Gebühren und Slippage, Geld in Euro, BTC bewertet mit BTC-USDC-Mitte · USDC-EUR-Mitte; **ETH bleibt
  ETH/EUR** wie in ADR-0030. Nur der Satz aus ADR-0030, die v0.3.1-Depots und ihre Historie blieben unverändert,
  gilt nicht mehr.
- **GPT über USDC:** `gpt-luna-usdc` und `gpt-sol-usdc` haben Modell, Effort, Takt (ADR-0013), Preise und
  Budgetanteile von `gpt-luna` / `gpt-sol` (0,50 / 2,50 € von 3 €, dieselben Umgebungsfelder) und stellen wie
  bisher eine Anfrage je Depot für BTC und ETH. Ihr BTC wird mit den Kosten des Wegs bewertet (dieselben Felder des
  Kontexts: Gebühr 0,15 %, Slippage 0,06 % je Seite, Rundreise wie `paper.usdc.roundtrip_cost`), über den Weg
  gekauft und verkauft und über ihn bewertet. Der Prompt bleibt unverändert (`llm-v1`, gleicher Hash); nur die
  Version der Entscheidungen dieser Depots heißt `llm-v1-usdc`. Fehlt ein Kurs des Wegs, bleibt BTC aus der
  Anfrage (gehaltenes BTC: keine Anfrage), wie bei den klassischen USDC-Depots (fail closed, ADR-0021).
- **Auswahl der GPT-USDC-Depots aus `BOT_AGENTS`:** steht `gpt-luna` bzw. `gpt-sol` in `BOT_AGENTS` (fehlt das Feld:
  beide), läuft `gpt-luna-usdc` bzw. `gpt-sol-usdc`. Die Euro-Namen in `BOT_AGENTS` laufen nicht; das Feld bleibt
  für einen Rückweg auf ein älteres Release lesbar (nur Namen, die Releases vor Spec 022 kennen, nie leer).
- **Euro-Depots aus:** `trend`, `breakout`, `meanrev`, `ensemble`, `gpt-luna`, `gpt-sol`, `buyhold` entscheiden,
  handeln und bewerten nicht mehr und werden auf einer neuen DB nicht angelegt. Rückweg ohne Codeänderung:
  `BOT_EURO_DEPOTS=1` in der Umgebung (Drop-in wie `BOT_BUYHOLD`; fehlt = aus, genau `0`/`1`, sonst
  Konfigurationsfehler) stellt den Depotsatz von vor Spec 022 her (Euro-Depots aus `BOT_AGENTS`, GPT in Euro statt
  über USDC). `BOT_BUYHOLD` (ADR-0031) bleibt als Schalter für das Euro-Buy-&-Hold.
- **Historie der Euro-Depots wird einmal gelöscht:** der Wartungsbefehl `trading-bot purge-euro-depots` löscht
  genau die Zeilen dieser sieben Depots aus `portfolios`, `decisions`, `trades`, `equity` und `llm_calls`, in einer
  Transaktion, nur bei gestopptem Dienst (WriterLock, ADR-0016/0017), nach einem Backup, mit Probelauf und nur mit
  Zustimmung des Nutzers. Zeilen anderer Depots und die Tabelle `market` bleiben bitgleich; Schema bleibt v2.
- **`observations.db` bleibt unberührt** (ADR-0027): die Aufzeichnungen sind Labordaten, keine Handelshistorie.
- **Übersicht:** ein Depot ist aktiv, solange sein neuester Equity-Punkt höchstens 5 min (statt 1 h) hinter der
  neuesten Runde liegt, in der es bewertet werden konnte (Kursregel aus ADR-0031 bleibt); Referenz `buyhold-usdc`.

## Verworfene Alternativen
- **Euro-Depots nur ausblenden oder ruhen lassen (wie ADR-0031):** vom Nutzer verworfen („kann weg“, „Wirklich
  löschen“).
- **GPT-Depots in Euro weiterlaufen lassen:** vom Nutzer verworfen („GPT auf USDC umstellen“).
- **Die alten GPT-Depots umbenennen oder ihre Historie umrechnen:** mischt zwei Ausführungen in einer Historie.
- **GPT-USDC-Depots in `BOT_USDC_AGENTS` wählen:** ein Release vor Spec 022 kennt die Namen nicht und startet dann
  nicht (Rückweg); und auf dem Server steht dort schon eine Auswahl ohne sie – nach dem Deploy liefe GPT gar nicht.
- **Neue Prompt-Version mit USDC-Wortlaut:** unnötig, die Kosten stehen in den vorhandenen Feldern; ein anderer
  Prompt würde den Vergleich mit den bisherigen GPT-Antworten verfälschen.
- **Löschen beim Start des Dienstes oder per Migration:** liefe ohne Backup und ohne Zustimmung; CLAUDE.md verbietet
  stilles Zurücksetzen.
- **`observations.db` mitlöschen:** dort steht, was der Markt und GPT geliefert haben – Labordaten, keine Depots.

## Konsequenzen
- Positiv: eine Ausführungsart, eine Referenz, eine kurze Übersicht; GPT handelt mit den günstigeren Kosten.
- Negativ / Preis: die Historie der Euro-Depots ist nach dem Löschbefehl nur noch in Backups; der Treue-Test
  (Spec 016) verliert seine Live-Daten der Euro-Depots; ein Rückweg auf ein Release vor Spec 022 legt die gelöschten
  Euro-Depots mit 10 000 € neu an; GPT-USDC-Depots beginnen ohne Historie; der Deploy startet die M1-Uhr neu.
- Neu bewerten, wenn: Echtgeld kommt (M2: echte Ausführung, echte Gebühren) oder BTC-USDC unbrauchbar wird.

## Code-Anker
- `src/llm_trading_bot/llm/agents.py` (`EURO_FIELD`, `GPT_USDC`, `load_config`), `src/llm_trading_bot/llm/cycle.py`
  (Routenkosten der GPT-USDC-Depots), `src/llm_trading_bot/ops/purge.py`, `src/llm_trading_bot/web/credentials.py`,
  `src/llm_trading_bot/web/overview.py` (`ACTIVE_SECONDS`) – Spec 022
