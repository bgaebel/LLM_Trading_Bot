<!-- Recherche für Spec 012 / ADR-0023 (2026-11-07). Grundlage für M2, ersetzt nicht das Prüfen mit echtem Key. -->
# Bitvavo REST-Trading-API: Faktenlage für den Entwurf der Echtgeld-Schnittstelle (Roadmap M1 Nr. 8)

Stand der Recherche: 2026-11-07. Dabei wurden weder ein authentifizierter Endpunkt noch ein Order-Endpunkt aufgerufen. Abgerufen wurden nur das öffentliche `GET /v2/markets?market=BTC-EUR|ETH-EUR` ohne Key, die Doku-Seiten und die GitHub-API.

Rohquellen: die OpenAPI-Datei https://docs.bitvavo.com/api-specs/exchange-rest-api.yaml (`info.version: 2.10.0`) und der Doku-Volltext https://docs.bitvavo.com/llms-full.txt.

Die OpenAPI-Datei trägt die Version 2.10.0. Neuere Änderungen bis 2026-08-07 stehen nur in den Release Notes. An zwei Stellen weicht sie davon ab: `pricePrecision` ist dort noch nicht als „immer null“ beschrieben, und die Gewichte für `/ordersOpen` sind schon die neuen.

Hauptquellen:
- Doku: https://docs.bitvavo.com/docs/rest-api/introduction/
- OpenAPI: https://docs.bitvavo.com/api-specs/exchange-rest-api.yaml
- Volltext: https://docs.bitvavo.com/llms-full.txt
- Releases: https://docs.bitvavo.com/releases/

---

## 1. Basis und Authentifizierung
Quelle: https://docs.bitvavo.com/docs/rest-api/introduction/

- **Basis-URL:** `https://api.bitvavo.com/v2`
- **Header bei jedem privaten Request:**
  - `Bitvavo-Access-Key`
  - `Bitvavo-Access-Timestamp`: Unix-Zeit in **ms**
  - `Bitvavo-Access-Signature`
  - `Bitvavo-Access-Window`: optional, Standard 10000 ms, höchstens 60000 ms. Laut Fehlerseite ist der erlaubte Bereich 100–60000 ms, sonst Fehler 303.
  - Dazu `Content-Type: application/json`.
- **Signatur:** HMAC-SHA256 in Hex, mit dem API-Secret als Schlüssel, über den String `timestamp + METHOD + path + body`.
  - Der Pfad beginnt mit `/v2`, zum Beispiel `/v2/order`.
  - Bei GET ist der Body ein leerer String.
  - Der gesendete Body muss Byte für Byte dem signierten entsprechen. Das offizielle Beispiel nutzt `json.dumps(body, separators=(',', ':'))`.
  - **Der Query-String gehört zum signierten Pfad.** Das steht so nur im offiziellen Python-SDK: `createSignature(now, method, endpoint + postfix, …)` mit `postfix = ?market=…`, siehe https://github.com/bitvavo/python-bitvavo-api/blob/master/python_bitvavo_api/bitvavo.py. Die Doku nennt als Beispiel nur `/v2/order` und sagt zum Query-String nichts ausdrücklich. **Vor M2 mit einem echten Key prüfen.**
- **Fehler bei der Authentifizierung** (https://docs.bitvavo.com/docs/errors/):

  | Code | Bedeutung |
  |---|---|
  | 300 | Authentifizierung fehlt |
  | 302 | Timestamp nicht in ms |
  | 303 | Access-Window außerhalb des Bereichs |
  | 304 | Request kam nicht innerhalb des Access-Windows an |
  | 305 / 306 | Key nicht aktiv / Aktivierung nicht bestätigt |
  | 307 | IP steht nicht auf der Whitelist |
  | 308 / 309 | Signaturformat ungültig / Signatur ungültig |
  | 317 | Konto gesperrt |
  | 318 | Konto nicht verifiziert |

  Die Codes 300–318 kommen mit HTTP 403.
- **Serverzeit:** Mit `GET /time` (Gewicht 1) lässt sich die Uhrabweichung prüfen. Siehe OpenAPI `/time`.

## 2. API-Keys: Rechte und IP-Whitelist
Quellen: https://docs.bitvavo.com/docs/get-started/ und https://docs.bitvavo.com/docs/rest-api/introduction/#permissions

- **Rechte:**
  - **View access / Read-only:** Konto, Salden, Transfers.
  - **Trade digital assets:** Orders anlegen, ändern, ansehen und stornieren.
  - **Withdraw digital assets:** Auszahlungen. Die Doku warnt, dass Auszahlungen über die API **ohne 2FA und ohne E-Mail-Bestätigung** laufen.
  - Dazu Subaccount-, Internal-Transfer- und Administrative-Rechte.
- **Wichtig für ADR-0004:** Laut Doku braucht ein Key, der Orders und Trades lesen soll (`GET /order`, `/orders`, `/ordersOpen`, `/trades`), **zusätzlich das Recht Trade digital assets**.
  - Ein reiner Read-only-Key reicht nur für Salden und Konto. `GET /balance` braucht vermutlich nur View.
  - Folge: Einen Abgleich mit dem Orderbestand gibt es in M1 nicht. Mit einem Read-only-Key wäre nur ein Saldenabgleich möglich.
- **Fehlende Rechte** liefern 310 (Trade), 311 (View) oder 312 (Withdraw).
- **IP-Whitelist:** Sie wird beim Anlegen des Keys gesetzt; mehrere IPs werden durch Komma getrennt. Die Doku nennt sie einen Tipp, nicht Pflicht. Ohne Whitelist kann jeder mit Key und Secret handeln.
- **Key anlegen:**
  - Nur auf der Website, mit aktivem 2FA.
  - Der Key muss per E-Mail-Link aktiviert werden.
  - Das Secret wird nur einmal angezeigt.
  - Quellen: Support-Artikel https://support.bitvavo.com/hc/en-us/articles/4405059841809 (WebFetch erhielt 403, Inhalt aus den Suchtreffern) und die Get-started-Seite.
- **Ableitung für den Entwurf:** Key nur mit View plus Trade, **nie Withdraw**, Whitelist nur mit der Server-IP. Die IP gehört nicht ins Repo.

## 3. Order anlegen: `POST /order` (Gewicht 1)
Quellen: https://docs.bitvavo.com/docs/rest-api/create-order/ und OpenAPI `/order post`

**Pflichtfelder:** `market`, `side` (`buy` | `sell`), `orderType`, `operatorId`.

| Feld | Typ | Regeln |
|---|---|---|
| `orderType` | string | `market`, `limit`, `stopLoss`, `stopLossLimit`, `takeProfit`, `takeProfitLimit` |
| `amount` | string | Menge in Basiswährung. Höchstens `quantityDecimals` Nachkommastellen. Pflicht bei limit. |
| `amountQuote` | string | Menge in Quote-Währung (EUR). Nur für `market`, `stopLoss`, `takeProfit`. Höchstens `notionalDecimals` Nachkommastellen. Market braucht entweder `amount` oder `amountQuote`; beide zusammen ergeben Fehler 236. |
| `price` | string | Pflicht bei limit. **Muss ein Vielfaches von `tickSize` sein.** Breaking Change seit v2.12.0 (2025-09-28), sonst Fehler 422: https://docs.bitvavo.com/releases/v2.12.0 |
| `triggerAmount`, `triggerType` (`price`), `triggerReference` | | Für Stop- und Take-Profit-Typen. `triggerReference`: `lastTrade`, `bestBid`, `bestAsk`, `midPrice`. |
| `timeInForce` | | `GTC` (Standard), `IOC`, `FOK`. Bei IOC oder FOK kann die Order `expired` enden. |
| `postOnly` | bool | Standard false |
| `selfTradePrevention` | | `decrementAndCancel` (Standard), `cancelOldest`, `cancelNewest`, `cancelBoth`. Self-Trading ist auf Bitvavo verboten. |
| `disableMarketProtection` | bool | Veraltet und muss false sein. Seit v2.6.0 wird das Feld ignoriert. |
| `responseRequired` | bool | Standard true: volles Order-Objekt. false: Doku-Text sagt „nur HTTP-Status“, das Beispiel zeigt aber einen reduzierten Body mit `orderId`, `clientOrderId` und Zeitstempeln. **Widersprüchlich.** |
| `clientOrderId` | string, **UUID** | Optional. Siehe Abschnitt 4. |
| `operatorId` | **Integer** (int64) | Pflicht. Siehe Abschnitt 5. |
| `codGroupId` | int 1–1000 | Gruppe für Cancel-on-Disconnect |

- **Limit offener Orders:** Die Doku nennt höchstens 100 offene Orders je Markt (Fehler 235). Live meldet `GET /markets` am 2026-11-07 aber `maxOpenOrders: 400` für BTC-EUR und 100 für ETH-EUR. Den Wert aus `/markets` nehmen.
- **Antwort (vollständig):**
  - Kennungen und Zeit: `orderId` (UUID, **nur je Markt eindeutig**), `clientOrderId` (nur wenn gesetzt), `market`, `operatorId`, `created` und `updated` (ms), `createdNs` und `updatedNs`
  - Zustand: `status`, `side`, `orderType`
  - Mengen: `amount` / `amountRemaining` oder `amountQuote` / `amountQuoteRemaining`, `filledAmount`, `filledAmountQuote`
  - Reservierung: `onHold`, `onHoldCurrency`
  - Gebühr: `feePaid` (negativ bei Rabatt), `feeCurrency` (Beispiel: EUR)
  - Teilausführungen: `fills[]` mit `id`, `timestamp`, `amount`, `price`, `taker`, `fee`, `feeCurrency`, `settled`. `fee` und `feeCurrency` fehlen, solange `settled = false` ist.
  - Sonst: `restatementReason`, `timeInForce`, `postOnly`, `selfTradePrevention`, `visible`, Trigger-Felder
- **Zeitstempel:** Seit v2.11.0 (2025-09-15) gibt es `createdNs` und `updatedNs`, vorerst nur mit µs-Genauigkeit. ms-Zeitstempel werden später mit Vorankündigung abgeschafft. https://docs.bitvavo.com/releases/v2.11.0
- **Unklar:** Welche Währung `onHoldCurrency` bei Kauf und Verkauf hat. Die FIX-Doku sagt Quote bei Verkauf und Basis bei Kauf (llms-full.txt, Tag 5004). Das wirkt vertauscht. Nicht darauf verlassen.

## 4. `clientOrderId`: Eindeutigkeit, keine Idempotenz

- **Format:** UUID. Eindeutig nur unter den **offenen** Orders.
- **Seit v2.6.0 (2025-02-06):** Nach Abschluss einer Order darf die `clientOrderId` wiederverwendet werden. `GET /order` mit `clientOrderId` liefert dann nur die neueste Order; in `GET /orders` steht sie nur bei der neuesten, frühere zeigen `null`. https://docs.bitvavo.com/releases/v2.6.0/
- **Fehler 220:** „A `clientOrderId` with this value already exists in the market“, HTTP 400, laut https://docs.bitvavo.com/docs/errors/. Zusammen mit v2.6.0 heißt das wohl: nur gegenüber offenen Orders.
- **Folge, abgeleitet:** Die `clientOrderId` ist **kein Idempotenzschlüssel**.
  - Eine Market-Order ist sofort abgeschlossen. Wird sie nach einem Timeout mit derselben `clientOrderId` erneut gesendet, nimmt Bitvavo sie wahrscheinlich als **zweite Order** an.
  - Eine Idempotenz für Orders dokumentiert Bitvavo nirgends. Ein `idempotencyKey` gibt es nur bei Auszahlungen (Fehler 412).
- **Offizielle Regel bei 503 / Code 109:** „The operation may or may not have succeeded … get the order before creating it again“, siehe https://docs.bitvavo.com/docs/errors/.
- **Abfrage und Storno über `clientOrderId`:** `GET /order` und `DELETE /order` akzeptieren laut Text `orderId` **oder** `clientOrderId`; sind beide gesetzt, zählt `clientOrderId`. Die OpenAPI markiert `orderId` aber als `required: true`. **Widersprüchlich, vor M2 prüfen.**

## 5. `operatorId`: Pflicht

- **Ganzzahl, Pflicht** bei REST und WebSocket für:
  - Order anlegen (`POST /order`)
  - Order ändern (`PUT /order`)
  - Order stornieren (`DELETE /order`, dort als Query-Parameter)
  - alle Orders stornieren (`DELETE /orders`, ebenfalls Query-Parameter)
- **Ohne `operatorId` lehnt Bitvavo den Request ab.** Einen eigenen Fehlercode dafür dokumentiert Bitvavo nicht.
- **Zeitlicher Ablauf:**
  - eingeführt in v2.7.0 (2025-04-23)
  - seit v2.8.0 (2025-06-03) in Antworten enthalten
  - **Pflicht seit v2.9.0 (2025-06-30).** Ein Stichtag steht nicht auf der Seite.
  - seit 2026-08-07 auch in `GET /trades` und im WebSocket-Kanal Track-your-orders
  - Quellen: https://docs.bitvavo.com/releases/v2.9.0/ und https://docs.bitvavo.com/releases/
- **Format:** Integer, keine Zeichenkette, kein Float, kein null; das SDK-Beispiel für Go übergibt trotzdem `"543462"` als String. Selbst gewählt, „unique and consistent within your account“, Beispiel: ein Wert je Bot oder Strategie, etwa 2001. Bitvavo nennt es „critical for compliance and auditability“. https://docs.bitvavo.com/docs/faqs/
- Der Support-Artikel https://support.bitvavo.com/hc/en-us/articles/37469511891985 war nicht abrufbar (403). Dass ein String-Wert die Order abweisen kann, stammt nur aus der Zusammenfassung der Suchtreffer. Dass der Typ Integer ist, belegen OpenAPI und FAQ.
- **Ableitung für den Entwurf:** eine feste `operatorId` je Depot oder Agent, dauerhaft in der DB festgehalten.

## 6. Abfragen und Stornieren

Quellen: OpenAPI und die jeweiligen Seiten unter https://docs.bitvavo.com/docs/rest-api/…

| Endpunkt | Gewicht | Parameter / Hinweise |
|---|---|---|
| `GET /order` | 1 | `market` (Pflicht), `orderId` und/oder `clientOrderId` |
| `DELETE /order` | 1 (seit 2026-05-19) | `market`, `orderId`/`clientOrderId`, `operatorId`. Antwort: `orderId`, `clientOrderId`, `operatorId`. Seit v2.7.1 gibt es für schon abgeschlossene oder unbekannte Orders **HTTP 404 / Code 240** statt 233. |
| `GET /orders` | 5 | `market` (Pflicht), `limit` 1–1000 (Standard 500), `start`/`end` (ms), `orderIdFrom`/`orderIdTo`. Der Filter `orderId` ist seit v2.9.0 entfernt. |
| `GET /ordersOpen` | **5** mit `market`, **100** ohne (seit 2026-05-19, vorher 1 bzw. 25) | `market` oder `base`. `fills` ist hier leer und veraltet (seit v2.6.0). |
| `DELETE /orders` | 25 mit `market`, 100 ohne | `market`, `operatorId` (Pflicht) |
| `DELETE /atomic/orders` | 100 | neu seit 2026-05-19, storniert alles in einem Markt atomar |
| `GET /trades` | 5 | `market` (Pflicht), `limit` ≤ 1000, `start`/`end` mit **höchstens 24 h Spanne**, `tradeIdFrom`/`tradeIdTo` (`tradeId` veraltet). Felder: `id`, `orderId`, `clientOrderId`, `operatorId`, `timestamp`, `market`, `side`, `amount`, `price`, `taker`, `fee`, `feeCurrency`, `settled`. **Trades mit Price Guarantee (App-Käufe) fehlen hier.** Die vollständige Historie liefert `GET /account/history` (Gewicht 1). |

Release-Quellen: https://docs.bitvavo.com/releases/2026-05-19-exchange/, https://docs.bitvavo.com/releases/v2.7.1

## 7. Order-Status, Teilausführungen und Preisschutz
Quellen: https://docs.bitvavo.com/docs/order-lifecycle/ und https://docs.bitvavo.com/releases/v2.10.0

- **Aktive Status:** `new`, `awaitingTrigger`, `partiallyFilled`.
- **Abgeschlossen** (seit v2.10.0, 2025-08-17) nur noch: `filled`, `expired`, `canceled`. Der Grund steht in `restatementReason`, zum Beispiel:
  - Preisschutz: `cancelOnSpreadProtection`, `cancelOnExecutionPriceProtection`, `cancelOnPlacementPriceProtection`, `cancelOnReferencePriceProtection`
  - Selbsthandel: `cancelOnSelfTradePrevention`, `decrementOnSelfTradePrevention`
  - Betrieb: `cancelOnMaintenance`, `cancelOnDisconnect`, `cancelOnDelisting`, `cancelOnLockPlaced`, `cancelOnAdminRequest`
  - Post-only: `cancelPostOnly`, `cancelPostOnlyOnAuctionMatching`
- **`expired` bei Market-Orders:** keine Gegenseite, zu wenig Liquidität **oder zu wenig Guthaben**.
- **Preisschutz ist immer aktiv.** Beim Schlupf-Schutz („slippage price protection“) wird eine Market-Order nur **teilweise** ausgeführt und der Rest storniert. Doku-Beispiel mit 4 % Schwelle: Kauf von 10 BTC, ausgeführt 5, Rest storniert.
- **Ableitung für den Entwurf:** Live-Market-Orders können teilweise ausgeführt enden. Gebucht werden muss nach den tatsächlichen `fills`, nicht nach dem Sollwert.
- **Gebühr erst bei Abrechnung:** Erst mit `settled = true` sind `fee` und `feeCurrency` bekannt. Gebühren werden „kurz nach“ dem Trade abgezogen (FAQ).
- **Marktstatus** laut `GET /markets`: `trading`, `halted`, `auction`, `auctionMatching`, `cancelOnly`. Zugehörige Fehler: 423–426 (HTTP 400) und 431 (HTTP 409).
- Die Reihenfolge von Fill- und Order-Events ist laut FAQ **nicht garantiert**.

## 8. Mindestgrößen und Genauigkeit: `GET /markets` (öffentlich, Gewicht 1)

**Felder:** `status`, `base`, `quote`, `minOrderInBaseAsset`, `minOrderInQuoteAsset`, `maxOrderInBaseAsset`, `maxOrderInQuoteAsset`, `quantityDecimals`, `notionalDecimals`, `tickSize`, `maxOpenOrders`, `feeCategory`, `orderTypes`. Seit v2.8.0 (2025-06-03). `pricePrecision` ist seit v2.12.0 veraltet und **immer null**. https://docs.bitvavo.com/releases/v2.8.0, https://docs.bitvavo.com/releases/v2.12.0

**Live abgerufen am 2026-11-07 (öffentlich):**

| Markt | minOrderInBaseAsset | minOrderInQuoteAsset | quantityDecimals | notionalDecimals | tickSize | maxOpenOrders | feeCategory |
|---|---|---|---|---|---|---|---|
| BTC-EUR | 0.00007294 | 5.00 | 8 | 2 | 1.00 | 400 | A |
| ETH-EUR | 0.00242890 | 5.00 | 8 | 2 | 0.010 | 100 | A |

- Die Werte sind JSON-Strings, nur `quantityDecimals`, `notionalDecimals` und `maxOpenOrders` sind Zahlen. Die Doku-Beispiele zeigen sie als Strings.
- **Vermutung, nicht dokumentiert:** `minOrderInBaseAsset` entspricht etwa 5 € geteilt durch den aktuellen Kurs. Der Wert ändert sich also mit dem Kurs und muss frisch geholt werden.
- **Fehler bei Größe und Genauigkeit:**

  | Code | Bedeutung |
  |---|---|
  | 212 | `amount` unter dem Minimum |
  | 217 | `amountQuote` unter dem Minimum |
  | 210 / 211 | über dem Maximum |
  | 213 | Preis zu niedrig |
  | 215 | Preis mit mehr als 15 Nachkommastellen |
  | 422 | Preis nicht auf `tickSize` |
  | 429 | zu viele Nachkommastellen |

  Alle mit HTTP 400. https://docs.bitvavo.com/docs/errors/
- **Hinweis für den Entwurf:** Der Paper-Zwangsverkauf `RISK_SELL` handelt laut `docs/ARCHITEKTUR.md` „auch unter 50 €“. Live scheitert jeder Rest unter 5 € beziehungsweise unter `minOrderInBaseAsset` mit Code 212 oder 217. Solche Reste bleiben unverkäuflich.

## 9. Guthaben: `GET /balance` (Gewicht 5)

- Parameter: `symbol` (optional).
- Antwort: `symbol`, `available`, `inOrder`.
- Seit v2.6.0 nur Assets mit Saldo über 0.
- Zu wenig Guthaben: **Code 216, HTTP 400** („Your balance is insufficient“). Eine Market-Order kann stattdessen auch `expired` enden.
- Quellen: OpenAPI `/balance`, https://docs.bitvavo.com/docs/errors/

## 10. Gebühren

- **`GET /account`** (Gewicht 1) liefert `fees.taker`, `fees.maker` und `fees.volume` (Handelsvolumen der letzten 30 Tage in EUR) für Kategorie A.
- **`GET /account/fees?market=BTC-EUR`** (Gewicht 1) liefert `tier` (beginnt bei 0), `volume`, `taker` und `maker` für die Kategorie dieses Markts.
- Quelle: OpenAPI `/account`, `/account/fees`. Die Doku-Seite heißt „Get market fees“: https://docs.bitvavo.com/docs/rest-api/get-market-fees/
- BTC-EUR und ETH-EUR liegen beide in **Kategorie A** (live `/markets`).
- **Stufe 0 in Kategorie A: maker 0,15 %, taker 0,25 %.** Das stimmt überein mit:
  - den Doku-Beispielwerten `0.0015` / `0.0025`
  - mehreren Suchtreffern zu https://bitvavo.com/en/fees
  - dem Paper-Wert `TAKER_FEE = 0,0025`
- **Unsicher:** die Volumenstufen darüber. Die Seite https://bitvavo.com/en/fees war nicht abrufbar (403). Laut Suchtreffern gibt es Stufen bei etwa 100 k, 250 k, 500 k, 1 M und 2,5 M €, dazu eine neuere Stufe ab 25 M € mit maker 0 % und taker 0,02% (Blog https://bitvavo.com/en/blog/new-maker-taker-fees, Datum unbekannt). Die Quellen widersprechen sich. **Die tatsächliche Gebühr live aus `/account/fees` und aus `fills[].fee` lesen, nicht fest einprogrammieren.**
- **Gebührenwährung:** Das Feld `feeCurrency` ist je Fill angegeben, alle Doku-Beispiele zeigen `EUR`. Ob bei Kauf und Verkauf immer die Quote-Währung belastet wird, ist **nicht ausdrücklich dokumentiert**.

## 11. Rate Limits
Quellen: https://docs.bitvavo.com/docs/rate-limits/ und FAQ

- **Kontingent:** Standard **1000 Gewichtspunkte pro Minute**.
  - Mit Authentifizierung wird über alle Konten summiert, öffentliche und private Endpunkte zusammen.
  - Ohne Authentifizierung wird je IP gezählt.
  - REST und WebSocket teilen sich das Kontingent.
- **Antwort-Header:** `bitvavo-ratelimit-limit`, `bitvavo-ratelimit-remaining`, `bitvavo-ratelimit-resetat` (ms). Live bestätigt: `limit: 1000`.
- **Überschreitung:** **HTTP 429, Code 105.**
  - Mit Authentifizierung **1 Minute gesperrt**, gezählt je API-Key. Doku-Beispiel: überschritten um 12:00:15, weiter um 12:02:00.
  - Ohne Authentifizierung **15 Minuten** je IP.
  - Code 112 betrifft nur das WebSocket-Nachrichtenlimit.
- **Weitere Hinweise:**
  - Ein höheres Limit gibt es auf Anfrage beim Support.
  - Parallele Reconnects können 429 auslösen.
  - Das Python-SDK liest die Sperrzeit aus dem Fehlertext. Dabei vergleicht es Sekunden mit Millisekunden, die Wartelogik dort ist fehlerhaft (eigene Beobachtung im Quelltext).
- **Bewertung:** Bei 1 Runde pro Minute und 2 Märkten liegt der Bedarf weit unter dem Limit. Eine Runde mit Order, Abfrage, Saldo und Kursen kostet grob 20–30 Punkte.

## 12. Cancel on Disconnect
Quellen: https://docs.bitvavo.com/docs/cancel-on-disconnect/ und OpenAPI `/cancelOrdersAfter`

- **Neu seit v2.11.0 (2025-09-15).**
- **Ablauf:**
  - `POST /cancelOrdersAfter` (Gewicht 5) mit `codGroupId` und `expiryAfterSeconds` (mindestens 10; 0 entfernt die Gruppe). Antwort: `timeOfExpirySeconds`.
  - Orders tragen die `codGroupId`.
  - Läuft der Countdown ohne Erneuerung ab, werden die offenen Orders der Gruppe storniert, mit `restatementReason = cancelOnDisconnect`.
  - Bitvavo empfiehlt, nach der halben Frist zu erneuern.
  - Fehler beim Erneuern: 433 mit `rejectionReason` `LIMIT_REACHED`, `INVALID_GROUP_ID` oder `INVALID_EXPIRY_AFTER_SECONDS`.
- **Grenzen:**
  - Standardmäßig nur **eine** Gruppe je Konto.
  - **Gilt nicht für Market-Orders.** Die Beispiele zeigen allerdings Market-Orders mit `codGroupId`, die Doku ist hier widersprüchlich.
  - Eine Höchstfrist ist nicht dokumentiert.
- **Relevanz:** nur, wenn der künftige Live-Broker Limit-, Stop- oder GTC-Orders liegen lässt.

## 13. Weitere Fehler und Wiederholregeln
Quelle: https://docs.bitvavo.com/docs/errors/

- **Fehlerformat:** `{"errorCode": <int>, "error": "<text>"}`.
- **Nicht wiederholen** bei 400, 403 und 404, ohne die Ursache zu beheben.
- **Bei 429:** bis `resetat` warten.
- **5xx:**

  | Code | Bedeutung | Vorgehen laut Doku |
  |---|---|---|
  | 101 | Unbekannter Fehler | Support kontaktieren |
  | 107 | Überlastet | nach 500 ms wiederholen |
  | 108 | Verarbeitungsproblem | Access-Window erhöhen oder nach 500 ms wiederholen |
  | **109** | **Timeout, Ergebnis unbekannt** | **Zustand prüfen, bevor neu gesendet wird** |
  | 111 | Matching Engine nicht verfügbar | kurz warten, wiederholen |
  | 419 | Server nicht verfügbar | kurz warten, wiederholen |
  | 430 | Keine Marktdaten | prüfen, ob der Markt im Status `trading` ist |

  Alle mit HTTP 503, außer 101 (HTTP 500).
- **Parameterfehler:** 203 (fehlende oder unverträgliche Parameter), 205 (ungültiger Wert), 236 (`amount` und `amountQuote` zugleich), 219 (Markt nicht mehr gelistet), 231 (Markt pausiert und nicht GTC).

## 14. Testumgebung und SDKs

- **Für REST und WebSocket gibt es keine Sandbox und kein Testnet.** Nur die FIX-API hat eine UAT-Umgebung (`connection.bitvavo-uat.com`, llms-full.txt, Abschnitt FIX Introduction). Folge: Jeder Test der Order-Strecke ist Echtgeld.
- **Python-SDK `bitvavo/python-bitvavo-api`:**
  - Letztes Release v1.4.6 vom 2025-05-14, letzter Push 2025-08-04.
  - Es entstand also **vor** `operatorId`-Pflicht (Juni 2025), Tick-Size-Pflicht (Sept. 2025) und neuen Gewichten (Mai 2026). Die SDK-Beispiele reichen `operatorId` aber als Body-Parameter durch.
  - Die SDKs für Java, Go und PHP sind archiviert. Das Node-SDK ist aktiv (v1.5.0 vom 2025-06-24, Push 2026-07-25).
  - Quelle: GitHub-API `api.github.com/orgs/bitvavo/repos`.
- **Bewertung, offen für die Spec:** Ein eigener schlanker HTTP-Client nach dem offiziellen Signaturbeispiel wäre aktueller und besser testbar als das SDK. Das ist eine Entwurfsentscheidung, keine Tatsache.

## 15. Konsequenzen für den Entwurf (Ableitungen, keine Fakten)

1. **Kein blindes Wiederholen von `POST /order`.** Vor jeder Order eine `clientOrderId` (UUID) erzeugen und **vorher** in der DB festhalten. Bei Timeout oder 5xx den Zustand per `GET /order` (mit `clientOrderId`) und `GET /trades` klären, bevor erneut gesendet wird. Ein unklarer Zustand heißt fail closed: das Depot sperren, nicht erneut handeln.
2. Den Live-Broker mit festen Typen versehen: `operatorId: int` je Agent, Beträge als `Decimal`, auf `quantityDecimals`, `notionalDecimals` und `tickSize` abgerundet. Vor dem Senden gegen `minOrderIn*` und den Marktstatus `trading` prüfen.
3. Gebucht wird nach den tatsächlichen Fills (Teilausführungen, Preisschutz, `settled`). Die Paper-Ausführung (ADR-0007, bitgleich zu Arena v0.3.1) bleibt unverändert. Live ist eine eigene Implementierung hinter derselben Schnittstelle.
4. Für den Key gilt: nur View plus Trade, nie Withdraw, IP-Whitelist. Laut ADR-0004 wird er erst in M2 angelegt. Auch ein Order- und Trade-Abgleich braucht das Trade-Recht, ist in M1 also nicht möglich.
5. Weil es keine Sandbox gibt, braucht M2 einen eigenen Plan für die ersten Echtgeld-Orders mit Mindestbetrag (etwa 5 €).

**Offen – manuell prüfen (nur in M2, mit echtem Key):**
- ob der Query-String in die Signatur gehört
- ob `GET /order` oder `DELETE /order` nur mit `clientOrderId` funktioniert
- wie die Antwort bei `responseRequired=false` aussieht
- Gebührenwährung und Volumenstufen
- welche Rechte `/balance` tatsächlich braucht
