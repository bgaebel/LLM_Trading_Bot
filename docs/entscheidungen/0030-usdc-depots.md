# ADR-0030: USDC-Depots – Signale aus BTC/EUR, BTC-Ausführung über BTC/USDC, Geld in Euro mit Umrechnung je Trade

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-08
- **Bereich:** #strategie #markt #kosten

## Kontext
Recherche 2026-11-08 (Bitvavo-Gebührenseite, öffentliche API): BTC/USDC kostet 0,05 % je Seite (Kategorie B),
BTC/EUR 0,25 % taker (Kategorie A), USDC/EUR 0,10 % (Kategorie C). BTC/USDC hat nur ~3 % des Volumens von BTC/EUR,
in ~83 % der Minuten keinen Handel und Kerzen erst ab 2024-07-01. Der Nutzer will BTC/USDC mit dessen Gebühren nutzen,
als neue Depots, mit Geld in Euro und Umrechnung je Trade (2026-11-08).

## Entscheidung
- **Neue Paper-Depots `trend-usdc`, `breakout-usdc`, `meanrev-usdc`, `ensemble-usdc`, `buyhold-usdc`** mit Version
  `v0.3.1-usdc` (Ausführungsvariante nach ADR-0029); die v0.3.1-Depots und ihre Historie bleiben unverändert.
- **Signale aus den BTC/EUR- und ETH/EUR-Kerzen** wie v0.3.1 (liquide, Historie seit 2021); BTC/USDC-Kerzen werden
  nicht verwendet.
- **Ausführung von BTC über zwei Bücher:** Kauf € → USDC (USDC-EUR-Brief, 0,10 % Gebühr, 0,01 % Slippage) → BTC
  (BTC-USDC-Brief, 0,05 % Gebühr, 0,05 % Slippage), Verkauf umgekehrt zum Geld. ETH bleibt ETH/EUR mit den
  Kosten von v0.3.1.
- **Geld in Euro:** Bargeld, Bewertung, Mindestorder, Drawdown-Stopp in €; BTC bewertet mit BTC-USDC-Mitte ·
  USDC-EUR-Mitte. Kein USDC-Bestand, kein Wechselkursrisiko im Bargeld.
- **Kosten in der Bewertung jedes Trades:** die Strategien dieser Depots rechnen mit dem Kostenweg
  `2 · (0,05 % + 0,05 % + 0,10 % + 0,01 %)` plus beiden Spreads statt mit den EUR-Kosten.
- **Kurse aus derselben Abfrage `/ticker/book`**; fehlt BTC-USDC oder USDC-EUR, entscheidet und bewertet ein
  USDC-Depot mit BTC-Bezug nicht (fail closed, ADR-0021). Schema `bot.db` bleibt v2.

## Verworfene Alternativen
- **Signale aus BTC/USDC-Kerzen:** zu dünn, die Lückenregeln sperren fast jede Runde.
- **Bargeld dauerhaft in USDC:** günstiger (~0,15 % statt ~0,37 % je Kauf + Verkauf), aber Wechselkursrisiko bis ±10 %
  je Quartal, das allein den Drawdown-Stopp auslösen und den Strategievergleich verzerren kann.
- **Bestehende Depots umstellen / nur Gebühren senken:** mischt Historie bzw. wäre eine falsche Kostenannahme.
- **ETH über ETH/USDC:** noch dünner; der Nutzer nannte nur BTC.

## Konsequenzen
- Positiv: Der Paper-Vergleich zeigt direkt, was die günstigere Ausführung bringt; gleiche Signale, andere Kosten.
- Negativ / Preis: zwei zusätzliche Kursmärkte als Abhängigkeit; mehr Depots in der Übersicht; Gebührenwährung auf
  BTC/USDC nicht bestätigt (Modell in €).
- Neu bewerten, wenn: Echtgeld kommt (M2: echte Gebühren aus `/account/fees`), BTC/USDC deutlich liquider wird, oder
  Bitvavo die Gebühren ändert.

## Code-Anker
- geplant: `market/models.py`, `market/bitvavo.py`, `paper/execution.py`, `paper/cycle.py`, `strategy/payload.py`,
  `llm/agents.py`, `web/overview*.py`, `lab/backtest.py` – Spec 020
