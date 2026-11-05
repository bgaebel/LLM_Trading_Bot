# Preset: stm32-cmake

## Passt für

STM32-Firmware mit STM32CubeMX-generiertem Code (Projekt-Generator „CMake"), HAL/LL, arm-none-eabi-gcc, Ninja.
Editor frei (VS Code + STM32-Extension, CLion, …). Nicht für CubeIDE-Managed-Build oder PlatformIO.

## Voraussetzungen

Windows 11:
- **STM32CubeMX** (Installer von st.com, Login nötig) – erzeugt Init-Code, `CMakeLists.txt`, `CMakePresets.json`, Toolchain-Datei.
- **STM32CubeCLT** (Installer von st.com) – enthält arm-none-eabi-gcc, CMake, Ninja, `STM32_Programmer_CLI`,
  ST-LINK-GDB-Server. Bin-Verzeichnisse in den `PATH`. Alternativ einzeln:
  `winget install Kitware.CMake Ninja-build.Ninja` + Arm GNU Toolchain von developer.arm.com.
- Host-Compiler für Tests: `winget install Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"`
  (ohne `--override` fehlt der C++-Workload). Alternativ MinGW-w64 im `PATH` – dann im Test-Befehl `-G Ninja` ergänzen.
- `pip install clang-format==18.1.8 pyserial` (Version = CI, sonst Formatierungs-Diffs).
- Prüfen: `arm-none-eabi-gcc --version`, `cmake --version` (≥ 3.22), `ninja --version`, `STM32_Programmer_CLI --version`.

Linux-CI: `carlosperate/arm-none-eabi-gcc-action@v1` (Version gepinnt), Ninja per apt, clang-format per pip.
Versionen von CubeMX, CubeCLT/GCC und Firmware-Paket (z. B. STM32Cube FW_F4) als ADR festhalten.

## Befehle für CLAUDE.md

Stack: `STM32{{Familie}} / CubeMX {{Version}} + HAL / arm-none-eabi-gcc {{Version}} / CMake + Ninja / C11`

| Zweck | Befehl |
|---|---|
| Build | `cmake --preset Debug && cmake --build --preset Debug` |
| Test (Host) | `cmake -S test -B build/host && cmake --build build/host --config Debug && ctest --test-dir build/host -C Debug --output-on-failure` |
| Lint/Format | `clang-format --dry-run --Werror $(git ls-files -- '*.c' '*.h' '*.cpp' '*.hpp' ':!:Core' ':!:Drivers' ':!:Middlewares')` (Fix: `-i` statt `--dry-run --Werror`) |
| Flash / Run | `STM32_Programmer_CLI -c port=SWD -w build/Debug/{{projekt}}.elf -v -rst` – nur mit angeschlossenem Board und nach Freigabe durch den Nutzer |
| Monitor / Logs | `python -m serial.tools.miniterm {{COM5}} 115200` (UART/ST-LINK-VCP) – interaktiv: startet der Nutzer; Claude nur mit Timeout, z. B. `timeout 20 …` (Git Bash) |

Hinweise: Befehle laufen in Git Bash (`$(...)`). Test (Host) nutzt den CMake-Standardgenerator (Visual Studio). Alternativ OpenOCD für Flash:
`openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c "program build/Debug/{{projekt}}.elf verify reset exit"`.
SWO-Ausgabe über `STM32_Programmer_CLI -c port=SWD -swv ...` (Syntax per `--help` prüfen, Kerntakt nötig).

## Projektgerüst

```
<repo>/
  {{projekt}}.ioc          CubeMX-Projekt (versioniert!)        ← CubeMX
  CMakeLists.txt           Root-Projekt mit USER-Abschnitten    ← CubeMX (einmalig erzeugt)
  CMakePresets.json        Debug/Release, Ninja, Toolchain      ← CubeMX; Skizze in geruest/
  cmake/                   gcc-arm-none-eabi.cmake, stm32cubemx/ ← CubeMX (stm32cubemx/ wird regeneriert)
  Core/ Drivers/ startup_*.s *.ld                               ← CubeMX
  app/ringbuf.{c,h}        reine Logik, kein HAL-Include         ← geruest/
  test/CMakeLists.txt      Host-Testprojekt (GoogleTest)         ← geruest/
  test/test_ringbuf.cpp, .clang-format                           ← geruest/
```

Das Gerüst ist absichtlich nur der Teil, den CubeMX nicht erzeugt. Firmware-Code liegt in `app/`;
`Core/Src/main.c` ruft ihn nur in `USER CODE`-Blöcken auf. Einbinden im Root-`CMakeLists.txt`
(in dessen User-Abschnitt, nicht in `cmake/stm32cubemx/`):

```cmake
target_sources(${CMAKE_PROJECT_NAME} PRIVATE app/ringbuf.c)
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE app)
```

Testframework: **GoogleTest** statt Unity – per FetchContent ohne Zusatzskripte, `ctest`-Integration,
bessere Fehlermeldungen; C-Module werden über `extern "C"`-Header aus C++-Tests aufgerufen.

## Teststrategie

- **Host (CI):** Alles in `app/` ist HAL-frei und wird mit GoogleTest auf dem PC getestet (Protokolle, Parser,
  Zustandsautomaten, Filter, Ringpuffer). Hardwarezugriffe hinter schmalen Interfaces (Funktionszeiger/Structs),
  im Test durch Fakes ersetzt.
- **Target-Build (CI):** Firmware wird kompiliert, Größe ausgegeben – fängt Linker-/Speicherprobleme.
- **HIL/manuell:** Flashen, Timing, Interrupts, Peripherie, Low-Power. Mit Messung/Log belegen, sonst
  **offen – manuell prüfen**. Später optional: Smoke-Test (Flash + UART) auf Self-Hosted-Runner.

## CI

`ci.yml` hat drei Jobs auf `ubuntu-24.04`:
- `lint`: clang-format (gepinnt per pip) auf eigenen Code, CubeMX-Ordner ausgenommen.
- `host-tests`: Host-Projekt in `test/` mit Ninja bauen, `ctest`. Cache: `build/host` (spart GoogleTest-Download).
- `firmware`: Toolchain per `carlosperate/arm-none-eabi-gcc-action` (cacht selbst), `cmake --preset Debug`,
  `arm-none-eabi-size`, `.elf`/`.map` als Artefakt. Wird übersprungen, solange kein Root-`CMakeLists.txt` existiert.

Grenzen: Kein Flash/HIL. GCC-Version im CI muss zur lokalen CubeCLT passen (`release:` anpassen).
`Drivers/` muss committet sein, sonst fehlt im CI die HAL (CubeMX läuft im CI nicht).

## Typische Fallstricke

### CubeMX-Regenerieren löscht Code außerhalb von USER CODE #cubemx #codegen
*{{JJJJ-MM-TT}} · Quelle: Preset stm32-cmake*
- **Symptom:** Nach „Generate Code" fehlen eigene Änderungen in `main.c`, `stm32*_it.c`, `*_msp.c`.
- **Ursache:** CubeMX überschreibt generierte Dateien; erhalten bleibt nur Code zwischen `/* USER CODE BEGIN x */` und `END`.
- **Lösung / Regel:** Eigene Logik in `app/`, in generierten Dateien nur Aufrufe in USER-Blöcken. Vor dem Regenerieren committen, danach `git diff` prüfen.

### .ioc und generierter Code müssen zusammen versioniert werden #cubemx #git
*{{JJJJ-MM-TT}} · Quelle: Preset stm32-cmake*
- **Symptom:** Pinbelegung im Code passt nicht zur `.ioc`, oder andere CubeMX-Version will das Projekt migrieren.
- **Ursache:** `.ioc` nicht committet bzw. mit anderer CubeMX-/FW-Paket-Version geöffnet.
- **Lösung / Regel:** `.ioc`, `Core/`, `Drivers/`, `cmake/` committen; Änderung an `.ioc` und Regenerat im selben Commit. CubeMX-Version per ADR pinnen, Migration nur bewusst.

### Eigene Quellen nicht in cmake/stm32cubemx/CMakeLists.txt eintragen #cmake #cubemx
*{{JJJJ-MM-TT}} · Quelle: Preset stm32-cmake*
- **Symptom:** Nach dem Regenerieren fehlen Dateien im Build, Linkerfehler „undefined reference".
- **Ursache:** `cmake/stm32cubemx/CMakeLists.txt` wird bei jeder Generierung neu geschrieben.
- **Lösung / Regel:** Eigene Quellen/Includes nur im Root-`CMakeLists.txt` per `target_sources`/`target_include_directories`.

### HAL_Delay hängt im Interrupt #hal #interrupt
*{{JJJJ-MM-TT}} · Quelle: Preset stm32-cmake*
- **Symptom:** `HAL_Delay()`/Timeouts von HAL-Funktionen kehren in einer ISR oder einem Callback nie zurück.
- **Ursache:** Der HAL-Tick (SysTick oder TIMx) hat gleiche/niedrigere Priorität als die laufende ISR und zählt nicht weiter.
- **Lösung / Regel:** Keine blockierenden HAL-Aufrufe in ISRs/Callbacks; Tick-Priorität (`TICK_INT_PRIORITY`) höher (numerisch kleiner) als alle ISRs, die `HAL_GetTick()` nutzen.

### FreeRTOS: falsche Interrupt-Prioritäten und SysTick als HAL-Timebase #freertos #interrupt
*{{JJJJ-MM-TT}} · Quelle: Preset stm32-cmake*
- **Symptom:** Sporadische Hardfaults/`configASSERT` in `vPortValidateInterruptPriority`, oder Hänger beim Start.
- **Ursache:** ISR ruft `...FromISR()` mit Priorität numerisch kleiner als `configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY`; SysTick doppelt genutzt.
- **Lösung / Regel:** In CubeMX HAL-Timebase auf einen TIM legen; ISRs mit RTOS-Aufrufen auf Priorität numerisch ≥ dieser Grenze; Priority Group 4.

### printf mit %f gibt nichts aus #newlib #toolchain
*{{JJJJ-MM-TT}} · Quelle: Preset stm32-cmake*
- **Symptom:** Float-Ausgaben leer oder `printf` landet nirgends.
- **Ursache:** newlib-nano ohne Float-Support (`--specs=nano.specs`); `_write()` nicht auf UART/SWO umgeleitet.
- **Lösung / Regel:** Bei Bedarf `-u _printf_float` linken (kostet Flash) oder Festkomma ausgeben; `_write()` im USER-Block implementieren.

### clang-format auf generiertem Code erzeugt Regenerier-Diffs #format #cubemx
*{{JJJJ-MM-TT}} · Quelle: Preset stm32-cmake*
- **Symptom:** Nach jedem „Generate Code" riesige Diffs in `Core/`.
- **Ursache:** Generierter Code wurde formatiert; CubeMX schreibt wieder im eigenen Stil.
- **Lösung / Regel:** `Core/`, `Drivers/`, `Middlewares/` vom Formatieren ausnehmen (Lint-Befehl filtert sie).

## ADR-Kandidaten

- CubeMX-, CubeCLT/GCC- und FW-Paket-Version: welche Versionen werden gepinnt, wie wird migriert?
- `Drivers/` committen oder per CubeMX/Submodul beziehen? (Default: committen, sonst baut CI nicht)
- HAL vs. LL vs. Register: pro Peripherie oder global?
- Bare-Metal-Superloop vs. FreeRTOS (CMSIS-RTOS2) vs. ThreadX?
- Debug-Probe und Flash-Tool: ST-LINK + STM32_Programmer_CLI vs. OpenOCD vs. J-Link?
- Log-Kanal: UART/VCP vs. SWO/ITM vs. SEGGER RTT?

## Anwendung

1. Voraussetzungen prüfen (`arm-none-eabi-gcc --version` usw.); fehlende Werkzeuge mit Installationsweg nennen.
2. Nutzer erzeugt in CubeMX das Projekt im Repo-Wurzelverzeichnis (Project Manager → Toolchain/IDE: **CMake**).
   Ohne CubeMX-Projekt: Schritte 3–6 trotzdem ausführen, Firmware-Build bleibt bis dahin offen.
3. `ci.yml` → `.github/workflows/ci.yml`; `release:` auf die lokale GCC-Version setzen.
4. `gitignore` an `.gitignore` anhängen.
5. `geruest/` kopieren: `app/`, `test/`, `.clang-format`. `CMakePresets.json` **nur**, wenn CubeMX keines erzeugt hat.
6. `app/` im Root-`CMakeLists.txt` einbinden (Snippet unter „Projektgerüst").
7. Befehlstabelle und Stack-Zeile in `CLAUDE.md` übernehmen, `{{projekt}}`/`{{COM5}}` ersetzen.
8. Build, Test (Host) und Lint ausführen und Ausgabe zitieren; Flash nur mit Board und Freigabe, Monitor nur mit
   Timeout; sonst als **offen – manuell prüfen** melden.
9. Passende Fallstricke nach `docs/LEARNINGS.md` (Datum einsetzen), ADR-Kandidaten mit dem Nutzer klären.
