---
name: start
description: Session-Start bzw. Gerätewechsel – Repo synchronisieren, richtigen Branch finden, Stand und aktive Arbeit laden, nächsten Schritt vorschlagen.
---

# /start – Session beginnen

1. **Synchronisieren:** `git fetch --prune`. Ist der Arbeitsbaum sauber und der Branch nur hinter dem Remote:
   `git pull --ff-only`. Bei lokalen Änderungen oder divergiertem Branch **nicht** automatisch mergen –
   Lage berichten und fragen.
2. **Richtigen Branch finden:** Auf einem anderen Gerät wurde vielleicht auf einem Feature-Branch weitergearbeitet:
   `git for-each-ref --sort=-committerdate --count=5 refs/remotes/origin --format='%(refname:short) %(committerdate:relative) %(subject)'`.
   Ist ein anderer Branch neuer als der aktuelle (typisch: `wip:`-Commit von `/ende`), anbieten, ihn auszuchecken
   (`git switch <branch>`). Danach gilt dessen `docs/STAND.md`.
3. **Stand laden:** `docs/STAND.md` ist über `CLAUDE.md` im Kontext – nach Pull oder Branchwechsel aber neu lesen.
   Ist eine Spec „in Arbeit": diese Spec lesen (Plan + Verlauf). Ist eine Debug-Sitzung offen/pausiert:
   deren Hypothesentabelle lesen.
4. **Plausibilisieren:** Passt der Stand zur Realität? (Branch aus STAND.md = aktueller Branch? Abgehakte
   Plan-Schritte = vorhandene Commits?) Abweichungen benennen.
5. **GitHub (falls `gh` angemeldet):** `gh pr list --author @me` und `gh issue list --limit 10`
   – nur erwähnen, was für den nächsten Schritt relevant ist (offene PR mit Review, rote CI, neues Bug-Issue).
6. **Bericht (max. 8 Zeilen):** Wo stehen wir · was ist offen/blockiert · vorgeschlagener nächster Schritt
   als konkreter Befehl (z. B. `/umsetzen 004`, `/diagnose 002`, `/spec ...`). Hinweise des Session-Hooks
   (Worktrees, Dropbox-Konfliktkopien) mit aufführen. Dann auf den Nutzer warten.
