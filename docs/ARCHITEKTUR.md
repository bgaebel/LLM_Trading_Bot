# Architektur

<!-- Lebendes Dokument: beschreibt den IST-Zustand, nicht die Geschichte (die steht in ADRs).
     Ziel: Wer das liest, findet in 2 Minuten die richtige Datei für eine Änderung.
     Bei Strukturänderungen im selben Commit aktualisieren. -->

## Überblick
{{3–5 Sätze oder ein Diagramm (Mermaid/ASCII): Hauptbausteine und wie sie zusammenspielen.}}

```
{{z. B.
 Sensor ──I2C──> [drivers/bme280] ──> [app/sampler] ──queue──> [app/telemetry] ──MQTT──> Broker
}}
```

## Verzeichnisse
| Pfad | Verantwortung |
|---|---|
| `{{src/...}}` | {{...}} |

## Module & Schnittstellen
<!-- Pro Modul: Zweck, öffentliche Schnittstelle, wer es nutzt, wichtige Invarianten. -->
### {{Modul}}
- **Zweck:** …
- **Schnittstelle:** `{{header.h / api}}`
- **Invarianten:** {{z. B. nicht aus ISR aufrufen}} · relevante ADRs: {{ADR-NNNN}}

## Datenfluss & Nebenläufigkeit
{{Tasks/Threads, Prioritäten, Queues, Interrupts, Locks – bzw. Request-Fluss bei Software.}}

## Externe Schnittstellen
| Schnittstelle | Protokoll / Format | Spezifikation |
|---|---|---|
| {{...}} | {{...}} | {{Link/Datei}} |

## Build, Konfiguration & Deployment
{{Wie entsteht das Artefakt, welche Konfig-Varianten gibt es, wie kommt es aufs Ziel.}}

## Teststrategie
- **Host-Unit-Tests:** {{was, womit}}
- **Integration / HIL:** {{was, womit, wer führt aus}}
- **Nicht automatisiert getestet:** {{bewusst offene Lücken}}
