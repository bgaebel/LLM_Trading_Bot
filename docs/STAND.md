# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `feat/s008-dienst` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 008 Dienst (ADR-0017): alle 6 Plan-Schritte umgesetzt, PR #11 offen. `trading-bot run` – eine Paper-Runde
  pro Minute, Config-Reload, Stopp mit Frist, systemd-Watchdog, Statusdatei, Logging, Units in `deploy/`.
  Offen: CI grün (Unit-Check und Unix-Socket-Tests laufen nur dort), AK11 manuell beim Nutzer.
- Spec 005, 006, 007: gemergt (PR #5–#10), alle AKs erfüllt, nur `/abschluss` offen.
- Fertig davor: Spec 004 (PR #4), 003 (PR #3), 001 (PR #2), 002 (PR #1).

## Nächster Schritt
1. AK11 manuell: `uv run trading-bot run --db <pfad>/bot.db` ≥ 5 Runden, Übersicht „Dienst läuft“, Strg+C →
   Status `stopped`. Dann PR #11 mergen und `/abschluss 008`.
2. `/abschluss 005`, `/abschluss 006`, `/abschluss 007` (Nutzer startet).
3. Dann Nr. 6 Deploy auf Hetzner (nur mit Zustimmung).

## In Arbeit
- Spec: 008 (in Arbeit, nur AK11 + CI offen); 005, 006, 007 (alle AKs erfüllt, Abschluss offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
