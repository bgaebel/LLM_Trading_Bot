# Preset: esp-idf

## Passt für

Firmware für ESP32-Familie (ESP32, -S3, -C3, -C6 …) mit Espressif ESP-IDF v5.x, C, Build über `idf.py` (CMake/Ninja).
Nicht für Arduino-Core oder PlatformIO-Projekte.

## Voraussetzungen

Windows 11 (PowerShell; cmd geht mit `export.bat`, Git Bash wird von Espressif nicht unterstützt):
- ESP-IDF über den Installation Manager (EIM): `winget install Espressif.EIM` (GUI) oder `winget install Espressif.EIM-CLI`,
  dann `eim install -i v5.5.6`. Version pinnen, nicht „latest". Pfad ohne Leerzeichen, kurz (z. B. `C:\esp`).
- Umgebung je Terminal aktivieren: Desktop-Verknüpfung „IDF_PowerShell" oder das Aktivierungsskript, dessen Pfad
  `eim select` anzeigt. Klassisch (Git-Checkout von IDF): `. C:\esp\esp-idf\export.ps1`. Ohne Aktivierung kein `idf.py`.
- USB-Treiber je nach Board (CP210x / CH34x); am nativen USB-Serial-JTAG-Port (S3/C3/C6) keiner.
- Host-Tests: Docker Desktop (oder WSL2 mit Linux-IDF), da das IDF-Linux-Target unter Windows nicht läuft.
- Formatter: `pip install clang-format==18.1.8` außerhalb der IDF-Umgebung (sonst im IDF-venv); Version wie in CI.

Linux-CI: Docker-Image `espressif/idf:v5.5.6` (enthält Toolchains aller Targets, ccache, build-essential für Linux-Target).

## Befehle für CLAUDE.md

Stack: `ESP32 / ESP-IDF v5.5.6 / C (idf.py, CMake)` – Chip und Version anpassen.

Claudes Bash-Tool ist Git Bash, jeder Aufruf eine frische Shell ohne IDF-Aktivierung. Deshalb laufen alle
`idf.py`-Aufrufe über `scripts/idf.ps1`: Es aktiviert ESP-IDF selbst (EIM-Auswahl aus `eim_idf.json`, sonst
`$env:IDF_PATH\export.ps1`, Override `IDF_ACTIVATE_SCRIPT`), ruft `idf.py` mit allen Argumenten auf und gibt
dessen Exit-Code zurück. Befehle aus dem Projekt-Root, Port (`COM5`) anpassen.

| Zweck | Befehl |
|---|---|
| Build | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/idf.ps1 build` (einmalig vorher: `… scripts/idf.ps1 set-target esp32`) |
| Test (Host) | `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/project" -w /project/test/host espressif/idf:v5.5.6 bash -c "idf.py --preview set-target linux && idf.py build && ./build/host_test.elf"` (Git Bash; `MSYS_NO_PATHCONV=1` verhindert, dass `/project` zu einem Windows-Pfad umgeschrieben wird) |
| Lint/Format | `clang-format --dry-run --Werror $(git ls-files '*.c' '*.h')` (Fix: `-i` statt `--dry-run --Werror`) |
| Flash / Run | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/idf.ps1 -p COM5 flash` – nur mit angeschlossenem Board und nach Zustimmung des Nutzers |
| Monitor / Logs | **Nutzer im eigenen Terminal:** `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/idf.ps1 -p COM5 monitor` (interaktiv, endet nie von selbst; Beenden: `Ctrl+]`; kombiniert: `… -p COM5 flash monitor`). Claude startet ihn nie ohne Timeout. |

In `CLAUDE.md` zusätzlich: „Build/Flash nur über die obigen Befehle mit `scripts/idf.ps1` – nicht `idf.py`
direkt aufrufen, keine eigene Aktivierung versuchen. Flashen nur nach Rückfrage; Monitor startet der Nutzer."

## Projektgerüst

```
CMakeLists.txt              # Top-Level-Projekt (project(app))
sdkconfig.defaults          # committete Basiskonfiguration
partitions.csv              # eigene Partitionstabelle (nvs, phy_init, factory)
.clang-format
scripts/
  idf.ps1                   # aktiviert ESP-IDF selbst und ruft idf.py auf (für Claude/Git Bash)
main/
  CMakeLists.txt
  main.c                    # app_main, nur Verdrahtung
components/
  logic/                    # reine C-Logik ohne IDF-/Hardware-Abhängigkeit
    CMakeLists.txt
    include/logic.h
    logic.c
test/host/                  # eigenes IDF-Projekt für das Linux-Target
  CMakeLists.txt            # bindet ../../components ein, COMPONENTS main
  sdkconfig.defaults        # CONFIG_IDF_TARGET="linux"
  main/CMakeLists.txt
  main/test_logic.c         # Unity-Tests, exit(UNITY_END())
```

Alle Dateien liegen 1:1 in `geruest/` und werden in den Projekt-Root kopiert. `sdkconfig` wird beim ersten Build
erzeugt und nicht committet. Geänderte Einstellungen aus `idf.py menuconfig` per `idf.py save-defconfig` nach
`sdkconfig.defaults` übernehmen; chipspezifisches in `sdkconfig.defaults.<target>`.

## Teststrategie

- **Host (Unit):** Logik in Komponenten ohne Hardware-Abhängigkeit (`components/<name>/`), getestet in `test/host/`
  mit Unity auf dem IDF-Linux-Target (läuft als native Linux-Executable, echtes FreeRTOS-POSIX-Port).
  Hardware-Zugriffe hinter schmalen Interfaces (Funktionszeiger/Struct) kapseln, damit Logik ohne `driver/` auskommt.
  Für IDF-APIs gibt es CMock-Mocks (`$IDF_PATH/tools/mocks/`) – erst einführen, wenn wirklich gebraucht.
- **On-Target (HIL):** eigene Test-App `test/target/` mit `CONFIG_UNITY_ENABLE_IDF_TEST_RUNNER=y`, `TEST_CASE(...)`
  und `unity_run_menu()`; starten per `idf.py -p COMx flash monitor`. Automatisierbar mit `pytest-embedded-idf`
  (lokal oder Self-hosted-Runner mit Board).
- **Manuell (offen – manuell prüfen):** echte Peripherie/Sensoren, Funk (WLAN/BLE-Reichweite, Koexistenz),
  Stromaufnahme/Deep-Sleep, Brownout, OTA auf echtem Gerät, Verhalten nach Power-Cycle.

## CI

`ci.yml` hat drei Jobs auf `ubuntu-latest`:
- `lint`: `clang-format --dry-run --Werror` über alle getrackten `.c/.h` (pip-Version gepinnt).
- `host-test`: im Container `espressif/idf:v5.5.6`, baut `test/host/` für das Linux-Target und führt
  `host_test.elf` aus; Exit-Code = Anzahl fehlgeschlagener Tests.
- `build`: im selben Container `idf.py set-target` + `build` + `size` je Target der Matrix (Default `esp32`),
  lädt Binärdateien und `flasher_args.json` als Artefakt hoch.

Caching: ccache (`IDF_CCACHE_ENABLE=1`) mit `actions/cache` je Target. Grenzen: kein HIL (bräuchte Self-hosted-Runner
mit Board), Image-Pull (~5 GB) kostet einige Minuten, Image-Tag muss manuell mit der lokalen IDF-Version synchron gehalten werden.
In Container-Jobs greift der Image-Entrypoint nicht – daher `. $IDF_PATH/export.sh` in jedem Schritt.

## Typische Fallstricke

### menuconfig-Änderungen gehen verloren / sdkconfig.defaults wirkt nicht #build #sdkconfig
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** Einstellung in `sdkconfig.defaults` geändert, Build nutzt alten Wert; oder menuconfig-Änderung fehlt nach Clone/`set-target`.
- **Ursache:** Existiert `sdkconfig`, hat es Vorrang; defaults greifen nur bei neuen Keys. `sdkconfig` ist nicht committet, `set-target` erzeugt es neu.
- **Lösung / Regel:** Dauerhafte Einstellungen nur über `idf.py save-defconfig` → `sdkconfig.defaults`. Nach Änderung der defaults `sdkconfig` löschen.

### Build scheitert in Cloud-Ordner oder langem Pfad #windows #build
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** Zufällige „Permission denied"/„file in use"-Fehler oder Pfad-zu-lang-Fehler beim Build unter Windows.
- **Ursache:** Dropbox/OneDrive sperren Dateien in `build/` während der Synchronisation; IDF und Toolchain vertragen keine Leerzeichen und stoßen an die Pfadlänge.
- **Lösung / Regel:** `build/` von der Synchronisation ausschließen oder Repo außerhalb des Cloud-Ordners klonen; Pfade kurz und ohne Leerzeichen.

### Flashen schlägt fehl: Port belegt oder falscher Boot-Modus #flash #uart
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** `could not open port COMx` bzw. `Failed to connect ... Wrong boot mode detected`.
- **Ursache:** Ein laufender Monitor (anderes Terminal, IDE) hält den Port; manche Boards haben keine Auto-Reset-Schaltung.
- **Lösung / Regel:** Monitor vor dem Flashen schließen (`flash monitor` in einem Aufruf). Notfalls BOOT halten, RESET tippen, dann flashen.

### Stack Overflow in Task main #freertos #stack
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** `***ERROR*** A stack overflow in task main has been detected` oder Guru Meditation kurz nach Start.
- **Ursache:** `app_main` läuft in einem Task mit kleinem Stack (Default 3584 Byte); große lokale Puffer/`printf` mit Float sprengen ihn.
- **Lösung / Regel:** Große Puffer `static` oder auf dem Heap; Stackgröße bei `xTaskCreate` bzw. `CONFIG_ESP_MAIN_TASK_STACK_SIZE` bewusst setzen; `uxTaskGetStackHighWaterMark` prüfen.

### Task-Watchdog durch Busy-Loop #freertos #watchdog
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** `task_wdt: Task watchdog got triggered` mit IDLE-Task in der Meldung.
- **Ursache:** Eine Schleife ohne blockierenden Aufruf hungert den IDLE-Task aus.
- **Lösung / Regel:** In Endlosschleifen immer blockieren (`vTaskDelay`, Queue/Semaphore mit Timeout); Watchdog nicht abschalten, um es zu „lösen".

### Host-Test baut nicht: Hardware-Header in Logik-Komponente #test #host
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** `test/host` bricht mit fehlendem `driver/...`/`esp_..`-Header oder unbekannter Komponente ab.
- **Ursache:** Das Linux-Target unterstützt nur einen Teil der IDF-Komponenten; Treiber gibt es dort nicht.
- **Lösung / Regel:** Logik-Komponenten ohne `REQUIRES` auf Treiber halten; Hardware über Interfaces injizieren. Host-Tests unter Windows nur via Docker/WSL.

### App passt nicht mehr in die Partition #partition #flash
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** Build-Fehler `app partition is too small for binary`.
- **Ursache:** Firmware ist über die `factory`-Größe in `partitions.csv` gewachsen (bei OTA: über `ota_0`).
- **Lösung / Regel:** `idf.py size`/`size-components` prüfen, ggf. Partition vergrößern. Die Partitionstabelle lässt sich per OTA nicht ändern – vor Auslieferung mit Reserve planen.

### Zugangsdaten (WLAN/MQTT) im Quellcode #security #git
*{{JJJJ-MM-TT}} · Quelle: Preset esp-idf*
- **Symptom:** SSID/Passwort oder MQTT-Token stehen in `main.c` bzw. `sdkconfig.defaults` und landen in der Git-History.
- **Ursache:** Schnellster Weg beim Prototyp; einmal gepusht, ist das Geheimnis kompromittiert (History, Forks, CI-Logs).
- **Lösung / Regel:** Nie in committeten Dateien. `secrets.h.example` (Platzhalter) committen, `secrets.h` lokal und
  per `.gitignore` ausgeschlossen (bindet Code `secrets.h` ein, muss CI vor dem Build das Beispiel kopieren). Alternativ Kconfig-Optionen ohne Default nur in der
  lokalen `sdkconfig` (nicht in `sdkconfig.defaults`) oder Provisioning zur Laufzeit in NVS (z. B. `wifi_provisioning`).
  Geleakte Daten sofort rotieren.

## ADR-Kandidaten

- Zugangsdaten wie? (`secrets.h` lokal + `.example` vs. Kconfig in lokaler `sdkconfig` vs. NVS-Provisioning zur Laufzeit)
- Ziel-Chip und Modul? (ESP32 / -S3 / -C3 / -C6; Flash-/PSRAM-Größe)
- IDF-Version pinnen wie? (exakter Patch-Tag `v5.5.6` vs. Release-Branch `release-v5.5`; Update-Rhythmus)
- sdkconfig im Repo? (nur `sdkconfig.defaults` [+ `.<target>`] vs. vollständige `sdkconfig` committen)
- Partitionierung? (single `factory` vs. OTA mit `ota_0`/`ota_1` + `otadata`; NVS-Größe, SPIFFS/LittleFS/FAT)
- Externe Abhängigkeiten? (IDF Component Manager mit `idf_component.yml` + committeter `dependencies.lock` vs. Git-Submodule in `components/`)
- Host-Tests wie? (IDF-Linux-Target in Docker/WSL vs. reines CMake + Unity/CTest außerhalb von IDF)

## Anwendung

1. `presets/esp-idf/ci.yml` nach `.github/workflows/ci.yml` kopieren; Image-Tag `espressif/idf:v5.5.6` und
   Target-Matrix an die gewählte Version/den Chip anpassen.
2. Inhalt von `presets/esp-idf/gitignore` an `.gitignore` anhängen.
3. Inhalt von `presets/esp-idf/geruest/` (inkl. `.clang-format`, `scripts/idf.ps1`) in den Projekt-Root kopieren; `project(app)` und
   Komponentenname `logic` bei Bedarf umbenennen (auch in `main/` und `test/host/main/` CMakeLists).
4. In `CLAUDE.md` die Zeile `Stack:`, die fünf Befehlszeilen aus der Tabelle oben und den Hinweis darunter
   eintragen (Port, Target, Version anpassen).
5. Fallstricke oben nach `docs/LEARNINGS.md` (Abschnitte Toolchain & Build / Laufzeit & Code) übernehmen, Datum einsetzen.
6. Verifizieren: `scripts/idf.ps1 set-target <chip>`, `scripts/idf.ps1 build` (jeweils mit dem `powershell …`-Präfix),
   Host-Test-Befehl (erwartet `2 Tests 0 Failures`), Lint-Befehl – Ausgaben zitieren. Meldet `idf.ps1` „no ESP-IDF
   activation found", IDF-Installation mit dem Nutzer klären. Flash nur mit Board und Zustimmung, Monitor startet der
   Nutzer – sonst als **offen – manuell prüfen** melden.
7. ADR-Kandidaten mit dem Nutzer durchgehen; Getroffenes per `/entscheidung` festhalten.
