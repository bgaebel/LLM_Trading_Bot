# Roadmap

<!-- Meilensteine in Reihenfolge, jeder mit prüfbarem "Fertig, wenn".
     Hier nur Zuordnung und Reihenfolge. Der Status eines Features steht ausschließlich in seiner Spec
     (keine zweite Statusspalte, die auseinanderläuft). Meilenstein-Symbol: ⬜ offen · 🔄 in Arbeit · ✅ fertig · ⏸ zurückgestellt
     Abgeschlossene Meilensteine in <details><summary>M1 – Name ✅</summary> … </details> einklappen. -->

## M1 – Paper-Bot 24/7 auf Hetzner 🔄
**Ziel:** Eine aus der Arena übernommene Strategie handelt BTC/EUR und ETH/EUR im Paper-Modus auf dem
Hetzner-Server; Status ist per SSH-Tunnel sichtbar.
**Fertig, wenn:** Der Bot läuft ≥ 7 Tage ununterbrochen auf dem CX23, übersteht einen Reboot ohne doppelte
Orders und mit unveränderter Historie, Backups sind wiederhergestellt geprüft, CI ist grün.

| # | Feature / Aufgabe | Spec | Issue |
|---|---|---|---|
| 1 | Marktdaten aus der Arena übernehmen (Bitvavo-Quotes, abgeschlossene Kerzen, Lückenbehandlung) | [001](specs/001-marktdaten.md) | – |
| 2 | Strategie wählen und übernehmen (klassisch oder LLM – Entscheidung anhand Arena-Ergebnissen) | – | – |
| 3 | Paper-Ausführung + SQLite-Persistenz (Gebühren, Slippage, Idempotenz, Single-Writer) | – | – |
| 4 | Risiko-Grenzen und Fail-closed (veraltete Kurse, Feed-/LLM-Ausfall, BTC/ETH entkoppelt) | – | – |
| 5 | Dienst: Worker-Schleife, sauberer Shutdown, Health, Logging | – | – |
| 6 | Hetzner-Server: Härtung (SSH-Key, Firewall, Updates), systemd, Backup, Update/Rollback | [002](specs/002-server-grundeinrichtung.md) (Härtung) | – |
| 7 | Status-Ansicht nur auf 127.0.0.1, Zugriff per SSH-Tunnel | – | – |
| 8 | Echtgeld-Schnittstelle als Entwurf (Interface + ADR-Vorschlag, keine Implementierung) | – | – |

## M2 – Echtgeld-Minimalbetrieb ⬜
**Ziel:** Echte Orders über die Bitvavo-Trading-API mit Kleinstbetrag, harten Limits (Positionsgröße,
Tagesverlust), Not-Aus und Abgleich mit dem Kontostand. Start erst nach angenommenem ADR und ausdrücklicher Freigabe.

## M3 – Auswertung und Ausbau ⬜
**Ziel:** Performance-Bericht gegen Buy & Hold, Benachrichtigung bei Ausfall, weitere Strategien aus der Arena.

## Ideen / später
- Externe Backups auf eine Hetzner Storage Box.
- WebSocket-Feed statt REST-Polling (mit REST-Recovery), falls die Arena das validiert.
- Täglicher Report per Mail/Messenger.
