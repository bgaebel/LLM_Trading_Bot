# ADR-0028: Weboberfläche wie ADR-0024; die Übersicht ist ohne Login lesbar, solange alles Paper ist

- **Status:** angenommen
- **Löst ab:** [ADR-0024](0024-weboberflaeche-oeffentlich-https.md)
- **Datum:** 2026-11-08
- **Bereich:** #web #sicherheit #datenschutz

## Kontext
ADR-0024 macht die Weboberfläche per HTTPS hinter Caddy öffentlich, aber nur mit Login; „nur die Übersicht
öffentlich“ war damals verworfen, weil der Nutzer es nicht gewählt hatte. Am 2026-11-08 will der Nutzer die
Übersicht ohne Anmeldung sichtbar machen. Auf der Übersicht stehen nur Daten des Bots: virtuelle Depots,
Equity-Verlauf, Kauf-/Verkaufssignale, virtuelle Trades, Lebenszeichen, GPT-Kosten des Tages. Echtgeld gibt es
nicht (ADR-0004); die Einstellungen enthalten den OpenAI-Key, den Pausenschalter und die Freigabe gestoppter Depots.

## Entscheidung
- **Alles aus ADR-0024 gilt weiter:** Caddy mit HTTPS, App nur auf `127.0.0.1` (8787 Tunnel, 8788 Caddy),
  Zugangsweg am Socket, ein öffentlicher Host, gehärteter Login (Sperre je Quelle, Geräte-Cookie, 12 Zeichen
  öffentlich, keine Einrichtung über das Internet).
- **Neu: `GET /` ohne Sitzung zeigt eine Lese-Ansicht der Übersicht** (beide Zugangswege): ohne Formular,
  CSRF-Token, Reiter und Cookie, mit einem Link „Anmelden“. Alles andere (Einstellungen, jede POST-Aktion,
  Abmelden) verlangt weiter den Login. Wer angemeldet ist, sieht die volle Übersicht wie bisher.
- **Ohne Login ausgeblendet:** Fehlertexte von OpenAI, der Datenbank und der Statusdatei (nur „nicht lesbar“) und
  Hinweise auf Konfigurationsdateien – sie beschreiben Interna und helfen Besuchern nicht.
- **Sitzungsende bleibt sichtbar:** Die Seite des Besitzers ist markiert; bekommt `/app.js` dort die Ansicht ohne
  Login zurück, ist die Sitzung abgelaufen und der Browser wechselt zum Login (wie Spec 010).
- **Last:** Die Lese-Ansicht liest die Datenbank höchstens alle 10 s (ein Zwischenspeicher im Web-Prozess für
  alle Besucher); gerendert wird je Abruf, Uhrzeit und Alter laufen weiter (die Stufe „läuft/verzögert“ hinkt
  an einer Schwelle höchstens 10 s nach). Die Aktualisierungen von `/app.js` kommen ohne die 121 KB
  Hintergrundgrafik (Kopfzeile `X-Live-Update: 1`). Die Signale-Tabelle wird nur neu gelesen, wenn eine neue
  Entscheidung dazukam.
- **Nicht in Suchmaschinen:** `X-Robots-Tag: noindex, nofollow` auf jeder Antwort und `/robots.txt` mit
  `Disallow: /`.
- **Abschaltbar:** Schalter „Übersicht ohne Login“ in den Einstellungen, gespeichert als
  `WEB_PUBLIC_OVERVIEW=0` in `secrets.env`; fehlt der Eintrag, ist die Ansicht an (Wunsch des Nutzers). Ist
  `secrets.env` unlesbar, gilt der Login (fail closed).
- **Nur im Paper-Modus:** Ein Test schlägt fehl, sobald der Live-Modus freigeschaltet werden kann. Vor Echtgeld
  (M2) muss ein neues ADR entscheiden, ob und was öffentlich bleibt.

## Verworfene Alternativen
- **Ganz ohne Login, auch Einstellungen:** der Key und der Pausenschalter wären für jeden erreichbar.
- **Eigener Pfad (z. B. `/public`) neben `/` mit Login:** zwei Seiten mit gleichem Inhalt; der Nutzer will die
  Übersicht selbst ohne Login öffnen.
- **Fertig gerenderte Seite zwischenspeichern:** Alter („vor 12 s“) und Serverzeit wären bis zu 10 s falsch.
- **Ratenbegrenzung je IP in Caddy:** braucht ein Zusatzmodul; der Zwischenspeicher und die Grenzen aus
  ADR-0024 reichen für eine Seite ohne Schreibzugriff.
- **Ansicht standardmäßig aus:** der Nutzer will sie an; abschalten geht mit einem Klick.

## Konsequenzen
- Positiv: Jeder mit dem Link sieht den Bot ohne Passwort; der Login schützt weiter alles, was etwas ändert.
- Negativ / Preis: Kontostände (virtuell), Signale und GPT-Begründungen sind öffentlich; die Datenschutzerklärung
  sagt nicht mehr „richtet sich nicht an die Öffentlichkeit“; mehr Aufrufe über das Internet.
- Neu bewerten, wenn: Echtgeld kommt (M2), die Seite echte Kontostände oder Daten Dritter zeigen soll oder die Last
  auffällt.

## Code-Anker
- `src/llm_trading_bot/web/app.py` (`_anonymous_overview`, Zwischenspeicher, Schalter, `LIVE_HEADER`),
  `web/overview_page.py` (`public`, `live`), `web/pages.py` (`data-view`), `web/script.py`, `web/overview.py`
  (`SignalCache`), `web/credentials.py` (`WEB_PUBLIC_OVERVIEW`), `web/datenschutz.py` – Spec 019
