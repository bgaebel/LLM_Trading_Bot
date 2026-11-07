# Spec 017: Strategie-Labor III – Auswertung der Live-Signale

- **Status:** Entwurf
- **Hinweis:** Skizze; wird ausgearbeitet und zur Freigabe vorgelegt, sobald ≥ 4 Wochen Beobachtungen (Spec 015) vorliegen.
- **Issue:** – · **Branch:** `feat/s017-signal-auswertung` · **Meilenstein:** M3 (vorgezogen: Strategie-Labor)
- **Erstellt:** 2026-11-07

## Ziel
Ein Bericht auf Anforderung zeigt, was die Live-Signale wert sind und ob das Kostenmodell stimmt – ehrlich über die
kleine Datenmenge.

## Bausteine (Skizze, Festlegungen aus der Prüfung vom 2026-11-07)
- **`lab-copy`:** `release.sh lab-copy` unter der Deploy-Sperre, Online-Backups als Dienstbenutzer in
  `$BACKUPS/lab-copy/` (eine Kopie, temporäre Namen, Aufräumen auch bei Fehler), Abholen per `ssh … sudo cat` mit
  SHA-256-Abgleich, höchstens drei SSH-Verbindungen; `daily/` und `pre-update/` unberührt. Jede Kopie braucht die
  Zustimmung des Nutzers; kein Zeitplan. Die Kopie ist keine Sicherung im Sinne von ADR-0020.
- **Bericht v1 (beantwortbar mit wenigen Wochen):** Datenqualität und Sperrzeiten, Kosten-Realität (Spread, Tiefe gegen
  Ordergröße, Fill gegen Quote), Zählungen von Entscheidungen und Auslösern, Beinahe-Abstände als Verteilung.
- **Ab Mindestfallzahl, kumuliert seit Aufzeichnungsbeginn:**
  - Ereignisse (Wechsel des wirksamen Ziels, echte Trades) getrennt von Zuständen (Rendite je Zustand, nicht
    überlappend gezogen);
  - SELL zählt als Ausstieg nur mit Position;
  - Einstieg = erste 1m-Eröffnung ≥ Entscheidung innerhalb 2 min, Ausstieg = letzter 1m-Schluss ≤ t + h innerhalb
    2 min, Ausschlüsse gezählt;
  - jede Trefferquote neben ihrer Basisrate;
  - GPT-Kalibrierung mit definiertem Ereignis je Aktion, Brier-Skill gegen die Basisrate, 2–3 Klassen;
  - κ gegen Referenzsignale; Luna gegen Sol erst nach einer Mindestzahl Wochen;
  - kontrafaktischer Ertrag nur über einen golden-geprüften Nachspiel-Pfad.
- **Register:** Jede Auswertung, aus der ein Kandidat entstehen kann (z. B. Schwellen-Vergleich über/unter), wird als
  Versuch registriert – oder ist ausdrücklich nur beschreibend.
