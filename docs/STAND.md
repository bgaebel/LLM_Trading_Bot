# Stand

**Aktualisiert:** 2026-11-09 · **Branch:** `feat/s016-backtest` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 022 live** (Release d64c4fb, 2026-11-09 01:06 UTC, ADR-0032): nur noch USDC-Depots – trend/breakout/
  meanrev/ensemble-usdc, gpt-luna-usdc, gpt-sol-usdc, buyhold-usdc. Euro-Depots aus und ihre Historie in `bot.db`
  gelöscht (Nutzer-Zustimmung, Probelauf = Ausführung, Backup `daily/bot-20261109T010729Z.db`). **M1-Uhr läuft ab
  2026-11-09 01:06 UTC → 2026-11-16 01:06 UTC.** Kopf-/Fußzeile ohne IP (main).
- Spec 021 (Euro-Buy-&-Hold aus) live und durch 022 überholt; AK11 belegt (buyhold nach 1 h ausgeblendet).
- **Spec 016 in Arbeit:** AK5 neu (Treue nur bei belegt gleichem Eingang, freigegeben 2026-11-08) und zwei
  Prüfer-Mängel behoben auf Branch `fix/s016-ak5-fidelity` (72fc4d0, gepusht, alle Checks grün, Treue-Test gegen
  Live-Kopie: 0 ungeklärt, Aktionen 987/987, exakt BTC 55 / ETH 71 < 300).

## Nächster Schritt
1. `fix/s016-ak5-fidelity` in `feat/s016-backtest` mergen, dann `main` (021, 022) einmergen; im Labor
   `BotConfig(..., buyhold=True)` setzen (Stolperdraht-Test) und prüfen, dass die Basisvariante (v0.3.1 Euro) läuft.
2. Basislauf Q2 2021–Q3 2025 (Basis, Kostenraster, Diagnose, usdc), Bericht an den Nutzer.
3. Specs 021/022 im Abschnitt „Ergebnis“ schließen; Mutationsgruppe für 022 nachziehen (in Spec offen).
4. Treue-Test: neue Kopie erst, wenn ≥ 300 exakte Snapshots je Markt möglich sind – jetzt nur noch USDC-Depots.

## In Arbeit
- Spec: 016 (in Arbeit); 017/018 Skizzen
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall; Backups/Snapshot nur nach Anpassung der
  Datenschutzerklärung (Journal mit IP-Adressen, siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
- Beobachtungs-DB wächst ≈ 2–3 MB/Tag: vor 2027-05 Ausdünnung/Archivierung (neues ADR).
- Lizenz des Repos: übernommener Arena-Code steht unter GPL-3.0, das Repo hat keine Lizenzangabe.
- Für Spec 014: Postfach `kontakt@<domain>` bei Migadu erreichbar?, AVV mit Hetzner und Migadu prüfen.
- Gebührenwährung auf BTC/USDC (vermutlich USDC) erst mit Echtgeld bestätigbar.
