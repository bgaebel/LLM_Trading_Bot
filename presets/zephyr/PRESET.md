# Preset: zephyr

## Passt für

Firmware auf Zephyr RTOS (C), gebaut mit `west`, Workspace-Topologie T2: Dieses Repo ist das
Manifest-Repo (`west.yml`), Zephyr und Module liegen eine Ebene darüber. Tests mit ztest/Twister auf `native_sim`.

## Voraussetzungen

Zephyr-Tag in `west.yml` pinnen (Gerüst: `v4.5.0`, ca. 1 Jahr Support; LTS ist Stand 11/2026 noch `v3.7`,
nächstes voraussichtlich 4.6 – vor Start prüfen). Workspace kurz und **außerhalb von Dropbox/OneDrive** (`C:\zws`).

**Windows 11** (PowerShell, laut Zephyr Getting Started Guide):
```powershell
winget install Kitware.CMake Ninja-build.Ninja oss-winget.gperf Python.Python.3.12 Git.Git oss-winget.dtc wget 7zip.7zip
winget install LLVM.LLVM                     # clang-format (CI nutzt 18 – Abweichungen möglich)
mkdir C:\zws; cd C:\zws
git clone <repo-url> app-repo                # Repo-Ordnername frei, hier: app-repo
py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1
pip install west
west init -l app-repo; west update
west zephyr-export
python -m pip install @((west packages pip) -split ' ')   # Python-Deps (Windows-Form laut Guide)
west sdk install --toolchains arm-zephyr-eabi   # nur benötigte Toolchains
```
**Host-Tests unter Windows:** `native_sim` läuft nur unter Linux. Lokal daher in **WSL2 (Ubuntu)** mit
eigenem Workspace im Linux-Dateisystem (`~/zws`, nicht `/mnt/c` – zu langsam), sonst nur in CI.
WSL einmalig: `sudo apt install git cmake ninja-build gperf ccache dfu-util device-tree-compiler wget
python3-dev python3-venv xz-utils file make gcc gcc-multilib g++-multilib`, danach dieselben west-Schritte
(Bash, `source .venv/bin/activate`, Deps per `west packages pip --install`). `native_sim` braucht kein SDK.

**Linux-CI:** `zephyrproject-rtos/action-zephyr-setup@v1` (west, Multilib, Python-Deps, SDK, Caches).

## Befehle für CLAUDE.md

Alle Befehle im Repo-Ordner mit aktivem venv. `<board>` z. B. `nrf52840dk/nrf52840`.

| Zweck | Befehl |
|---|---|
| Build | `west build -b <board> app -p auto` |
| Test (Host) | WSL/CI: `west twister -T tests -p native_sim` · aus Windows: `wsl -- bash -lc "cd ~/zws/app-repo && source ../.venv/bin/activate && west twister -T tests -p native_sim"` |
| Lint/Format | `clang-format --dry-run --Werror app/src/*.[ch] tests/*/src/*.c` (Git Bash; Fix: `-i` statt `--dry-run --Werror`) |
| Flash / Run | `west flash` (`-r jlink`/`pyocd`/`openocd`) – nur mit angeschlossenem Board und nach Freigabe durch den Nutzer · Simulation (nur Linux/WSL, läuft endlos → mit Timeout): `west build -b native_sim app -d build-sim -t run` |
| Monitor / Logs | UART: `python -m serial.tools.miniterm COM5 115200` · RTT: `west rtt` – interaktiv: startet der Nutzer; Claude nur mit Timeout (z. B. `timeout 20 …` in Git Bash) |

`Stack: Zephyr v4.5 / west (T2) / C / <board>`

## Projektgerüst

```
<repo>/                  ← im Workspace C:\zws\ (daneben: zephyr/, modules/, .west/)
west.yml                 Manifest: Zephyr-Tag + name-allowlist der Module
.clang-format            Kernel-Stil (Tabs, 100 Spalten)
app/                     CMakeLists.txt, prj.conf (Kconfig)
app/src/                 main.c (Thread-/Hardware-Code, dünn) · filter.c/.h (reine Logik, keine Zephyr-APIs)
tests/filter/            CMakeLists.txt (bindet app/src/filter.c ein), prj.conf (CONFIG_ZTEST=y),
                         testcase.yaml (platform_allow: native_sim), src/main.c (ztest-Suite)
```
Alles liegt in `geruest/` und wird 1:1 ins Repo-Root kopiert. Später üblich: `app/boards/<board>.overlay`,
`app/boards/<board>.conf`, eigene Boards unter `boards/`, `app/sysbuild.conf` (MCUboot).

## Teststrategie

- **Host (Pflicht, CI):** reine Logik in Modulen ohne Hardware-Zugriff, ztest-Suiten unter `tests/<modul>/`,
  ausgeführt mit Twister auf `native_sim`. Treiber per Emulator/Mock (`CONFIG_EMUL`, `fff`) nur wenn nötig.
- **Build-Check:** Firmware für das Zielboard in CI bauen (fängt Devicetree-/Kconfig-Fehler).
- **HIL (optional):** `west twister --device-testing --device-serial COMx -p <board> -T tests` auf echtem Board,
  oder Hardware-Map (`--hardware-map map.yaml`). Nicht in CI ohne Self-hosted-Runner.
- **Manuell (offen – manuell prüfen):** Timing, Stromverbrauch, Funk, Sensoren, Verhalten nach Reset/Brown-out.

## CI

Zwei Jobs auf `ubuntu-24.04`: `lint` (`clang-format-18 --dry-run --Werror` über getrackte `.c/.h`) und
`build-test`: Checkout nach `app-repo/` (die Action macht `west init -l`), `action-zephyr-setup@v1`
(Multilib, flaches `west update`, Python-Deps, SDK; cached pip, SDK, ccache), `west build -b $BOARD app`,
`west twister -T tests -p native_sim`; bei Fehler `twister-out/` als Artefakt.
Grenzen: `BOARD` = `native_sim` – fürs Zielboard `env.BOARD`, HAL in `name-allowlist` und `toolchains:`
anpassen. Erster Lauf ohne Cache dauert Minuten. Kein Flash/HIL in CI.

## Typische Fallstricke

### native_sim läuft nicht unter Windows #test #windows #native_sim
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** Build bzw. Twister für `native_sim` scheitert unter Windows oder führt keine Tests aus.
- **Ursache:** `native_sim` ist ein Linux-Host-Executable (POSIX-Arch); Windows wird nicht unterstützt.
- **Lösung / Regel:** Host-Tests in WSL2 (Workspace in `~/`) oder in CI ausführen.

### 32-bit native_sim braucht gcc-multilib #test #build #native_sim
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** Build für `native_sim` scheitert mit fehlenden `bits/*.h` bzw. Linkerfehlern zu `-m32`.
- **Ursache:** `native_sim` baut standardmäßig 32-bit.
- **Lösung / Regel:** `gcc-multilib g++-multilib` installieren oder `native_sim/native/64` verwenden.

### Lange Pfade und Sync-Ordner unter Windows #build #windows
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** Build bricht mit „file name too long“, CMake-Fehlern oder zufälligen Zugriffsfehlern ab.
- **Ursache:** Tiefe Build-Pfade überschreiten 260 Zeichen; Dropbox/OneDrive sperren Dateien beim Sync.
- **Lösung / Regel:** Workspace kurz und außerhalb von Sync-Ordnern (`C:\zws`); Twister mit `--short-build-path`.

### Manifest geändert, west update vergessen #west #build
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** Unerklärliche Kconfig-/API-Fehler nach Pull oder Versionswechsel.
- **Ursache:** `zephyr/` und Module stehen noch auf dem alten Stand aus `west.yml`.
- **Lösung / Regel:** Nach jeder Änderung an `west.yml` `west update`; bei Board-/Overlay-Wechsel `-p always`.

### Kconfig-Option wirkt nicht #kconfig #build
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** Option in `prj.conf` gesetzt, Funktion fehlt trotzdem; nur eine Warnung im Build-Log.
- **Ursache:** Abhängigkeit (`depends on`) nicht erfüllt oder Tippfehler – Kconfig verwirft den Wert still.
- **Lösung / Regel:** Ergebnis in `build/zephyr/.config` prüfen; `west build -t menuconfig` zeigt Abhängigkeiten.

### Gerät „not ready“ trotz Overlay #devicetree #runtime
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** `device_is_ready()` liefert false oder Build-Fehler `__device_dts_ord_N undefined`.
- **Ursache:** Node hat nicht `status = "okay"`, Treiber-Kconfig fehlt, oder Overlay-Dateiname passt nicht zum Board.
- **Lösung / Regel:** `build/zephyr/zephyr.dts` prüfen; Overlay heißt `app/boards/<board-mit-_-statt-/>.overlay`.

### Stack-Overflow durch Logging/printf #runtime #stack
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** Hard Fault oder MPU-Fault kurz nach Start, oft nach Hinzufügen von Logs oder Float-Formatierung.
- **Ursache:** `CONFIG_MAIN_STACK_SIZE`/Thread-Stacks zu klein.
- **Lösung / Regel:** `CONFIG_THREAD_ANALYZER=y` zum Messen; Stacks gezielt vergrößern, Reserve dokumentieren.

### Zugangsdaten in prj.conf oder Quellcode #security #kconfig #git
*{{JJJJ-MM-TT}} · Quelle: Preset zephyr*
- **Symptom:** WLAN-PSK, MQTT-Login oder API-Key stehen in `prj.conf`/`main.c` und damit in Git-Historie und CI-Logs.
- **Ursache:** Kconfig-Strings sind bequem; aus der Historie kaum wieder zu entfernen.
- **Lösung / Regel:** Nie committen. Eigene Kconfig-Strings (leerer Default) in `app/Kconfig`, Werte in `app/secrets.conf` (gitignored, `secrets.conf.example` committet), Build mit `-DEXTRA_CONF_FILE=secrets.conf`; besser: Provisionierung zur Laufzeit über Settings/NVS.

## ADR-Kandidaten

- Zephyr-Version: aktuelles LTS vs. neuestes Release (Update-Rhythmus)?
- Workspace: T2 (App-Repo als Manifest) vs. T3 (separates Manifest-Repo) vs. Vendor-SDK (z. B. nRF Connect SDK)?
- Board: Dev-Kit + Overlays vs. eigene Board-Definition unter `boards/`?
- Bootloader/Update: MCUboot via sysbuild + DFU (SMP/USB/BLE) vs. kein Bootloader?
- Log-Ausgabe: UART vs. RTT vs. USB-CDC; Log-Level im Release?
- Host-Tests: `native_sim` + ztest vs. `unit_testing`-Plattform vs. separates Host-CMake-Projekt?

## Anwendung

1. `presets/zephyr/ci.yml` → `.github/workflows/ci.yml` kopieren; `BOARD`, HAL und `toolchains:` an Ziel anpassen.
2. `presets/zephyr/gitignore` an `.gitignore` anhängen.
3. Inhalt von `presets/zephyr/geruest/` ins Repo-Root kopieren (bestehende Dateien nicht überschreiben);
   Zephyr-Tag in `west.yml` mit dem Nutzer festlegen, HAL des Ziel-SoCs in `name-allowlist` eintragen.
4. Tabelle „Befehle für CLAUDE.md“ in `CLAUDE.md` übernehmen, `<board>` und Workspace-Pfad konkret einsetzen; `Stack:`-Zeile füllen.
5. „Typische Fallstricke“ nach `docs/LEARNINGS.md` (Bereich „Toolchain & Build“ bzw. „Laufzeit & Code“) kopieren, `{{JJJJ-MM-TT}}` ersetzen.
6. Workspace einrichten (Voraussetzungen), dann verifizieren: Build für `<board>`, Twister in WSL oder per
   CI-Lauf, Lint. Was nicht lief, als **offen – manuell prüfen** melden.
7. ADR-Kandidaten mit dem Nutzer durchgehen, getroffene Entscheidungen per `/entscheidung` festhalten.
