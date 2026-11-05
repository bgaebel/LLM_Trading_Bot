#!/usr/bin/env bash
# Create/update the standard issue labels. Idempotent (gh label create --force).
# Usage: bash scripts/github-labels.sh [owner/repo]   (default: repo of the current directory)
set -u

gh="$(command -v gh || true)"
# Fallback: default winget install path, not yet on PATH in shells opened before the install.
win_gh="/c/Program Files/GitHub CLI/gh.exe"
[ -z "$gh" ] && [ -x "$win_gh" ] && gh="$win_gh"
if [ -z "$gh" ]; then
  echo "FEHLER: GitHub CLI 'gh' nicht gefunden. Installieren: winget install GitHub.cli (oder https://cli.github.com)" >&2
  exit 1
fi
if ! "$gh" auth status >/dev/null 2>&1; then
  echo "FEHLER: gh ist nicht angemeldet. Erst 'gh auth login' ausführen." >&2
  exit 1
fi

repo_args=()
[ $# -ge 1 ] && repo_args=(--repo "$1")

# name|color (hex, no #)|description
labels=(
  "type:feature|1D76DB|Neue Funktion oder größere Änderung"
  "type:bug|D73A4A|Fehlverhalten"
  "type:chore|C5DEF5|Wartung, Build, Abhängigkeiten, Aufräumen"
  "type:docs|0075CA|Nur Dokumentation"
  "status:blockiert|000000|Blockiert – wartet auf etwas anderes"
  "server|F9D0C4|Braucht manuellen Test auf dem Hetzner-Server"
)

failed=0
for entry in "${labels[@]}"; do
  IFS='|' read -r name color desc <<< "$entry"
  if "$gh" label create "$name" --color "$color" --description "$desc" --force ${repo_args[@]+"${repo_args[@]}"} >/dev/null; then
    echo "ok      $name"
  else
    echo "FEHLER  $name" >&2
    failed=1
  fi
done
exit "$failed"
