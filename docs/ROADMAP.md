# Roadmap

<!-- Meilensteine in Reihenfolge, jeder mit prüfbarem "Fertig, wenn".
     Hier nur Zuordnung und Reihenfolge. Der Status eines Features steht ausschließlich in seiner Spec
     (keine zweite Statusspalte, die auseinanderläuft). Meilenstein-Symbol: ⬜ offen · 🔄 in Arbeit · ✅ fertig · ⏸ zurückgestellt
     Abgeschlossene Meilensteine in <details><summary>M1 – Name ✅</summary> … </details> einklappen. -->

## M1 – Paper-Bot 24/7 auf Hetzner 🔄
**Ziel:** Eine aus der Arena übernommene Strategie handelt BTC/EUR und ETH/EUR im Paper-Modus auf dem
Hetzner-Server; Status ist per SSH-Tunnel sichtbar.
**Fertig, wenn:** Der Bot läuft ≥ 7 Tage ununterbrochen auf dem CX23 (gezählt ab dem letzten Deploy, Nutzer
2026-11-08), übersteht einen Reboot ohne doppelte
Orders und mit unveränderter Historie, Backups sind wiederhergestellt geprüft, CI ist grün.

| # | Feature / Aufgabe | Spec | Issue |
|---|---|---|---|
| 1 | Marktdaten aus der Arena übernehmen (Bitvavo-Quotes, abgeschlossene Kerzen, Lückenbehandlung) | [001](specs/001-marktdaten.md) | – |
| 2 | Indikatoren + klassische Strategien + LLM-Kontext aus der Arena (identisch zu v0.3.1) | [003](specs/003-strategien.md) | – |
| 3 | Paper-Ausführung + SQLite-Persistenz (Gebühren, Slippage, Idempotenz, Single-Writer) | [004](specs/004-paper-ausfuehrung.md) | – |
| 3a | LLM-Strategie (GPT): Depots gpt-6-luna (Haupt-Trader) und gpt-6.1-sol (Gegenprobe) plus Buy & Hold; strenges JSON-Schema, Budgetsperre, Provenienz (ADR-0012/0013) | [005](specs/005-llm-strategie.md) | – |
| 4 | Risiko-Grenzen und Fail-closed (veraltete Kurse, Feed-/LLM-Ausfall, BTC/ETH entkoppelt) | [011](specs/011-risiko-grenzen.md) | – |
| 5 | Dienst: Worker-Schleife, sauberer Shutdown, Health, Logging | [008](specs/008-dienst.md) | – |
| 6 | Hetzner-Server: Härtung (SSH-Key, Firewall, Updates), systemd, Backup, Update/Rollback | [002](specs/002-server-grundeinrichtung.md) (Härtung), [009](specs/009-deploy.md) (Deploy, Backup) | – |
| 7 | Status-Ansicht nur auf 127.0.0.1, Zugriff per SSH-Tunnel; zuerst Login + Zugangsdaten, dann Übersicht; öffentlich per HTTPS mit Login | [006](specs/006-weboberflaeche.md) (Login, Zugangsdaten), [007](specs/007-uebersicht.md) (Übersicht), [010](specs/010-live-uebersicht.md) (live), [013](specs/013-oeffentlich-https.md) (öffentlich), [014](specs/014-rechtstexte.md) (Impressum, Datenschutz), [019](specs/019-uebersicht-ohne-login.md) (Übersicht ohne Login) | – |
| 8 | Echtgeld-Schnittstelle als Entwurf (Interface + ADR-Vorschlag, keine Implementierung) | [012](specs/012-broker-naht.md) | – |
| 9 | USDC-Depots: BTC über BTC/USDC ausgeführt, Geld in Euro (ADR-0030) | [020](specs/020-usdc-depots.md) | – |

## M2 – Echtgeld-Minimalbetrieb ⬜
**Ziel:** Echte Orders über die Bitvavo-Trading-API mit Kleinstbetrag, harten Limits (Positionsgröße,
Tagesverlust), Not-Aus und Abgleich mit dem Kontostand. Start erst nach angenommenem ADR und ausdrücklicher Freigabe.

## M3 – Auswertung und Ausbau ⬜
**Ziel:** Performance-Bericht gegen Buy & Hold, Benachrichtigung bei Ausfall, weitere Strategien aus der Arena.

| # | Feature / Aufgabe | Spec | Issue |
|---|---|---|---|
| L1 | Strategie-Labor I: Live-Aufzeichnung in der Beobachtungs-DB (zuerst: geht täglich verloren) | [015](specs/015-live-aufzeichnung.md) | – |
| L2 | Strategie-Labor II: Kerzenarchiv, Backtest mit dem Live-Code, Basisbericht v0.3.1 | [016](specs/016-backtest-baseline.md) | – |
| L3 | Strategie-Labor III: Auswertung der Live-Signale (ab ≥ 4 Wochen Aufzeichnung) | [017](specs/017-signal-auswertung.md) | – |
| L4 | Strategie-Labor IV: Kandidaten, Prüfhürden, Schatten-Depots | [018](specs/018-strategie-kandidaten.md) | – |

## Ideen / später
- Externe Backups auf eine Hetzner Storage Box.
- WebSocket-Feed statt REST-Polling (mit REST-Recovery), falls die Arena das validiert.
- Täglicher Report per Mail/Messenger.
