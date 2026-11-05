# Learnings

<!-- Erkenntnisse, die Zeit gekostet haben und nicht aus dem Code ablesbar sind.
     Neueste oben innerhalb des Bereichs. Jeder Eintrag hat #tags, damit man per grep findet:
       grep -n "#uart" docs/LEARNINGS.md
     Regeln:
     - Ein Eintrag = Symptom → Ursache → was hilft. Max. ~6 Zeilen. Längeres → Link auf docs/debug/ oder Spec.
     - Würde ein Wiederholen Schaden anrichten (Hardware zerstört, Daten weg)? → zusätzlich als
       harte Regel in CLAUDE.md unter "Projektspezifische Regeln".
     - Veraltet ein Eintrag (Bug im Tool gefixt, Bauteil getauscht)? Nicht löschen, sondern
       ~~durchstreichen~~ und "(überholt JJJJ-MM-TT: Grund)" ergänzen.
     - Reine Fakten aus Datenblatt/Errata gehören nach docs/hardware/; hier steht die Erfahrung damit (mit Link).
     - Code-Anker: // LEARNING(#tag): <Kurztitel exakt wie hier> -->

## Format

```
### <Kurztitel> #bereich #werkzeug
*<JJJJ-MM-TT> · Quelle: <Spec/Debug/Commit>*
- **Symptom:** was man beobachtet
- **Ursache:** was wirklich dahintersteckt
- **Lösung / Regel:** was man tun bzw. lassen muss
```

---

## Toolchain & Build

## Hardware

## Laufzeit & Code

## Prozess & Werkzeuge
