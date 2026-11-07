# ADR-0023: Live-Ausführung für M2 – zwei Phasen, nie blind wiederholen, getrennte Historie

- **Status:** vorgeschlagen
- **Löst ab:** –
- **Datum:** 2026-11-07
- **Bereich:** #echtgeld #ausfuehrung #sicherheit #bitvavo

> Bleibt „vorgeschlagen“ bis zur Planung von M2 und wird dort angenommen, geändert oder verworfen (Spec 012,
> Abgrenzung). Bis dahin gilt ADR-0004 unverändert.

## Kontext
M2 soll echte Orders über die Bitvavo-Trading-API mit Kleinstbetrag, harten Limits, Not-Aus und Abgleich mit dem
Kontostand bringen (ROADMAP). ADR-0022 legt nur die Naht fest. Grundlage hier ist die Recherche
`docs/recherche/bitvavo-trading-api-2026-11.md` (Stand 2026-11-07; nur öffentliche Endpunkte abgerufen):
- `clientOrderId` (UUID) ist nur unter **offenen** Orders eindeutig und nach Abschluss wiederverwendbar – **kein
  Idempotenzschlüssel**; bei 503/Code 109 ist der Ausgang laut Bitvavo ungewiss („get the order before creating it
  again“). Quellen: docs.bitvavo.com/releases/v2.6.0, docs.bitvavo.com/docs/errors
- `operatorId` (Ganzzahl) ist seit v2.9.0 Pflicht bei Anlegen und Stornieren. Quellen: docs.bitvavo.com/releases/v2.9.0,
  docs.bitvavo.com/docs/faqs
- Limitpreise müssen ein Vielfaches von `tickSize` sein (seit v2.12.0); Mengen höchstens `quantityDecimals` bzw.
  `notionalDecimals` Nachkommastellen; `amountQuote` gibt es nur für Market-, Stop-Loss- und Take-Profit-Orders,
  eine Limit-Order braucht `amount` und `price`. Quellen: docs.bitvavo.com/releases/v2.12.0,
  docs.bitvavo.com/docs/rest-api/create-order
- Mindestorder laut `GET /markets` am 2026-11-07: `minOrderInQuoteAsset` 5,00 €, `minOrderInBaseAsset` 0,00007294 BTC
  bzw. 0,00242890 ETH. Vermutet, nicht dokumentiert: `minOrderInBaseAsset` folgt dem Kurs – deshalb vor jeder Order
  frisch lesen. Quelle: GET /v2/markets (öffentlich)
- Preisschutz ist immer aktiv; Market-Orders können teilweise ausgeführt und der Rest storniert werden. Endstatus
  sind nur `filled`, `expired`, `canceled` (Grund in `restatementReason`); eine Ablehnung (HTTP 400) erzeugt keine
  Order. Quellen: docs.bitvavo.com/docs/order-lifecycle, docs.bitvavo.com/releases/v2.10.0
- Gebühr und Gebührenwährung stehen erst mit `settled = true` am Fill; Fill- und Order-Ereignisse kommen nicht
  garantiert in Reihenfolge. Quelle: docs.bitvavo.com/docs/faqs
- Orders und Trades zu lesen braucht das Recht „Trade“; „Withdraw“ erlaubt Auszahlungen ohne 2FA; IP-Whitelist ist
  möglich, nicht Pflicht. Quelle: docs.bitvavo.com/docs/get-started
- `GET /trades` erlaubt höchstens 24 h Spanne je Abfrage; eine IOC-Order ohne Ausführung hinterlässt keinen Trade,
  nur `GET /orders` (mit Zeitfenster) zeigt sie. Quelle: OpenAPI exchange-rest-api.yaml
- Rate Limit 1000 Punkte/Minute, gemeinsam für alle Endpunkte und WebSocket; Überschreitung → HTTP 429/Code 105 und
  Sperre des Keys bis zum Ende der folgenden Minute (Doku-Beispiel 12:00:15 → 12:02:00). Quelle:
  docs.bitvavo.com/docs/rate-limits
- Es gibt keine Sandbox. Quelle: docs.bitvavo.com/llms-full.txt

## Entscheidung (Vorschlag)
- **Zwei Phasen, nie in einer Transaktion senden:** Vorgang mit `client_order_id` (frische UUID4) und
  `operator_id` als PLANNED committen → genau ein `POST /order` in `dispatch` außerhalb jeder Transaktion → Ergebnis
  in neuer Transaktion buchen. Beim Start gilt jeder PLANNED-Vorgang als UNKNOWN.
- **Nie blind wiederholen:** Zeitüberschreitung, 5xx, Code 109 oder HTTP 429 auf `POST /order` → UNKNOWN; der
  Markt des Depots ist gesperrt, bis ein gefundenes Order-Objekt mit Endstatus den Ausgang klärt (`GET /order`
  mit `clientOrderId`, sonst `GET /orders` im Zeitfenster um den Versand, dazu `GET /trades`). „Nicht gefunden“
  bleibt UNKNOWN und verlangt manuelle Klärung. Ablehnungen werden nicht wiederholt. Vor
  `bitvavo-ratelimit-resetat` keine weitere private Anfrage.
- **Gebucht wird nur, was ausgeführt wurde:** nach den Fills (Teilausführung wird nicht nachgezogen); erst wenn
  alle Fills `settled` sind, bis dahin bleibt der Markt gesperrt wie bei UNKNOWN; Gebühren aus `fills[].fee` in
  `feeCurrency` (nicht EUR → zum Fill-Preis umgerechnet und markiert); `fill_id` eindeutig gegen Doppelbuchung.
- **Zustand nach ausgeführter Menge:** alle Fills = FILLED, teilweise = PARTIAL, keine Fills und
  `expired`/`canceled` = EXPIRED, Ablehnung ohne Order = REJECTED; Bitvavo-Status und `restatementReason` werden
  unverändert gespeichert.
- **Getrennte Historie:** eigene Live-DB (eigenes Schema, `user_version` ≥ 100), nie Migration der Paper-DB (v2);
  Paper- und Live-Kurven werden nie gemischt; die Weboberfläche zeigt Live nur lesend und deutlich markiert.
- **Modus nur beim Prozessstart**, nie über Weboberfläche oder `secrets.env`.
- **Key:** nur View + Trade, nie Withdraw, IP-Whitelist auf den Server; Key und IP nie ins Repo.
- **Ein Live-Depot je Bitvavo-Konto bzw. Unterkonto:** `/balance` ist kontoweit; mehrere Depots auf einem Konto
  machen Saldenabgleich und Self-Trade-Prevention unbeherrschbar.
- **Abgleich vor jeder Runde** gegen `GET /balance`; nicht abgerechnete Fills zählen nicht als Abweichung; eine
  Abweichung über einer kleinen Toleranz → keine Order, auch kein `RISK_SELL`, Warnung, manuelle Klärung.
- **`operatorId`:** eine feste Ganzzahl je Live-Depot, gespeichert in der Live-DB, nie geändert.
- **Eigener schlanker signierender Client** nach dem Doku-Beispiel, kein SDK.

## Offen für M2
- **Entscheidung des Nutzers:** Startkapital; Obergrenze der Live-Equity (hängt daran); ein eigenes Bitvavo-Konto
  bzw. Unterkonto nur für den Bot (sonst stimmt der Saldenabgleich nicht); welche Depots live handeln (naheliegend
  nur gpt-luna, ADR-0012).
- **Ordertyp:** IOC-Limit mit Preisgrenze `ask·(1+s)` bzw. `bid·(1−s)` auf `tickSize` (Schlupf gedeckelt, keine
  liegende Order) gegen Market mit `amountQuote` (einfach, Schlupf nur durch Bitvavos Preisschutz begrenzt);
  Schlupfgrenze `s` begründet festlegen (Bezug: Paper-Slippage).
- **Harte Obergrenzen** als Code-Konstanten, per Konfiguration nur senkbar. Erste Vorschläge zum Prüfen: Order
  ≤ 120 €, ≤ 10 Orders/Tag, Tagesverlust 3 %, dazu der Drawdown-Stopp 10 %. Achtung: Eine Order-Obergrenze unter der
  Arena-Ordergröße beschneidet Entscheidungen; die nächste Runde sähe wieder eine Differenz über `MIN_TRADE_EUR` und
  ordert erneut (frisst das Tageslimit, weicht vom Paper-Verlauf ab). Grenzen und Startkapital so wählen, dass das
  nicht passiert, oder das Nachziehen bewusst regeln.
- **Not-Aus live:** Soll die Pause (ADR-0021) live auch `RISK_SELL` blockieren? Vorschlag: ja, Not-Aus = kein
  Versand; das braucht ein ADR, das ADR-0021 für live ergänzt.
- Ein Prozess (ADR-0017) oder eigener Live-Dienst – eine Abweichung von ADR-0017 braucht ein eigenes ADR.
- Reste unter der Mindestorder nach einem Verkauf sind live unverkäuflich; `release_problem` verlangt heute
  Bestand 0; `MIN_TRADE_EUR = 50` und die 2-%-Schwelle setzen ein Mindest-Startkapital voraus.
- Plan für die ersten Echtgeld-Orders ohne Sandbox (Mindestbetrag laut `/markets`).
- **Mit echtem Key prüfen:** Gehört der Query-String in die Signatur? Funktionieren `GET`/`DELETE /order` nur mit
  `clientOrderId` (OpenAPI verlangt `orderId`)? Reicht `GET /orders` zum Auffinden über `clientOrderId`? Antwort bei
  `responseRequired=false`? Gebührenwährung und Volumenstufen? Welche Rechte braucht `/balance`?

## Verworfene Alternativen
- **Offizielles Python-SDK:** zieht Abhängigkeiten nach und ist nicht auf dem neuesten API-Stand; ein schlanker
  signierender Client ist überschaubar und prüfbar.
- **Live-Daten in der Paper-DB:** würde die Paper-Historie verändern (CLAUDE.md, ADR-0012).
- **`clientOrderId` als Idempotenzschutz beim Wiederholen:** von Bitvavo nicht gedeckt (siehe Kontext).

## Konsequenzen
- Positiv: M2 startet mit belegten Annahmen; die gefährlichen Fälle (Doppelorder, unklarer Zustand, nicht
  abgerechnete Gebühr, Restmengen) sind vorab benannt.
- Negativ / Preis: mehr Zustände und eine zweite DB; ohne Sandbox bleibt ein Restrisiko bei den ersten Orders.
- Neu bewerten, wenn: M2 geplant wird oder Bitvavo die Trading-API ändert.

## Code-Anker
- `src/llm_trading_bot/broker/contract.py`, `intents.py` (Typen und Zustandsmaschine aus ADR-0022) – geplant, Spec 012
