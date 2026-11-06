# ADR-0014: Weboberfläche für Zugangsdaten nur auf 127.0.0.1, Passwort-Login, Secrets-Datei außerhalb des Repos

- **Status:** abgelöst durch [ADR-0015](0015-weboberflaeche-passwort-8.md)
- **Löst ab:** –
- **Datum:** 2026-11-06
- **Bereich:** #web #sicherheit #betrieb

## Kontext
Der Nutzer will sich auf einer Webseite einloggen und dort API-Keys und Einstellungen eingeben, statt sie per SSH
in `/etc/llm-trading-bot/env` zu schreiben. Randbedingungen: HTTP nur auf `127.0.0.1`, Zugriff auf dem Server per
SSH-Tunnel (ADR-0005), kein öffentliches Dashboard (PROJEKT.md), Secrets nie in Repo, Logs oder Diffs (CLAUDE.md),
keine Bitvavo-Keys mit Handelsrecht vor M2 (ADR-0004). Einziger Nutzer ist der Betreiber.

## Entscheidung
- Befehl `trading-bot web --data-dir <dir>`: Webserver aus der Standardbibliothek (`http.server`), bindet
  ausschließlich an `127.0.0.1`; jede andere Adresse wird abgelehnt.
- **Ersteinrichtung:** Ist noch kein Passwort gesetzt, druckt der Server beim Start einen einmaligen
  Einrichtungscode ins Terminal. Nur wer diesen Code kennt, kann auf der Seite das Passwort festlegen.
- **Login:** ein Betreiber, Passwort mindestens 12 Zeichen, gespeichert als `scrypt`-Hash mit Salt
  (`web-auth.json`, Rechte 600). Vergleich in konstanter Zeit; nach 5 Fehlversuchen 60 s Sperre.
  Sitzung als zufälliges Cookie (`HttpOnly`, `SameSite=Strict`), 12 h gültig, nur im Speicher.
  Jedes Formular trägt ein CSRF-Token der Sitzung.
- **Zugangsdaten:** `secrets.env` im Datenverzeichnis (Rechte 600, atomar geschrieben). Erlaubte Felder:
  `OPENAI_API_KEY` und die Einstellungen aus Spec 005 (Depots, Budgets, Effort). Gespeicherte Keys werden nie wieder
  vollständig angezeigt (nur die letzten 4 Zeichen); ein leeres Feld lässt den Key unverändert.
  „Verbindung testen" ruft nur die kostenlose Modell-Abfrage von OpenAI auf.
- `paper-cycle` liest `secrets.env` (Option `--secrets`, Standard: neben der DB); gesetzte Umgebungsvariablen
  gehen vor.
- Sicherheits-Header auf jeder Antwort (CSP ohne fremde Quellen und ohne Inline-Skript, `X-Frame-Options: DENY`,
  `Cache-Control: no-store`, `Referrer-Policy: no-referrer`).

## Verworfene Alternativen
- **Öffentlich erreichbare Seite mit TLS:** widerspricht ADR-0005 und den Nicht-Zielen; ein Login allein ist bei
  offenem Port kein ausreichender Schutz für API-Keys.
- **Web-Framework (Flask, FastAPI):** neue Abhängigkeiten für drei Formulare; die Standardbibliothek reicht.
- **Passwort beim ersten Aufruf ohne Code setzen:** wer zuerst die Seite erreicht, hätte den Zugang.
- **Keys in der SQLite-DB:** die DB wird gesichert und weitergegeben (Backups, Nr. 6); Secrets gehören getrennt.
- **Bitvavo-Key-Feld:** ADR-0004 – kein Handelsrecht vor M2.
- **Schreiben nach `/etc/llm-trading-bot/env`:** gehört root; der Dienst läuft ohne Root-Rechte (ADR-0006).

## Konsequenzen
- Positiv: Keys ohne SSH-Dateibearbeitung eingeben; Keys bleiben auf dem Rechner, nur per Tunnel erreichbar.
- Negativ: ein weiterer Prozess mit Zugriff auf die Secrets; unter Windows schützen Dateirechte weniger als 600.
  Sitzungen enden mit einem Neustart des Webservers.
- Neu bewerten, wenn: mehrere Nutzer, Zugriff ohne SSH-Tunnel oder Echtgeld-Keys (M2) dazukommen.

## Code-Anker
- `src/llm_trading_bot/web/` (Spec 006)
