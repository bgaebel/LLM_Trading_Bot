# ADR-0031: Wie ADR-0012, aber das Euro-Buy-&-Hold-Depot läuft nicht mehr – Referenz ist Buy & Hold USDC

- **Status:** angenommen
- **Löst ab:** [ADR-0012](0012-luna-haupt-sol-gegenprobe.md)
- **Datum:** 2026-11-08
- **Bereich:** #paper #web #referenz

## Kontext
ADR-0012 legt die Standard-Depots fest: `gpt-luna` und `gpt-sol` „plus Buy & Hold“. Seit Spec 020 (ADR-0030) läuft
zusätzlich `buyhold-usdc`, das BTC über BTC-USDC kauft und in Euro bewertet wird. Der Nutzer braucht das
Euro-Depot `buyhold` nicht mehr und hat am 2026-11-08 entschieden, es ganz abzuschalten (nicht nur auszublenden).
Er wurde darauf hingewiesen, dass die Euro-Depots damit keine genau passende Referenz mehr haben (ihr BTC läuft
über BTC-EUR mit höheren Kosten), dass das ein neues ADR braucht und dass ein Deploy die M1-Uhr neu startet.

## Entscheidung
- **Wie ADR-0012** (Depots, Modelle, Anteile, Schema), mit einer Ausnahme: **`buyhold` läuft im Live-Bot nicht
  mehr** – keine Entscheidung, kein Trade, keine Bewertung, keine Drawdown-Prüfung, kein Anlegen auf einer neuen DB.
- **Historie bleibt:** Die Zeilen von `buyhold` in `bot.db` (Depot, Entscheidungen, Trades, Equity) werden nie
  geändert oder gelöscht; Schema bleibt v2.
- **Code bleibt schaltbar** für Labor und Rückweg: `BOT_BUYHOLD` in der Umgebung, fehlt = aus, genau `1` = an,
  genau `0` = aus, sonst Konfigurationsfehler. Wieder an (oder Rollback auf ein Release davor) bewertet das Depot
  einfach weiter, ohne erneut zu kaufen.
- **Referenz ist `buyhold-usdc`:** „vs.“ = Rendite des Depots seit dem ersten Equity-Punkt der Referenz minus
  Rendite der Referenz seit demselben Punkt, in Prozentpunkten. Ist `buyhold-usdc` nicht aktiv, ist `buyhold` die
  Referenz, falls aktiv, sonst gibt es keine.
- **Übersicht zeigt nur aktive Depots:** aktiv = neuester Equity-Punkt höchstens 1 h älter als der neueste der DB
  (vor der ersten Runde alle). Die Regel braucht nur die DB (ADR-0016).

## Verworfene Alternativen
- **Nur ausblenden, weiter betreiben:** vom Nutzer verworfen („Ganz abschalten“).
- **Zeilen löschen oder umbenennen:** verfälscht die Handelshistorie (CLAUDE.md, Kernwert in PROJEKT.md).
- **Code entfernen:** das Labor (Spec 016) betreibt den Live-Zyklus mit v0.3.1 plus Buy & Hold; ein Rückweg ohne
  Codeänderung bleibt so möglich.
- **Sichtbar, solange in der neuesten Runde bewertet:** ein Depot mit Bestand in einem Markt ohne frischen Kurs
  verschwände genau dann, wenn etwas nicht stimmt.
- **„vs.“ weiter ab Startkapital:** `buyhold-usdc` begann später als die übrigen Depots; der Vergleich mischte dann
  unterschiedliche Zeiträume.

## Konsequenzen
- Positiv: eine Referenz statt zwei; die Übersicht wird kürzer; die Historie bleibt vollständig.
- Negativ / Preis: v0.3.1- und GPT-Depots werden gegen eine Referenz mit günstigerer BTC-Ausführung gemessen;
  Depots, die jünger als die Referenz sind, zeigen „–“; der Deploy startet die M1-Uhr neu.
- Neu bewerten, wenn: die USDC-Depots dauerhaft abgeschaltet werden oder M2 (Echtgeld) eine Referenz in der
  echten Ausführung braucht.

## Code-Anker
- `src/llm_trading_bot/llm/agents.py` (`BUYHOLD_FIELD`, `BotConfig.benchmarks`), `src/llm_trading_bot/paper/cycle.py`
  (Erstkauf), `src/llm_trading_bot/web/overview.py` (`ACTIVE_SECONDS`, `reference`) – Spec 021
