# ADR-0024: Weboberfläche öffentlich per HTTPS hinter Caddy; App bleibt auf 127.0.0.1

- **Status:** abgelöst durch [ADR-0028](0028-uebersicht-ohne-login.md)
- **Löst ab:** [ADR-0005](0005-betrieb-hetzner-cloud.md)
- **Datum:** 2026-11-07
- **Bereich:** #betrieb #web #sicherheit

## Kontext
ADR-0005 legte fest: Betrieb auf Hetzner Cloud CX23 mit Ubuntu und systemd, Status nur per SSH-Tunnel. Der Nutzer
will die Weboberfläche ohne Tunnel von jedem Gerät öffnen, öffentlich mit Login (2026-11-07); seine Domain bei
Hetzner zeigt schon auf den Server. Auf der Seite liegen der OpenAI-Key (maskiert), Einstellungen, der
Pausenschalter und die Freigabe gestoppter Depots – Echtgeld gibt es nicht (ADR-0004).

## Entscheidung
- **Betrieb unverändert:** Hetzner Cloud CX23, Ubuntu, systemd (aus ADR-0005).
- **Von außen nur über Caddy** (offizielles, signiertes apt-Repo, in den automatischen Updates; Admin-API nur als
  Unix-Socket; HTTP/1.1 und HTTP/2): HTTPS mit Let's-Encrypt-Zertifikat, Port 80 nur für Zertifikat und
  Weiterleitung, `www` → Domain, HSTS, Grenzen für Zeit, Größe und Verbindungen, kein Zugriffslog. Die App bindet
  weiter nur an `127.0.0.1`: Port 8787 für den Tunnel (wie heute), Port 8788 nur für Caddy. Der Zugangsweg wird am
  Socket erkannt, nicht an Headern.
- **Die App kennt genau einen öffentlichen Host** (Server-Konfiguration, nie im Repo).
- **Härtung des öffentlichen Wegs:** Fehlversuchssperre je Quell-IP mit Verdopplung bis 1 h und globaler
  Obergrenze; ein signiertes Geräte-Cookie nimmt das Gerät des Besitzers davon aus; der Tunnel hat einen eigenen
  Zähler. `__Host-`-Cookies mit `Secure`. Keine Einrichtung über das Internet. Öffentlicher Login nur mit einem
  Passwort von mindestens 12 Zeichen, das nicht zu den häufigen gehört (ergänzt ADR-0015; per Tunnel gelten weiter
  8 Zeichen). Neu: Passwort ändern in den Einstellungen.

## Verworfene Alternativen
- **Privates Netz (Tailscale):** sicherer, aber der Nutzer will ohne App auf jedem Gerät zugreifen.
- **Nur die Übersicht öffentlich:** vom Nutzer nicht gewählt.
- **App direkt auf 0.0.0.0 mit eigenem TLS; nginx + certbot; Cloudflare-Tunnel:** mehr Eigenbau, zwei Werkzeuge
  bzw. ein Drittanbieter-Konto.
- **Ubuntus Caddy-Paket:** universe, keine verlässlichen Sicherheitsupdates für den einzigen Dienst im Internet.
- **Globale Fehlversuchssperre:** ein Angreifer könnte den Besitzer dauerhaft aussperren.
- **Zwei-Faktor-Anmeldung:** vorerst nicht nötig, Kandidat für M2.

## Konsequenzen
- Positiv: Zugriff ohne Tunnel; Zertifikate und Caddy-Updates laufen von selbst.
- Negativ / Preis: Die Login-Seite ist öffentlich und wird angegriffen; ein zusätzlicher Dienst; das Nicht-Ziel
  „kein öffentlich erreichbares Dashboard“ in PROJEKT fällt; ein kurzes Passwort muss geändert werden.
- Neu bewerten, wenn: Echtgeld kommt (M2: Zwei-Faktor, Freigaben nur per Tunnel?) oder Angriffe auffallen.

## Code-Anker
- `src/llm_trading_bot/web/app.py`, `web/auth.py`, `deploy/Caddyfile`, `deploy/release.sh`, `scripts/deploy.sh`
  – geplant, Spec 013
