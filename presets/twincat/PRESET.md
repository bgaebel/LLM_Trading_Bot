# Preset: twincat

## Passt für

Beckhoff TwinCAT 3 (3.1.4024 / 4026), SPS-Programm in IEC 61131-3 Structured Text, entwickelt in XAE
(TcXaeShell oder Visual Studio). Schwerpunkt: Git-taugliches Projekt, TcUnit-Tests, ehrliche CI-Grenzen.

## Voraussetzungen

Windows 11 (Entwicklungsrechner):
- TwinCAT 3 XAE: Installer von beckhoff.com (Login nötig) bzw. ab 4026 TwinCAT Package Manager
  (`tcpkg`, Paketnamen vor Nutzung mit `tcpkg list` prüfen – hier nicht verifiziert). Version im Team fest
  vereinbaren und in ADR + `Stack:`-Zeile notieren; gemischte XAE-Versionen erzeugen Diff-Rauschen.
- Lokales Testen: TwinCAT-Runtime (XAR) oder ab 4026 User-Mode-Runtime (UmRT). XAR verträgt sich nicht mit
  aktivem Hyper-V/VBS (Core Isolation) – dann UmRT oder separates Testsystem.
- TcUnit: `TcUnit.library` von github.com/tcunit/TcUnit/releases (Stand Erstellung 1.3.1), in XAE über
  *PLC → Library Repository → Install* installieren, im Projekt mit **fester Version** referenzieren.
- Optional TcUnit-Runner 0.9.3.0 (github.com/tcunit/TcUnit-Runner, Repo archiviert) für automatisierte Testläufe.
- Python 3.13 (vorhanden) für `scripts/check-twincat.py` – nur Standardbibliothek.

Linux-CI: nur Python (ubuntu-latest). TwinCAT selbst läuft dort nicht.

## Befehle für CLAUDE.md

Stack: `Beckhoff TwinCAT 3.1.4026 / ST / TcUnit 1.3` (tatsächliche Build-Nummer eintragen)

| Zweck | Befehl |
|---|---|
| Build | `powershell -NoProfile -ExecutionPolicy Bypass -STA -File scripts/build-twincat.ps1 -Solution App.sln` (Automation Interface, lokales XAE; 4024: `-ProgId TcXaeShell.DTE.15.0`). Noch nicht gegen echten Build validiert → bis dahin **manuell in XAE:** *Build → Build Solution* |
| Test (Host) | **manuell in XAE:** Konfiguration auf lokale Runtime (UmRT/XAR, `127.0.0.1.1.1`) aktivieren → Login → Start; Ergebnis in der Error List. Automatisiert nur auf Testsystem: `TcUnit-Runner.exe -v <sln> -t TestTask -a <AmsNetId> -u 5` |
| Lint/Format | `python scripts/check-twincat.py` (kein Standard-Formatter für ST; statische Analyse TE1200 optional manuell in XAE) |
| Flash / Run | **Nutzer in XAE – Claude führt nichts aus:** *Activate Configuration* → Login → Download / Online Change. Claude meldet danach **offen – manuell prüfen**. |
| Monitor / Logs | **Nutzer in XAE – Claude führt nichts aus:** Online-Ansicht, Watch-Listen, Logger-Ausgabe (`ADSLOGSTR`) im Output-Fenster; Claude wertet nur vom Nutzer eingefügte Ausgaben aus. |

Claude darf selbst: `check-twincat.py`, `build-twincat.ps1` (sofern XAE installiert), Lesen/Editieren der
XML-Quellen. Claude darf **nicht**: Konfiguration aktivieren, einloggen, Programme starten, TcUnit-Runner gegen
ein Zielsystem laufen lassen (aktiviert die Konfiguration!). Testergebnisse sind sonst **offen – manuell prüfen**.

## Projektgerüst

TwinCAT-Solution/-Projekt (`.sln`, `.tsproj`, `.plcproj`) in XAE anlegen – nicht aus der Vorlage
generieren. Danach:

```
App.sln
App/App.tsproj
App/<Plc>/<Plc>.plcproj
App/<Plc>/POUs/F_Add.TcPOU          ← geruest/plc/POUs/        (Platzhalter-Anwendung)
App/<Plc>/Test/FB_F_Add_Test.TcPOU  ← geruest/plc/Test/        (TcUnit-Suite)
App/<Plc>/Test/PRG_TEST.TcPOU       ← geruest/plc/Test/        (ruft TcUnit.RUN())
scripts/check-twincat.py            ← geruest/scripts/         (CI-Prüfungen)
scripts/build-twincat.ps1           ← geruest/scripts/         (Headless-Build)
```

POUs in XAE per *Add → Existing Item* einbinden, `TcUnit` referenzieren, eigene Task `TestTask` anlegen, die
`PRG_TEST` aufruft. Git-Einstellungen (Beckhoff-Infosys „Source Control"; vor Projektstart, im Team gleich):
- *Tools → Options → TwinCAT → XAE Environment → File Settings*: **Enable Multiple Files** (XAE-Neustart);
  bestehende Objekte per Kontextmenü **Independent Project File** → eigene `.plcproj`/`.xti` statt alles im `.tsproj`.
- *Tools → Options → TwinCAT → PLC Environment → Write Options*: **Separate LineIDs** = TRUE (→ `LineIDs.dbg`,
  ignoriert), **Sort by name** = TRUE. Ab 4026 alternativ LineIDs abschalten (PLC-Projekteigenschaften →
  *Advanced* → *Write Line IDs*; dann brauchen auch Kommentar-Änderungen einen Online Change).
- Bibliotheken mit fester Version statt `*` (neueste) referenzieren; TwinCAT-Version im Projekt pinnen.

`.TcPOU` sicher bearbeiten (gilt auch für `.TcDUT`, `.TcGVL`, `.TcIO` = Interface):
- ST-Code steht in `<![CDATA[ … ]]>` unter `<Declaration>` bzw. `<Implementation><ST>`. Nur Text innerhalb
  von CDATA ändern; `]]>` darf darin nie vorkommen.
- `Id="{GUID}"` von POU, Methoden, Properties nie ändern oder duplizieren; neue Methode → neue GUID.
- Nicht umformatieren (Einrückung, Attributreihenfolge, CRLF, UTF-8-BOM) – nur minimale Edits.
- `<LineIds>`-Blöcke nicht anfassen; XAE schreibt sie neu.
- Neue Datei: `Name` = Dateiname, neue GUID, in `.plcproj` `<Compile Include="POUs\X.TcPOU">` mit
  `<SubType>Code</SubType>` ergänzen (oder per XAE einbinden lassen). Umbenennen nur in XAE.
- Nach jedem Edit `python scripts/check-twincat.py`; Kompilierbarkeit nur per Build belegt.

## Teststrategie

- **Unit (TcUnit):** Testsuiten als `FB_<Name>_Test EXTENDS TcUnit.FB_TestSuite`, je Test eine Methode mit
  `TEST('…')` … `Assert…` … `TEST_FINISHED()`. Laufen auf einer Runtime (lokal UmRT/XAR), nicht auf dem Host-OS.
  Reine Logik in Funktionen/FBs ohne I/O-Zugriff halten, damit sie testbar bleibt.
- **Integration/HIL:** auf Zielsystem bzw. Testaufbau mit echter I/O; Ablauf in `docs/hardware/` dokumentieren.
- **Manuell:** Aktivieren, Inbetriebnahme, Safety (TwinSAFE), Visualisierung, Motion-Achsen.
- Tests in einem eigenen Ordner + eigener Task; ob sie im Produktivprojekt mitkompiliert werden → ADR.

## CI

`ci.yml` Job `checks` (ubuntu-latest, Option A): führt `scripts/check-twincat.py` aus – XML wohlgeformt,
UTF-8, `Name` = Dateiname, `.plcproj`-Referenzen existieren, keine generierten Dateien (`_Boot`,
`_CompileInfo`, `*.compileinfo`, `*.tclrs`, `*.~u`, `LineIDs.dbg` …) eingecheckt. Kein Cache nötig.
**Grenze:** GitHub-gehostete Runner können TwinCAT weder bauen noch testen (kein XAE, Lizenz/Runtime,
Windows-GUI-Tooling). Grün heißt nur „formal sauber", nicht „kompiliert".
Option B (auskommentierter Job `build-and-test`): Self-hosted Windows-Runner mit XAE, Runtime und
TcUnit-Runner; Build über `build-twincat.ps1`, Tests über TcUnit-Runner, xUnit-XML als Artefakt.
Runner-Dienst braucht interaktive Session bzw. passenden Benutzer für COM/DTE – vorher manuell erproben.

## Typische Fallstricke

### LineIDs erzeugen Diff-Rauschen #twincat #git
*Preset · Quelle: Beckhoff Infosys „Source Control"*
- **Symptom:** Nach jedem Build sind viele `.TcPOU` geändert, ohne Code-Änderung.
- **Ursache:** XAE schreibt `<LineIds>`-Blöcke (Debug-Zuordnung) in jede POU-Datei.
- **Lösung / Regel:** *Write Options → Separate LineIDs* = TRUE im ganzen Team, `LineIDs.dbg` ignorieren; reine LineId-Diffs nicht committen.

### Generierte Dateien eingecheckt #twincat #git
*Preset · Quelle: github/gitignore TwinCAT3*
- **Symptom:** Merge-Konflikte in `_Boot/`, `*.compileinfo`, `*.tmc`; riesige Diffs.
- **Ursache:** Build/Aktivieren erzeugt diese Dateien neu.
- **Lösung / Regel:** Preset-`gitignore` nutzen; `*.tmc` nur committen bei C++/TcCOM-Modulen, geteilten Datentypen oder Events im Standalone-PLC-Projekt.

### Gemischte XAE-Versionen im Team #twincat #toolchain
*Preset · Quelle: Beckhoff-/Community-Erfahrung*
- **Symptom:** Dateien ändern sich beim bloßen Öffnen/Speichern (`ProductVersion`, Attribute), Projekt öffnet in älterer XAE nicht.
- **Ursache:** Jede XAE-Version schreibt ihr Dateiformat; Upgrade erfolgt implizit beim Speichern.
- **Lösung / Regel:** Eine XAE-Build-Nummer pro Projekt (ADR), Upgrade als eigener Commit.

### Bibliotheksversion „*" (neueste) #twincat #libraries
*Preset · Quelle: Beckhoff-/Community-Erfahrung*
- **Symptom:** Gleicher Commit verhält sich auf zwei Rechnern verschieden oder baut nicht.
- **Ursache:** Platzhalter lösen auf die jeweils lokal neueste Bibliothek auf.
- **Lösung / Regel:** Bibliotheken und Platzhalter auf feste Versionen setzen; benötigte `.library`-Versionen dokumentieren.

### TcUnit-Test endet nie #tcunit #test
*Preset · Quelle: TcUnit-Doku*
- **Symptom:** Testlauf meldet kein Gesamtergebnis, TcUnit-Runner läuft in den Timeout.
- **Ursache:** `TEST_FINISHED()` fehlt in einem Test (oder wird bei asynchronem Test nie erreicht).
- **Lösung / Regel:** Jede Testmethode endet mit `TEST_FINISHED()`; zeitabhängige Tests mit Zyklus-Zähler und Abbruchbedingung.

### Runtime startet nicht unter Windows 11 #twincat #runtime
*Preset · Quelle: Beckhoff-/Community-Erfahrung*
- **Symptom:** Aktivieren auf lokalem System scheitert / Runtime bleibt im Config-Mode.
- **Ursache:** Hyper-V, VBS bzw. Core Isolation belegen die Virtualisierung, die XAR benötigt.
- **Lösung / Regel:** Lokal UmRT (ab 4026) oder separates Testsystem nutzen statt Sicherheitsfeatures abzuschalten.

### POU außerhalb von XAE umbenannt #twincat #xml
*Preset · Quelle: Beckhoff-/Community-Erfahrung*
- **Symptom:** Projekt lädt mit fehlenden Objekten, Build findet POU nicht.
- **Ursache:** Dateiname, `Name`-Attribut und `<Compile Include>` in `.plcproj` müssen übereinstimmen.
- **Lösung / Regel:** Umbenennen in XAE; bei Dateisystem-Edits `check-twincat.py` laufen lassen.

### Zugangsdaten (MQTT/OPC UA/Datenbank) im SPS-Quellcode #security #git
*Preset · Quelle: allgemeine Praxis*
- **Symptom:** Passwörter/Tokens stehen als Initialwert in einer GVL oder POU (`sPassword : STRING := '…';`) und landen in der Git-History.
- **Ursache:** Bequemer Weg bei TF6701 (MQTT) & Co.; `.TcGVL`/`.TcPOU` sind normale, committete Quelldateien.
- **Lösung / Regel:** Nie in committeten Dateien. Zugangsdaten zur Laufzeit von einer Datei auf dem Zielsystem
  laden (z. B. Tc2_System `FB_FileOpen`/`FB_FileGets` aus dem Boot-Verzeichnis), deren Vorlage als `*.example`
  im Repo liegt, oder per Inbetriebnahme in `PERSISTENT`-Variablen setzen. Eine gitignorierte GVL taugt nicht –
  `.plcproj` referenziert sie, CI (`check-twincat.py`) und frische Clones brechen. Geleakte Daten sofort rotieren.

## ADR-Kandidaten

- Zugangsdaten: Datei auf dem Zielsystem (+ `.example` im Repo) vs. `PERSISTENT`-Variablen per Inbetriebnahme.
- TwinCAT-Version: 4024 (bewährt, VS2017/2019-Shell) vs. 4026 (VS2022-Shell, Package Manager, UmRT).
- Testprojekt: Tests im Produktivprojekt (eigene Task) vs. separates Test-PLC-Projekt vs. Code als Bibliothek + Testprojekt.
- CI: nur formale Prüfungen (Option A) vs. Self-hosted Runner mit XAE + TcUnit-Runner (Option B).
- `*.tmc` versionieren: ignorieren (reines ST) vs. committen (C++/TcCOM, geteilte Typen, Events).
- Bibliotheksverteilung: `.library` im Repo vs. internes Repository/Package-Feed vs. nur dokumentierte Versionen.
- Namenskonvention: Beckhoff-/PLCopen-Präfixe (`FB_`, `E_`, `ST_`, `n`, `b`) vs. eigene Konvention.

## Anwendung

1. Voraussetzungen prüfen: XAE-Version (`Help → About`), installierte ProgIDs (siehe `build-twincat.ps1`),
   TcUnit installiert, `python --version`. Fehlt etwas: Nutzer nennen, nicht still weitermachen.
2. `ci.yml` → `.github/workflows/ci.yml`.
3. `gitignore` an `.gitignore`, `gitattributes` an `.gitattributes` und `editorconfig` an `.editorconfig`
   anhängen (CRLF für TwinCAT-Dateien, kein Umformatieren durch Editoren).
4. Neues Projekt: Nutzer legt Solution + PLC-Projekt in XAE an (Einstellungen aus „Projektgerüst"), dann
   `geruest/scripts/` → `scripts/`, `geruest/plc/*` in den PLC-Projektordner kopieren und in XAE einbinden.
   Bestehendes Projekt: nur `scripts/` übernehmen, POUs als Referenz.
5. Befehlstabelle und `Stack:`-Zeile in `CLAUDE.md` übernehmen (Solution-Name, Task-Name, ProgID anpassen);
   unter „Projektspezifische Regeln" ergänzen: „Nie Konfiguration aktivieren/einloggen; `.TcPOU` nur innerhalb
   CDATA minimal editieren, GUIDs unverändert."
6. Verifizieren: `python scripts/check-twincat.py` (muss grün sein), `build-twincat.ps1` falls XAE vorhanden;
   TcUnit-Lauf als **offen – manuell prüfen** an den Nutzer geben.
7. Fallstricke nach `docs/LEARNINGS.md` (Bereich „Toolchain & Build"), ADR-Kandidaten mit dem Nutzer klären.
