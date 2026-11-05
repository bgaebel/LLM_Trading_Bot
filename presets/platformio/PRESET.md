# Preset: platformio

## Passt für

Firmware für ESP32 / STM32 / AVR mit PlatformIO-CLI (`pio`), Framework Arduino, ESP-IDF oder STM32Cube.
Reine Logik wird auf dem Host getestet (`env:native` + Unity). Gerüst: ESP32 (`esp32dev`) mit Arduino.

## Voraussetzungen

Versionen gepinnt in `requirements-dev.txt` (PlatformIO Core 6.2.0, clang-format 23.1.5) und `platformio.ini`
(`espressif32 @ 8.0.0` = Arduino-Core 3.3.x, `native @ 1.2.1`).

**Windows 11** (Python 3.13 vorhanden):
```powershell
py -3.13 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt          # pio + clang-format
winget install BrechtSanders.WinLibs.POSIX.UCRT   # gcc/g++ für env:native, setzt PATH; neues Terminal, `g++ --version`
```
- USB-Treiber des Boards (CP210x oder CH340), sonst kein COM-Port.
- Die VS-Code-Erweiterung „PlatformIO IDE" bringt ein eigenes `pio` unter `~/.platformio/penv` mit. Für
  Claude gilt das venv-`pio`; beide teilen sich `~/.platformio` (Plattformen/Toolchains).

**Linux / CI:** `pip install -r requirements-dev.txt`; gcc ist auf `ubuntu-latest` vorhanden.
Toolchains lädt `pio` beim ersten Build selbst nach `~/.platformio` (ESP32: ~1 GB).

## Befehle für CLAUDE.md

Stack: ESP32 (esp32dev) / PlatformIO 6.2 / Arduino-Core 3.3 (espressif32 8.0.0) / C++17

| Zweck | Befehl |
|---|---|
| Build | `pio run -e esp32dev` |
| Test (Host) | `pio test -e native` |
| Lint/Format | `clang-format --dry-run --Werror $(git ls-files '*.c' '*.cpp' '*.h' '*.hpp') && pio check -e esp32dev --fail-on-defect=medium --fail-on-defect=high` (Formatieren: `clang-format -i …`) |
| Flash / Run | `pio run -e esp32dev -t upload` (ggf. `--upload-port COM5`) – nur mit angeschlossenem Board und nach Freigabe durch den Nutzer |
| Monitor / Logs | `pio device monitor -e esp32dev` – interaktiv: startet der Nutzer (Beenden: Ctrl+C); Claude nur mit Timeout, z. B. `timeout 20 pio device monitor -e esp32dev` (Git Bash) |

Lint-Zeile in Git Bash ausführen (`$(…)`). Ohne Board bleiben Flash/Monitor **offen – manuell prüfen**.
Tests auf dem Target (HIL): `pio test -e esp32dev` (braucht eigenen Testordner, s. u.).

## Projektgerüst

Alles aus `geruest/` ins Projektwurzelverzeichnis kopieren (inkl. der Punkt-Datei `.clang-format`):
```
platformio.ini            Envs esp32dev (Firmware) + native (Host-Tests), cppcheck-Konfiguration
requirements-dev.txt      gepinnte Host-Werkzeuge (pio, clang-format)
.clang-format             Google-Stil, 100 Spalten
src/main.cpp              Arduino setup()/loop(), nur Verdrahtung mit der Hardware
lib/debounce/src/         Beispielmodul mit reiner Logik (kein Arduino.h) → host-testbar
test/test_native/         Unity-Test für lib/debounce, läuft nur in env:native
```
Andere Boards: `[env:<name>]` mit `platform`/`board`/`framework` ergänzen (z. B. `ststm32` + `nucleo_f401re`
+ `stm32cube`, `atmelavr` + `uno` + `arduino`), Plattform-Version pinnen, Befehle in CLAUDE.md anpassen.

## Teststrategie

- **Host (Pflicht, CI):** Logik (Parser, Zustandsautomaten, Regler, Protokolle) gehört in `lib/<modul>/` ohne
  HAL-Includes; Hardwarezugriffe hinter schmale Interfaces, die im Test durch Fakes ersetzt werden.
  `test_build_src` ist standardmäßig aus – `src/` wird im Test nicht gebaut, nur das, was aus `lib/` inkludiert wird.
- **On-Target / HIL:** `test/test_embedded/` mit `setup()`/`loop()`-Unity-Runner, `pio test -e esp32dev`.
  Braucht Board am USB → nur lokal oder auf einem Self-Hosted-Runner; in CI nicht enthalten.
- **Manuell:** Timing, Funk (WLAN/BLE), Stromaufnahme, Peripherie am realen Aufbau. Ergebnisse in der Spec
  festhalten, als **offen – manuell prüfen** markieren, bis sie belegt sind.

## CI

`ci.yml`: ein Job auf `ubuntu-latest` – Python 3.13 + gepinnte Tools, `clang-format`-Check über alle
getrackten C/C++-Dateien, `pio test -e native`, `pio run -e esp32dev`, `pio check` über `src/`, `include/`, `lib/`
(`check_src_filters`; Abbruch bei Medium/High-Befunden), `firmware.bin` als Artefakt.
Cache: pip über `requirements-dev.txt`, `~/.platformio` über den Hash von `platformio.ini` (spart den
Toolchain-Download). Grenzen: keine On-Target-Tests, kein Flashen; bei mehreren Boards je Env einen
Build-Schritt (oder Matrix) ergänzen.

## Typische Fallstricke

### env:native scheitert unter Windows mit „gcc is not recognized" #pio #toolchain #windows
*Preset · Quelle: PlatformIO-Doku*
- **Symptom:** `pio test -e native` bricht sofort ab, Firmware-Build läuft.
- **Ursache:** Die Plattform `native` nutzt den System-Compiler; Windows hat keinen.
- **Lösung / Regel:** MinGW-gcc installieren (WinLibs/MSYS2) und in `PATH`; prüfen mit `g++ --version`.

### Ungepinnte Plattform bricht den Build über Nacht #pio #build
*Preset · Quelle: Erfahrung*
- **Symptom:** Unveränderter Code baut plötzlich nicht mehr oder verhält sich anders (z. B. LEDC-/Timer-API).
- **Ursache:** `platform = espressif32` ohne Version zieht neue Toolchain und neuen Arduino-Core (Major-Sprünge mit API-Brüchen).
- **Lösung / Regel:** Plattform immer mit exakter Version pinnen; Updates bewusst per ADR/Commit.

### Test findet Code aus src/ nicht #pio #test
*Preset · Quelle: PlatformIO-Doku*
- **Symptom:** Linker-Fehler „undefined reference" in `pio test -e native`.
- **Ursache:** `test_build_src = no` (Default) – `src/` wird für Tests nicht gebaut; `lib/`-Module nur, wenn ihr Header inkludiert wird (LDF).
- **Lösung / Regel:** Testbare Logik nach `lib/<modul>/` legen und im Test inkludieren; `src/` bleibt dünn.

### Monitor zeigt nur Zeichensalat #serial #pio
*Preset · Quelle: Erfahrung*
- **Symptom:** Unlesbare Zeichen im Monitor.
- **Ursache:** `monitor_speed` (Default 9600) ≠ `Serial.begin(...)`.
- **Lösung / Regel:** `monitor_speed` in `platformio.ini` gleich der Firmware-Baudrate setzen.

### Upload: „Failed to connect to ESP32" / COM-Port belegt #esp32 #flash
*Preset · Quelle: Erfahrung*
- **Symptom:** Upload bricht beim Verbinden ab oder Port ist „access denied".
- **Ursache:** Offener Serial-Monitor hält den Port; manche Boards gehen ohne BOOT-Taste nicht in den Download-Modus.
- **Lösung / Regel:** Monitor vor dem Upload schließen; notfalls BOOT halten, EN kurz drücken.

### ESP32: Strapping- und Flash-Pins als GPIO #esp32 #hardware #gpio
*Preset · Quelle: ESP32-Datenblatt*
- **Symptom:** Board bootet nicht oder resettet, sobald Peripherie angeschlossen ist.
- **Ursache:** GPIO 6–11 gehören zum SPI-Flash; GPIO 0, 2, 5, 12, 15 sind Strapping-Pins; ADC2 ist bei aktivem WLAN nicht nutzbar.
- **Lösung / Regel:** Pinbelegung gegen Datenblatt prüfen und in `docs/hardware/` festhalten.

### Host-Test grün, Target falsch: Typgrößen #test #portability
*Preset · Quelle: Erfahrung*
- **Symptom:** Überlauf/falsche Werte nur auf dem Target (typisch AVR).
- **Ursache:** `int` ist auf AVR 16 Bit, auf Host und ESP32 32 Bit; `double` auf AVR = `float`.
- **Lösung / Regel:** In Logik-Modulen nur `<stdint.h>`-Typen verwenden; Grenzwerte explizit testen.

### .pio/ im Dropbox-Ordner #pio #dropbox #windows
*Preset · Quelle: Erfahrung*
- **Symptom:** Builds langsam, „Permission denied"/gesperrte Dateien, Sync-Konflikte in `.pio/`.
- **Ursache:** Dropbox synchronisiert tausende Build-Dateien während des Builds.
- **Lösung / Regel:** `.pio` (und `.venv`) von Dropbox ausschließen: `Set-Content -Path .pio -Stream com.dropbox.ignored -Value 1` (Ordner muss existieren, ggf. vorher anlegen).

### Zugangsdaten im Quellcode #security #wlan #git
*Preset · Quelle: Erfahrung*
- **Symptom:** WLAN-Passwort, MQTT-Login oder API-Key landen in `main.cpp` und damit in Git-Historie und CI-Logs.
- **Ursache:** Schnell als `const char*` eingetragen „nur zum Testen“; aus der Historie kaum wieder zu entfernen.
- **Lösung / Regel:** Nie in den Quellcode. `include/secrets.h.example` (Platzhalter) committen, `include/secrets.h` lokal kopieren und befüllen – ist per `.gitignore` ausgeschlossen. In CI vor dem Build `cp include/secrets.h.example include/secrets.h`.

## ADR-Kandidaten

- Framework: Arduino vs. ESP-IDF (bzw. STM32Cube/HAL) vs. Arduino als ESP-IDF-Komponente?
- Plattform-/Core-Version: welche Version pinnen, wann aktualisieren?
- RTOS-Nutzung: Superloop vs. FreeRTOS-Tasks (Task-Aufteilung, Prioritäten)?
- Speicher: dynamische Allokation (`String`, `new`) zur Laufzeit erlaubt oder nur beim Start?
- Konfiguration/Persistenz: Preferences/NVS vs. LittleFS vs. Kompilierzeit-Konstanten?
- Updates im Feld: nur USB vs. OTA (Partitionstabelle, Signierung)?

## Anwendung

1. Voraussetzungen prüfen: `pio --version`, `clang-format --version`, `g++ --version`. Fehlt etwas → Befehl aus „Voraussetzungen" nennen.
2. `ci.yml` → `.github/workflows/ci.yml`.
3. `gitignore` an `.gitignore` anhängen. Liegt das Projekt in Dropbox: `.pio` und `.venv` ausschließen (s. Fallstricke).
4. Neues Projekt: Inhalt von `geruest/` (inkl. `.clang-format`) ins Wurzelverzeichnis kopieren; Board/Env
   und `kButtonPin` an die echte Hardware anpassen. Bestehendes Projekt: nur `requirements-dev.txt`,
   `env:native` und `.clang-format` übernehmen, wenn sie fehlen.
5. Befehlstabelle und `Stack:`-Zeile in `CLAUDE.md` eintragen (Env-Namen anpassen).
6. Verifizieren: Build, Test (Host), Lint ausführen und Ausgabe zitieren. Flash nur mit Board und Freigabe, Monitor nur mit Timeout,
   sonst als **offen – manuell prüfen** melden.
7. Passende Fallstricke nach `docs/LEARNINGS.md` (Datum = heute), ADR-Kandidaten mit dem Nutzer klären.
