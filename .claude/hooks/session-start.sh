#!/usr/bin/env bash
# SessionStart hook: prints a compact git/work snapshot that Claude receives as context.
# Must never fail or block the session — every command is guarded and the script always exits 0.
# docs/STAND.md and the ADR index are NOT printed here; CLAUDE.md already imports them.

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

echo "=== Session-Kontext (automatisch) ==="

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch=$(git branch --show-current 2>/dev/null)
  echo "Branch: ${branch:-(detached)}"

  # Ahead/behind relative to the last fetch (no network access here; /start fetches).
  if upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
    counts=$(git rev-list --left-right --count "HEAD...$upstream" 2>/dev/null)
    echo "Upstream: $upstream (vor/zurück seit letztem fetch: ${counts/$'\t'/ / })"
  else
    echo "Upstream: keiner gesetzt"
  fi

  changes=$(git -c core.quotePath=false status --porcelain 2>/dev/null)
  if [ -n "$changes" ]; then
    echo "Uncommittete Änderungen ($(printf '%s\n' "$changes" | wc -l | tr -d ' ')):"
    printf '%s\n' "$changes" | head -n 15
  else
    echo "Arbeitsbaum: sauber"
  fi

  echo "Letzte Commits:"
  git log --oneline -n 5 2>/dev/null

  worktrees=$(git worktree list 2>/dev/null | wc -l | tr -d ' ')
  [ "${worktrees:-1}" -gt 1 ] && echo "Hinweis: $((worktrees - 1)) zusätzliche git-Worktrees vorhanden – ggf. aufräumen."
else
  echo "Kein git-Repository."
fi

# Active specs and open debug sessions (status line in file header). Same globs as scripts/docs-check.sh.
active=$(grep -l -E '^- \*\*Status:\*\* (in Arbeit|freigegeben)' docs/specs/[0-9][0-9][0-9]*-*.md 2>/dev/null)
[ -n "$active" ] && echo "Aktive Specs: $(echo $active | tr '\n' ' ')"
open_debug=$(grep -l -E '^- \*\*Status:\*\* (offen|pausiert)' docs/debug/[0-9][0-9][0-9]*-*.md 2>/dev/null)
[ -n "$open_debug" ] && echo "Offene Debug-Sitzungen: $(echo $open_debug | tr '\n' ' ')"

# Dropbox/OneDrive conflict copies are gitignored, so git status hides them – but they mean
# another PC's change did not land in the original file.
conflicts=$(find . -maxdepth 5 \( -name .git -o -name node_modules -o -name .venv -o -name .pio -o -name build \) -prune -o \
  \( -iname '*in Konflikt stehende Kopie*' -o -iname '*conflicted copy*' \) -print 2>/dev/null | head -n 5)
[ -n "$conflicts" ] && printf 'WARNUNG: Cloud-Sync-Konfliktkopien gefunden – Inhalt prüfen und zusammenführen:\n%s\n' "$conflicts"

# The "Stack:" placeholder is only replaced by /kickoff (the copy scripts replace {{PROJEKTNAME}} earlier).
if grep -q '^Stack: {{' CLAUDE.md 2>/dev/null; then
  echo "HINWEIS: Projekt ist noch nicht eingerichtet – dem Nutzer /kickoff vorschlagen."
fi

exit 0
