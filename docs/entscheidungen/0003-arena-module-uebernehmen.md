# ADR-0003: Arena-Module mit Herkunftsvermerk übernehmen statt die Arena einzubinden

- **Status:** angenommen
- **Löst ab:** –
- **Datum:** 2026-11-05
- **Bereich:** #arena #architektur

## Kontext
Die [LLM Trading Arena](https://github.com/bgaebel/LLM-Trading-Arena) (Stand `7563724`, 2026-11-05) enthält
erprobte Bausteine: Bitvavo-Client (`bitvavo.py`), Strategien (`strategies.py`), Signalzyklen (`engine03.py`),
SQLite-/Risiko-/LLM-Logik (`arena.py`), Budget, Backup und Betrieb unter systemd. Sie ist ein Forschungsaufbau
mit eigenen Regeln (nur Paper, Experimentversionierung) und läuft unabhängig weiter.

## Entscheidung
- Benötigte Module werden **per Spec einzeln** in dieses Repo übernommen, an ADR-0002 angepasst (Typen, pytest)
  und mit Tests abgesichert.
- Jede übernommene Datei bekommt im Kopf einen Herkunftsvermerk:
  `# Origin: LLM-Trading-Arena@<commit> apps/arena/<datei> (adapted)`.
- Keine Laufzeit- oder Build-Abhängigkeit zur Arena. Die Arena wird aus diesem Projekt nicht verändert.
- Übernommen wird auch das Marktmodell der Arena: Bitvavo, BTC/EUR und ETH/EUR, nur abgeschlossene Kerzen,
  Fail-closed bei fehlenden/veralteten Kursen, Gebühren und Slippage explizit.

## Verworfene Alternativen
- **Arena als git-Submodul/Git-Paket** – flache Imports aus `apps/arena/` sind nicht als Paket nutzbar;
  jede Arena-Änderung würde den Bot ungeprüft mitändern.
- **Arena hierher migrieren und ersetzen** – vermischt Forschung und Betrieb; die Arena soll weiter Strategien vergleichen.
- **Neuentwicklung ohne Arena-Code** – verwirft erprobte und getestete Logik.

## Konsequenzen
- Positiv: Bot bleibt schlank und stabil; übernommene Teile sind nachvollziehbar und einzeln geprüft.
- Negativ: Fixes in der Arena werden nicht automatisch übernommen – bei Bedarf gezielt nachziehen (Herkunftsvermerk zeigt den Stand).
- Neu bewerten, wenn: beide Projekte dauerhaft dieselben Module brauchen – dann gemeinsames Paket erwägen.

## Code-Anker
- Kopfkommentare `# Origin: LLM-Trading-Arena@…` in übernommenen Dateien
