# ADR-0026: Strategie-Labor auf dem PC – unveränderter Live-Code, Prüfregister, versiegelter Prüfzeitraum, GPT nur live

- **Status:** abgelöst durch [ADR-0029](0029-strategie-labor-ausfuehrungsvarianten.md)
- **Löst ab:** –
- **Datum:** 2026-11-07
- **Bereich:** #strategie #auswertung #betrieb

## Kontext
Der Nutzer will die Strategien mit Daten schärfen: historische Kurse einlesen, Live-Signale auswerten,
Verbesserungen ermöglichen (2026-11-07). Die Strategien sind identisch zur Arena v0.3.1 und ändern sich nur als
neue Version (ADR-0007). Die Recherche (Workflow, 2026-11-07) ergab:
- Bitvavo liefert Kerzen 1m/15m/1h/4h kostenlos zurück bis 2019 (BTC-15m erst ab 2021-02-20), ohne Bid/Ask-Historie;
  ein 429 sperrt die IP 15 Minuten – auf dem Server würde das den Bot treffen.
- `paper_cycle` bezieht Zeit und Kurse nur über Uhr und `MarketClient`; ein historischer Client kann den
  unveränderten Live-Code fahren (Probelauf: 320 h ohne Codeänderung).
- Wenige Wochen Live-Betrieb reichen für kein Urteil über eine Strategie; der beste von N getesteten Varianten sieht
  schon zufällig gut aus (Deflated Sharpe Ratio, PBO). Ein Prüfzeitraum, der schon angesehen wurde, prüft nichts mehr.
- GPT lässt sich auf Historie nicht fair testen: Das Modell kennt die Kursverläufe aus dem Training.

## Entscheidung
- **Das Labor läuft auf dem PC, nie auf dem Server.** Es liest öffentliche Bitvavo-Kerzen und nur geprüfte Kopien
  der Bot-Daten (`mode=ro`). Eine solche Kopie ist keine Sicherung im Sinne von ADR-0020 („Backups nur auf dem
  Server“ bleibt); jede Kopie braucht die Zustimmung des Nutzers, ohne Zeitplan. Labordaten liegen außerhalb des Repos
  und außerhalb von Dropbox (Standard `%LOCALAPPDATA%\llm-trading-bot-lab`, unter Linux `~/.local/share/...`).
- **Backtests fahren den unveränderten Live-Pfad** (`paper_cycle` mit historischem `MarketClient`, simulierter Uhr,
  Runde je Minute wie ADR-0017). Ein schnellerer Pfad nur mit Golden-Beleg der Gleichheit. Ein **Treue-Test** gleicht
  den Nachbau mit aufgezeichneten Live-Snapshots ab; ohne ausreichende Vergleichszahl gilt Treue als nicht belegt.
- **Versiegelter Prüfzeitraum:** Alle Daten ab **2025-10-01** werden für Entwicklung und Basisbericht nicht angesehen;
  sie dienen einmal je registriertem Kandidaten als Prüfung. Live-Daten nach dieser Entscheidung sind eine zweite,
  frische Prüfung.
- **Prüfregister:** Jeder Versuch (Konfiguration × Datenfenster × Variante, Wiederholungen zusammengefasst) wird mit
  Code-Commit, Datensatz-Prüfsumme und Tagesrenditen festgehalten; daraus folgt die Zahl der Versuche für
  Mehrfachtest-Korrekturen. Auswertungen, aus denen Kandidaten entstehen, sind Versuche.
- **Neue Strategieversionen** gehen nur über Hürden, die nach dem Basisbericht an einer Stelle (Spec 018) festgelegt
  werden – mit getrennten Stufen für ein Paper-Schatten-Depot und für Echtgeld –, und nur als eigenes Paper-Depot neben
  v0.3.1 nach Freigabe des Nutzers (ADR-0007; ADR-0004 bleibt: kein Echtgeld).
- **GPT wird nur live bewertet**, nie auf historischen Kursen.

## Verworfene Alternativen
- **Labor auf dem Server:** teilt das Rate-Limit mit dem Bot (429 = 15 min Sperre), 2 vCPU neben der Minutenrunde.
- **Eigener, schneller Backtester ohne Live-Code / externe Frameworks:** zweiter Rechenweg, Abweichungen fallen nicht auf.
- **Schwellen anhand der Live-Wochen nachstellen:** zu wenige Daten, eine Marktphase, Überanpassung.
- **Feste statistische Hürden schon jetzt:** ohne Basisbericht weiß niemand, welche Effekte überhaupt messbar sind.
- **GPT auf Historie testen:** Rückschau-Verzerrung durch Trainingsdaten, Kosten, nicht reproduzierbar.

## Konsequenzen
- Positiv: Ergebnisse gelten für genau den Code, der live läuft; Zufallstreffer werden als solche erkannt; der
  versiegelte Zeitraum bleibt eine echte Prüfung.
- Negativ / Preis: Der Live-Pfad ist langsam; Parametersuchen brauchen später einen golden-geprüften Schnellpfad.
  Die letzten 13 Monate sind im Basisbericht nicht zu sehen. Viele Kandidaten werden scheitern – das ist gewollt.
- Neu bewerten, wenn: Echtgeld kommt (M2), oder ein Kandidat den versiegelten Zeitraum verbraucht hat.

## Code-Anker
- geplant: `src/llm_trading_bot/lab/` (Spec 016–018)
