# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- Spec 005 GPT-Depots (`gpt-luna` Haupt, `gpt-sol` Gegenprobe, ADR-0012/0013) und Spec 006 Weboberfläche
  (Login, Key, Depots, Budget; ADR-0015; dunkles Terminal-Design) sind gemergt (PR #5, PR #6).
  Beide bleiben „in Arbeit", bis ihre manuellen Abnahmen erledigt sind.
- Fertig davor: Spec 004 (PR #4), 003 (PR #3), 001 (PR #2), 002 (PR #1).

## Nächster Schritt
1. Nutzer: an der Weboberfläche anmelden (`trading-bot web`, Passwort ist gesetzt), OpenAI-Key eintragen,
   „Verbindung testen", abmelden → 006 AK9.
2. Danach 005 AK10: `paper-cycle` zweimal mit echtem Key; Tokens/Kosten in den Verlauf; beide Specs abschließen.
3. Dann nächste M1-Spec laut `docs/ROADMAP.md` (Dauerbetrieb/Deploy auf Hetzner).

## In Arbeit
- Spec: 005 (nur AK10 offen), 006 (nur AK9 offen)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- OpenAI-Key vom Nutzer (über die Weboberfläche).

## Offen – manuell prüfen
- Spec 006 AK9: Login, Key, Verbindungstest, Logout im Browser.
- Spec 005 AK10: zwei echte `paper-cycle`-Läufe mit Key; gemessene Tokens/Kosten in den Verlauf.
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
