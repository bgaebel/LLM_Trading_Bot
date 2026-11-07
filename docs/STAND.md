# Stand

**Aktualisiert:** 2026-11-07 · **Branch:** `main` · **Meilenstein:** M1 – Paper-Bot 24/7 auf Hetzner

## Fokus
- **Spec 014 fertig:** Impressum und Datenschutzerklärung live (ADR-0025), Log-Fristen auf dem Server durchgesetzt
  (15 Tage, Firewall ohne Log). Release auf dem Server: 30536d0.
- Spec 013 fertig: Weboberfläche öffentlich per HTTPS (ADR-0024), live seit 2026-11-07.
- Alle anderen M1-Features fertig (Specs 001–012); Bot läuft auf dem Server seit 2026-11-06 22:38 UTC.

## Nächster Schritt
1. Ab 2026-11-13: M1-Abschluss prüfen (7 Tage Lauf seit 2026-11-06 22:38 UTC). Bisher nur die zwei Erstkäufe
   von Buy & Hold: sehr ruhiger Markt (BTC ±0,1 %/Tag), alle Strategien warten auf Bewegung über den Kosten.

## In Arbeit
- Spec: –
- Offene Debug-Sitzung: –

## Blocker / wartet auf
- –

## Offen – manuell prüfen
- Optional in der Hetzner-Oberfläche: Cloud-Firewall; Backups/Snapshot nur nach Anpassung der
  Datenschutzerklärung (Journal mit IP-Adressen, siehe `docs/betrieb/server.md`).
- Ausgabenlimit im OpenAI-Konto setzen. Startkapital für M2 offen.
- Für Spec 014: Postfach `kontakt@<domain>` bei Migadu erreichbar?, AVV mit Hetzner und Migadu prüfen.
