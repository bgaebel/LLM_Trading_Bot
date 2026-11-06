# Stand

**Aktualisiert:** 2026-11-06 · **Branch:** `feat/s005-llm-strategie` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
Spec 005 GPT-Depots: `gpt-luna` (Haupt-Trader) und `gpt-sol` (Gegenprobe) plus Buy & Hold (ADR-0012), eine Anfrage
für BTC+ETH nur bei Bedarf – stündlich, bei ≥ 1,8 % Bewegung oder Signalwechsel, höchstens 4/h (ADR-0013),
Budget 3 €/Tag (luna 0,50 / sol 2,50). Alle Plan-Schritte umgesetzt; unabhängige Prüfung durchgeführt und alle
bestätigten Befunde behoben (Verlauf der Spec). Offen nur AK10 (Live-Test mit echtem Key).
Spec 006 (Weboberfläche für Key und Einstellungen) liegt auf `feat/s006-web`, das auf diesem Branch aufbaut.

## Nächster Schritt
1. PR für 005 (CI), danach 006 auf den neuen Stand bringen und ebenfalls abschließen.
2. Key über die Weboberfläche eintragen (Nutzer), dann AK10: `paper-cycle` zweimal mit echtem Key.

## In Arbeit
- Spec: 005 (in Arbeit, nur AK10 offen), 006 (in Arbeit)
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- OpenAI-Key vom Nutzer für AK10 (später über die Weboberfläche).

## Offen – manuell prüfen
- Spec 005 AK10: zwei echte `paper-cycle`-Läufe mit Key; gemessene Tokens/Kosten in den Verlauf.
- Optional in der Hetzner-Oberfläche: Cloud-Firewall, Backups/Snapshot (siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
