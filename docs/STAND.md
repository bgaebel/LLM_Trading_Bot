# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Fertig und gemergt: Spec 004 Paper-Ausführung + SQLite (PR #4), davor 002 (PR #1), 001 (PR #2), 003 (PR #3).
Spec 005 LLM-Strategie (GPT) als Entwurf geschrieben, ADR-0009 vorgeschlagen. Nutzerentscheidungen vom
2026-11-06: nur GPT über die OpenAI-API, Modell `gpt-6.1-sol`, Arena-Takt (30 min + Ereignis, ≤ 60/Tag),
Budget 3 €/UTC-Tag.

## Nächster Schritt
1. Spec 005 und ADR-0009 durchsehen und freigeben. Zu bestätigen: `openai`-SDK als neue Abhängigkeit,
   Budget/Provenienz in der DB (Schema v2), `max_output_tokens` 8000 statt 1500 (Arena), Preis-Standard 2/10 € je 1 Mio.
2. Danach `/umsetzen 005`; für AK10 wird ein OpenAI-Key lokal in der Shell gebraucht.
3. Später Nr. 4 Risiko inkl. 10-%-Drawdown-Stopp, Nr. 5 Dienst (Quotes nur einmal je Zyklus holen).

## In Arbeit
- Spec: 005 (Entwurf, wartet auf Freigabe)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- Freigabe von Spec 005 / ADR-0009 durch den Nutzer.

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall als zweite Schicht, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- OpenAI-Key erst bei Bedarf in `/etc/llm-trading-bot/env` auf dem Server (Datei existiert leer, root, 600);
  zusätzlich ein Ausgabenlimit im OpenAI-Konto setzen.
- Offene Projektentscheidung: Startkapital für M2.
