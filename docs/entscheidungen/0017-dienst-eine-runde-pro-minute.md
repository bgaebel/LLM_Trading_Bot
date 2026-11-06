# ADR-0017: Dienst als ein Prozess mit einer Paper-Runde pro Minute, systemd-Watchdog und Statusdatei

- **Status:** angenommen
- **Datum:** 2026-11-06
- **Bereich:** #betrieb #dienst #persistenz

## Kontext
Bisher läuft ein Paper-Zyklus nur von Hand (`trading-bot paper-cycle`). Für M1 muss der Bot 24/7 als Dienst unter
systemd laufen (ADR-0005), Ausfälle erkennbar machen und sauber stoppen. Die Arena nutzt drei Threads (Kurse 5 s,
Strategie 60 s, GPT 20 s), einen HTTP-Health-Endpunkt und `Type=simple`. Der Bot hat dagegen einen fertigen,
getesteten Zyklus (`paper_cycle`), der Kurse, Buy & Hold, klassische Depots, GPT (parallel, nur bei Bedarf) und die
Bewertung in einer Runde erledigt. Der Nutzer hat am 2026-11-06 nach Abwägung gewählt: ein Takt alle 60 s,
Watchdog plus Statusdatei, Einstellungen jede Runde neu lesen, systemd-Dateien jetzt anlegen und erst mit Nr. 6
installieren.

## Entscheidung
- **Ein Prozess, ein Schreiber:** `trading-bot run --db <pfad>` hält den `WriterLock` für seine ganze Laufzeit und
  führt jede Minute eine Runde `paper_cycle` aus, 5 s nach dem Minutenwechsel (die 1-Minuten-Kerze ist dann
  abgeschlossen). Dauert eine Runde länger, beginnt die nächste am nächsten freien Termin – kein Nachholen.
- **Konfiguration je Runde:** Umgebung + `secrets.env` werden jede Runde neu gelesen (Umgebung gewinnt). Ungültige
  Werte: letzte gültige Konfiguration bleibt, eine Warnung wird geloggt. Neue Depots bekommen ihr Startkapital;
  entfernte Depots behalten ihre Historie.
- **Fehler:** Ein Fehler in einer Runde wird geloggt und als „degraded“ gemeldet, die nächste Runde läuft normal
  (fail closed: ohne frische Kurse keine Entscheidung). Eine falsche Schemaversion beendet den Dienst (Exit 1). Eine
  Runde über 300 s gilt als hängend: Exit 4, systemd startet neu.
- **Stopp:** SIGTERM/SIGINT beenden die Schleife; eine laufende Runde fragt GPT nicht mehr neu an, eine schon
  laufende Anfrage darf höchstens bis zur Frist von 75 s dauern, danach endet der Prozess hart. Das ist sicher, weil
  jeder Schreibvorgang eine Transaktion ist und eine unbeendete GPT-Anfrage ihre Reservierung im Budget behält.
- **Health:** systemd `Type=notify` mit `READY`, `WATCHDOG` (aus der Hauptschleife), `STOPPING` und `STATUS` über
  `NOTIFY_SOCKET` (ohne Fremdpaket; ohne systemd wirkungslos). Dazu eine Statusdatei `service-status.json` neben
  der DB (atomar, 600, ohne Secrets), die die Weboberfläche als Lebenszeichen „Dienst“ zeigt.
- **Logs:** eine Zeile je Runde plus Trades und GPT-Ergebnisse auf stdout (journald), Stufe per `LOG_LEVEL`; Keys
  werden vor jeder Ausgabe unkenntlich gemacht.
- **systemd-Dateien** (`deploy/`, mit Platzhaltern, in der CI per `systemd-analyze verify` geprüft): Dienst-Unit mit
  `Restart=on-failure`, `WatchdogSec`, `TimeoutStopSec`, Härtung (`ProtectSystem=strict`, `ReadWritePaths` nur das
  Datenverzeichnis, `NoNewPrivileges`, `UMask=0077`), Unit für die Weboberfläche, Journal-Limit. Installation erst mit
  Roadmap Nr. 6 und Zustimmung des Nutzers.

## Verworfene Alternativen
- **Arena-Modell mit drei Threads:** frischere Kurse und GPT entkoppelt, aber Zerlegung des getesteten Zyklus und
  mehrere schreibende Threads; vom Nutzer verworfen. Ein schneller Kurs-/Risiko-Worker kommt gezielt mit Nr. 4.
- **HTTP-Health im Dienst (wie Arena):** zweiter Webserver neben der Weboberfläche; vom Nutzer verworfen.
- **systemd-Timer, der jede Minute `paper-cycle` startet:** einfach, aber jede Minute Prozessstart und neuer Lock,
  kein Zustand zwischen Runden (Config-Warnungen, Fehlerzähler), und Nr. 4 braucht einen dauerhaften Prozess.
- **Konfiguration nur beim Start:** Änderungen aus der Weboberfläche würden erst nach einem Neustart greifen.

## Konsequenzen
- Positiv: wenig neuer Code um einen bewährten Zyklus; ein Schreiber; Hänger und Abstürze führen zu Neustart;
  Zustand ohne SSH sichtbar.
- Negativ: Kurse werden nur einmal pro Minute aktualisiert; eine langsame GPT-Antwort verzögert die Runde (nicht
  die Kurse anderer Runden). Neu bewerten mit Nr. 4 (Drawdown-Stopp braucht häufigere Kurse).
- Neu bewerten, wenn: Risiko-Grenzen (Nr. 4) oder Echtgeld (M2) dazukommen, oder Runden regelmäßig länger als 60 s
  dauern.

## Code-Anker
- `src/llm_trading_bot/service/` (geplant, Spec 008), `deploy/llm-trading-bot.service`
