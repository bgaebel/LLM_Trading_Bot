# ADR-0019: Übersicht aktualisiert sich live über ein eigenes Skript (`script-src 'self'`)

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-07
- **Bereich:** #web #sicherheit

## Kontext
Die Übersicht (Spec 007) ist eine Momentaufnahme, die sich per `<meta http-equiv="refresh">` alle 15 s neu lädt; die
CSP lautet bisher `script-src 'none'`. Der Nutzer will, dass Änderungen ohne Neuladen live erscheinen (2026-11-07):
das Alter von Lebenszeichen zählt sichtbar, eine neue Runde erscheint ohne Seitenwechsel. ADR-0014/0015 verlangen
„CSP ohne fremde Quellen und ohne Inline-Skript“ – eine Skriptdatei vom selben Server ist damit vereinbar; „kein
Skript“ stand nur in Spec 007. Die Seite ist nur per SSH-Tunnel erreichbar (ADR-0005), zeigt LLM-Begründungen (fremder
Text, wird escaped) und hat Formulare mit CSRF-Token.

## Entscheidung
- Die CSP wird zu `script-src 'self'`; alles andere bleibt (`default-src 'self'`, `style-src 'self'`, kein
  Inline-Skript, keine Inline-Event-Handler, kein `eval`, keine fremden Quellen, `frame-ancestors 'none'`).
- Genau eine Skriptdatei `/app.js` aus dem Paket (Standardbibliothek, kein Build-Schritt, keine Fremdbibliothek),
  nur auf der Übersicht eingebunden. Sie holt alle 5 s die Übersicht vom selben Server und ersetzt den Inhalt; das
  HTML erzeugt weiter nur der Server (dieselbe Escaping-Regel wie bisher). Alter („vor N s“) zählt sie jede Sekunde
  aus einem `data-`Attribut mit der Epoche hoch.
- Ohne Skript (abgeschaltet) bleibt das Neuladen per `<noscript>`-Meta-Refresh. Die Einstellungsseite bekommt kein
  Skript.

## Verworfene Alternativen
- **Nur Meta-Refresh (bisher):** zeigt Änderungen erst beim Neuladen; vom Nutzer verworfen.
- **Server-Sent Events / WebSocket:** hält Verbindungen offen; der einfädige Ablauf der App (eine Anfrage nach der
  anderen) und die kurzen Lese-Snapshots (ADR-0016) passen schlecht dazu; 5-s-Abfrage reicht bei Runden je Minute.
- **JSON-Endpunkt mit Rendering im Browser:** doppelte Darstellungslogik (Python und JavaScript) und neue
  Escaping-Stellen; verworfen zugunsten „Server rendert, Skript tauscht aus“.
- **Fremdbibliothek (htmx o. ä.):** widerspricht „keine fremden Quellen“ und braucht Pflege.

## Konsequenzen
- Positiv: Live-Anzeige ohne neue Abhängigkeit; Darstellung und Escaping bleiben an einer Stelle.
- Negativ: erstmals JavaScript – eine eigene Datei, die mit der CSP `script-src 'self'` laufen darf; jede Änderung
  an `app.js` ist sicherheitsrelevant (kein `eval`, keine Inline-Handler, nur gleiche Herkunft). Alle 5 s ein
  Lese-Snapshot (gemessen 0,04 s auf einer Jahres-DB, Spec 007).
- Neu bewerten, wenn: die Seite ohne Tunnel erreichbar sein soll, Fremdinhalte als HTML statt Text gezeigt werden,
  oder mehr als diese eine Seite Skript braucht.

## Code-Anker
- `src/llm_trading_bot/web/app.py` (`SECURITY_HEADERS`), `src/llm_trading_bot/web/app.js` (geplant, Spec 010)
