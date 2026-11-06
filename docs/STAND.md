# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `feat/s005-llm-strategie` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Spec 005 LLM-Strategie überarbeitet nach Nutzer-Umsteuerung: drei GPT-Depots (`gpt-6.1-sol`, `gpt-6.1-terra`,
`gpt-6-luna`) plus Buy & Hold, BTC+ETH in einer Anfrage mit Prompt v1, Takt 4/h + ≤ 4 Ereignis/h, Effort `low`,
Budget 3 €/Tag gesamt (sol 2,00 / terra 0,90 / luna 0,10). ADR-0010 und ADR-0011 vorgeschlagen (lösen 0008/0009 ab).
Schon umgesetzt und bleibt: Schema v2 mit `llm_calls`, Budget-Reservierung/Abrechnung.
Fertig davor: Spec 004 (PR #4), 003 (PR #3), 001 (PR #2), 002 (PR #1).

## Nächster Schritt
1. Überarbeitete Spec 005 und ADR-0010/0011 freigeben.
2. Danach weiter mit Plan-Schritt 3 (Prompt v1, Prüfung, Takt je Depot, Budget je Depot).
3. Für AK10 wird ein OpenAI-Key lokal in der Shell gebraucht.

## In Arbeit
- Spec: 005 (Entwurf, überarbeitet; Schritte 1–2 erledigt)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- Freigabe der überarbeiteten Spec 005 und von ADR-0010/0011.

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- OpenAI-Key erst bei Bedarf in `/etc/llm-trading-bot/env` auf dem Server (Datei existiert leer, root, 600);
  zusätzlich ein Ausgabenlimit im OpenAI-Konto setzen.
- Offene Projektentscheidung: Startkapital für M2.
