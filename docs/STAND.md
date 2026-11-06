# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Specs 001–008 fertig. Zuletzt abgeschlossen (je mit Fixes aus der Abschlussprüfung): 005 (PR #12), 006 (PR #13),
  007 (Equity-Reihe per Index ausgedünnt, Ladefehler als Meldung), 008 (PR #11).
- Offen in M1: Nr. 6 Deploy auf Hetzner, Nr. 4 Risiko-Grenzen, Nr. 8 Echtgeld-Schnittstelle als Entwurf.

## Nächster Schritt
1. Nr. 6 Deploy auf Hetzner (Spec anlegen; alles auf dem Server nur mit Zustimmung). Dabei entscheiden (Spec 008,
   „Offen / Folgearbeit“): Verhalten bei Uhrsprung (NTP), `RestartPreventExitStatus`/StartLimit für Exit 1 und 3;
   Variablen außerhalb von `secrets.env` per Unit/Drop-in (Spec 006).
2. Danach Nr. 4 Risiko-Grenzen.

## In Arbeit
- Spec: –
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
