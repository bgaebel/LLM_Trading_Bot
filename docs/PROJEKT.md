# Projekt: LLM Trading Bot

<!-- Das "Warum" und "Wofür". Ändert sich selten und nur mit dem Nutzer.
     Wird nicht in jede Session geladen – CLAUDE.md verlinkt hierher. -->

## Ziel
Ein dauerhaft laufender Trading-Bot für BTC/EUR und ETH/EUR (BTC zusätzlich über BTC/USDC ausgeführt, ADR-0030), der auf der
[LLM Trading Arena](https://github.com/bgaebel/LLM-Trading-Arena) aufbaut. Die Arena vergleicht Strategien
(klassisch, GPT, Claude, Buy & Hold) als Forschungsaufbau. Der Bot setzt eine dort untersuchte Strategie
als schlanken, betriebsfesten Dienst auf einem Hetzner-Cloud-Server um. Er läuft zuerst im Paper-Modus
und soll später, nach eigener Freigabe, mit echtem Geld über Bitvavo handeln.

## Kernwert
Keine ungewollte echte Order und keine verfälschte Handelshistorie – im Zweifel handelt der Bot nicht.

## Erfolgskriterien
- [ ] Bot läuft 24/7 auf dem Hetzner-Server; Neustart/Reboot erzeugt keine doppelten Orders, Historie bleibt unverändert.
- [ ] Jede Entscheidung ist nachvollziehbar gespeichert (Strategie-/Modellversion, Datenzeitstempel, Grund, Kostenannahmen).
- [ ] Ohne Quellcodeeinsicht erkennbar: läuft der Bot, wann kam der letzte gültige Kurs, warum handelt er gerade (nicht).
- [ ] Paper-Ergebnis nach Gebühren/Slippage gegen Buy & Hold ausgewiesen.
- [ ] Echtgeld-Betrieb erst nach eigener Spec, eigenem ADR und ausdrücklicher Freigabe – mit harten Limits und Not-Aus.

## Nicht-Ziele
- Keine Echtgeld-Orders vor dem Echtgeld-Meilenstein (siehe ADR-0004).
- Keine weiteren Börsen, Hebel, Margin oder Derivate in v1; als weitere Märkte nur BTC/USDC und USDC/EUR für die
  Ausführung der USDC-Depots (ADR-0030, Nutzer 2026-11-08).
- Keine Bedienung ohne Login: von außen nur per HTTPS oder SSH-Tunnel; ohne Passwort nur die Übersicht lesend,
  solange alles Paper ist (ADR-0028, Nutzer 2026-11-08).
- Kein Hochfrequenzhandel; höhere Signalfrequenz ist kein Ziel an sich.
- Keine Änderungen am Arena-Repository aus diesem Projekt heraus; die Arena bleibt eigenständig.
- Keine Gewinnbehauptungen; Kennzahlen nur aus echten Marktdaten und gekennzeichneten Orders.

## Randbedingungen
- **Hardware / Plattform:** Hetzner Cloud Server CX23 (x86-64), Ubuntu (Version bei Einrichtung festhalten; geplant 24.04 LTS), systemd.
- **Werkzeuge / Versionen:** Python 3.12 (System-Python von Ubuntu 24.04), uv, pytest, ruff, mypy strict.
- **Extern vorgegeben:** Bitvavo-REST-API (öffentliche Marktdaten; Trading-API erst ab Echtgeld-Meilenstein),
  OpenAI-API für die LLM-Strategie (Anthropic/Claude vom Nutzer am 2026-11-06 verworfen, ADR-0012).
- **Ressourcen:** Serverkosten CX23; LLM-API-Budget 3 € pro UTC-Tag für alle GPT-Depots zusammen (Nutzer, 2026-11-06;
  ADR-0013, Anteile ADR-0012).
  Startkapital für den Echtgeld-Betrieb – noch festzulegen.

## Beteiligte & Umfeld
- Nutzer/Betreiber: Björn (Solo-Projekt).
- Nachbarsystem: LLM Trading Arena (Quelle übernommener Module, liefert Strategievergleiche).
- Externe Dienste: Bitvavo, OpenAI, Hetzner Cloud.

## Glossar
| Begriff | Bedeutung |
|---|---|
| Arena | Das Forschungsprojekt LLM-Trading-Arena, aus dem Module übernommen werden |
| Paper-Modus | Virtuelle Ausführung mit echten Kursen, Gebühren und Slippage – keine echte Order |
| Live-Modus | Echte Orders über die Bitvavo-Trading-API (erst ab Echtgeld-Meilenstein) |
| Fail closed | Bei fehlenden/veralteten Daten oder ungültigen Antworten keine neue Order |
| Abgeschlossene Kerze | OHLCV-Kerze, deren Intervall vorbei ist; nur diese fließen in Signale ein |
