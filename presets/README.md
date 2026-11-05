# Stack-Presets

Startpakete für häufige Stacks. `/kickoff` wählt eines aus, wendet es an und löscht danach den Ordner `presets/`.
Presets beschleunigen nur den Start – danach gehört alles dem Projekt und wird dort gepflegt.

| Preset | Passt für | Host-Tests | CI auf GitHub-Runnern |
|---|---|---|---|
| [platformio](platformio/PRESET.md) | ESP32/STM32/AVR mit PlatformIO (Arduino, ESP-IDF, STM32Cube) | Unity, `env:native` | voll |
| [esp-idf](esp-idf/PRESET.md) | ESP32-Familie mit nativem ESP-IDF 5.x | Unity auf Linux-Target | voll |
| [stm32-cmake](stm32-cmake/PRESET.md) | STM32 mit CubeMX-CMake-Projekt | separates Host-CMake-Projekt | voll |
| [zephyr](zephyr/PRESET.md) | Zephyr RTOS mit west | ztest + Twister auf `native_sim` | voll |
| [twincat](twincat/PRESET.md) | Beckhoff TwinCAT 3 SPS (Structured Text) | TcUnit (auf XAE) | nur formale Prüfungen, Build braucht eigenen Runner |
| [python](python/PRESET.md) | Python ≥ 3.12 mit uv | pytest | voll |
| [web](web/PRESET.md) | TypeScript, Vite (oder Next.js), pnpm | Vitest | voll |

## Aufbau eines Presets

```
presets/<name>/
  PRESET.md   Voraussetzungen, Befehle für CLAUDE.md, Gerüst, Teststrategie, Fallstricke, ADR-Kandidaten, Anwendung
  ci.yml      ersetzt .github/workflows/ci.yml
  gitignore   wird an .gitignore angehängt
  gitattributes, editorconfig   (optional, z. B. twincat) werden an die gleichnamigen Punkt-Dateien angehängt
  geruest/    minimales, baubares Projektgerüst (nur bei neuen Projekten kopieren)
```

## Anwenden (Kurzfassung – Details im jeweiligen PRESET.md)

1. Voraussetzungen prüfen (Toolchain installiert? Version?). Fehlt etwas: Installationsbefehl nennen, nicht still weitermachen.
2. `ci.yml` → `.github/workflows/ci.yml` (bestehende CI zusammenführen, nicht ersetzen), `gitignore` an `.gitignore`
   anhängen, ggf. `gitattributes`/`editorconfig` ebenso.
3. Neues Projekt: `geruest/` ins Projektwurzelverzeichnis kopieren. Bestehendes Projekt: Gerüst nur als Referenz nutzen.
4. Befehlstabelle und Zeile „Shell:" in `CLAUDE.md` übernehmen. Build, Test (Host) und Lint ausführen;
   Flash nur mit Board und Zustimmung, Monitor nicht (endet nicht von selbst).
5. „Typische Fallstricke" nach `docs/LEARNINGS.md` übernehmen (nur die, die zum Projekt passen).
6. „ADR-Kandidaten" mit dem Nutzer durchgehen; getroffene Entscheidungen als ADR festhalten.

## Mehrere Stacks in einem Repo

Zum Beispiel Firmware und Python-Testwerkzeug: Beide Presets anwenden, jeweils in ein Unterverzeichnis
(`firmware/`, `tools/`). Die Befehlstabelle in `CLAUDE.md` bekommt pro Teil eine Zeilengruppe, die CI-Jobs
werden in einer `ci.yml` zusammengeführt (mit `working-directory`).

## Eigenes Preset ergänzen

Neuen Ordner nach obigem Aufbau anlegen und hier in die Tabelle eintragen. Bewährt hat sich, ein Preset aus
einem erfolgreich laufenden Projekt abzuleiten statt es auf Vorrat zu schreiben.
