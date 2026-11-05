# Hardware

<!-- Nur für Firmware-/Hardware-Projekte. Bei reiner Software diesen Ordner löschen (/kickoff fragt).
     Hier stehen FAKTEN über die Hardware, die man sonst im Datenblatt/Schaltplan suchen müsste.
     Große PDFs nicht committen (Link + Revision reicht), außer sie sind schwer wiederzufinden. -->

## Ziel-Hardware
| Board / Bauteil | Revision | Dokument |
|---|---|---|
| {{z. B. Custom-Board „Sensorknoten"}} | {{Rev B}} | {{Schaltplan-Link}} |
| {{MCU, z. B. STM32G474RE}} | {{Silicon Rev}} | {{Datenblatt, Reference Manual, Errata – Link}} |

## Pinbelegung
| Pin | Funktion | Peripherie | Richtung / Pegel | Hinweis |
|---|---|---|---|---|
| {{PA2}} | {{UART2_TX → Debug}} | {{USART2 AF7}} | {{out, 3V3}} | {{...}} |

## Takt, Speicher, Partitionen
- Takt: {{HSE 8 MHz → SYSCLK 170 MHz}}
- Flash/RAM-Layout: {{Linker-Script / Partitionstabelle}}

## Datenblatt-Notizen & Errata
<!-- Was im Datenblatt wichtig, versteckt oder falsch ist. Mit Seitenzahl/Abschnitt. -->
- {{...}}

## Debug-Zugang & Messaufbau
- Programmer/Debugger: {{ST-Link V3, J-Link, USB-JTAG}}
- Serielle Konsole: {{Port, Baudrate}}
- Messaufbau für HIL/manuelle Tests: {{...}}
