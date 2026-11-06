# ADR-0015: Weboberfläche wie ADR-0014, Passwort mindestens 8 Zeichen

- **Status:** angenommen
- **Löst ab:** [ADR-0014](0014-weboberflaeche-zugangsdaten.md)
- **Datum:** 2026-11-06
- **Bereich:** #web #sicherheit #betrieb

## Kontext
ADR-0014 wurde ohne vorherige Durchsicht des Nutzers angenommen (Anweisung „autonom bis zur Webseite“) und
verlangte mindestens 12 Zeichen. Der Nutzer hat am 2026-11-06 mindestens 8 Zeichen festgelegt. Die Seite ist nur
über `127.0.0.1` bzw. einen SSH-Tunnel erreichbar; Raten ist durch `scrypt` und die Sperre nach 5 Fehlversuchen
gebremst.

## Entscheidung
- Passwort für die Weboberfläche: **mindestens 8 Zeichen**.
- Alles Übrige unverändert aus ADR-0014: nur `127.0.0.1` (plus Host-Header-Prüfung), Ersteinrichtung nur mit dem
  einmaligen Einrichtungscode aus dem Terminal, `scrypt`-Hash mit Salt in `web-auth.json` (600), Vergleich in
  konstanter Zeit, nach 5 Fehlversuchen 60 s Sperre, Sitzungs-Cookie `HttpOnly`/`SameSite=Strict` für 12 h nur im
  Speicher, CSRF-Token in jedem Formular, `secrets.env` (600, atomar) im Datenverzeichnis mit `OPENAI_API_KEY` und den
  Spec-005-Einstellungen, Keys nie wieder vollständig angezeigt, kostenloser Verbindungstest, `paper-cycle` liest
  `secrets.env` (Umgebung geht vor), Sicherheits-Header, kein Bitvavo-Key-Feld (ADR-0004), Standardbibliothek.

## Verworfene Alternativen
- **Mindestens 12 Zeichen (ADR-0014):** vom Nutzer verworfen.
- **Keine Mindestlänge:** zu leicht zu erraten, auch mit Sperre.
- Alle weiteren verworfenen Alternativen wie in ADR-0014.

## Konsequenzen
- Positiv: Passwort, das der Betreiber sich merken will.
- Negativ: kleinerer Suchraum; ausreichend nur, solange die Seite nicht öffentlich erreichbar ist.
- Neu bewerten, wenn: die Seite ohne SSH-Tunnel erreichbar sein soll, mehrere Nutzer oder Echtgeld-Keys dazukommen.

## Code-Anker
- `src/llm_trading_bot/web/auth.py` (`MIN_PASSWORD_LENGTH`), `src/llm_trading_bot/web/` (Spec 006)
