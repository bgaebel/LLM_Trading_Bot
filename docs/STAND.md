# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 005 LLM-Strategie: fertig; Fix aus der Abschlussprüfung (kein Handel, wenn der Quote-Abruf nach den
  Antworten scheitert) mit PR #12 gemergt.
- Spec 008 Dienst (ADR-0017): fertig, PR #11 gemergt.
- Spec 006, 007: gemergt (PR #6–#10), alle AKs erfüllt, nur `/abschluss` offen.
- Fertig davor: Spec 004 (PR #4), 003 (PR #3), 001 (PR #2), 002 (PR #1).

## Nächster Schritt
1. `/abschluss 006`, `/abschluss 007`.
2. Dann Nr. 6 Deploy auf Hetzner (nur mit Zustimmung). Dabei entscheiden (Spec 008, „Offen / Folgearbeit“):
   Verhalten bei Uhrsprung (NTP), `RestartPreventExitStatus`/StartLimit für Exit 1 und 3.
3. Danach Nr. 4 Risiko-Grenzen.

## In Arbeit
- Spec: 006, 007 (alle AKs erfüllt, Abschluss offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
