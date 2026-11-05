#!/usr/bin/env bash
# Create a new project from this template (Git Bash, Linux, macOS).
# Usage: scripts/neues-projekt.sh <ziel-verzeichnis> [--name "Projektname"] [--github]
set -euo pipefail

usage() {
  echo "Aufruf: $0 <ziel-verzeichnis> [--name \"Projektname\"] [--github]" >&2
  exit 1
}
fail() { echo "FEHLER: $*" >&2; exit 1; }

target="" name="" github=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name) [ $# -ge 2 ] || usage; name="$2"; shift 2 ;;
    --github) github=1; shift ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) [ -z "$target" ] || usage; target="$1"; shift ;;
  esac
done
[ -n "$target" ] || usage

# Canonical physical path: resolves symlinks (pwd -P) and, in Git Bash, 8.3 short names/junctions.
canon_dir() {
  local d
  d="$(cd "$1" && pwd -P)" || return 1
  if command -v cygpath >/dev/null 2>&1; then
    d="$(cygpath -u "$(cygpath -m -l "$d")")"
  fi
  printf '%s\n' "${d%/}"
}
# Canonicalize the deepest existing ancestor of a (possibly non-existing) path.
canon_path() {
  local p="$1" rest=""
  while [ ! -d "$p" ] && [ "$p" != "/" ] && [ "$p" != "." ]; do
    rest="/$(basename "$p")$rest"
    p="$(dirname "$p")"
  done
  printf '%s%s\n' "$(canon_dir "$p")" "$rest"
}

template_root="$(canon_dir "$(dirname "$0")/..")"

# Absolute target path (target may not exist yet). Git Bash: accept C:\... paths.
if command -v cygpath >/dev/null 2>&1; then
  target="$(cygpath -u "$target")"
fi
case "$target" in
  /*) ;;
  *) target="$PWD/$target" ;;
esac
target="${target%/}"
[ -e "$target" ] && [ ! -d "$target" ] && fail "Ziel ist eine Datei: $target"
target="$(canon_path "$target")"
case "$target" in
  */.|*/..|*/./*|*/../*) fail "Ziel enthält '.'/'..' unterhalb eines nicht existierenden Ordners: $target" ;;
esac
[ -n "$name" ] || name="$(basename "$target")"

# --- Checks ---
shopt -s nocasematch  # Windows/macOS file systems are case-insensitive
case "$target/" in
  "$template_root"/*) fail "Ziel liegt im Template-Ordner: $target" ;;
esac
shopt -u nocasematch
if [ -e "$target" ]; then
  [ -d "$target" ] || fail "Ziel ist eine Datei: $target"
  [ -z "$(ls -A "$target")" ] || fail "Ziel existiert und ist nicht leer: $target"
fi
command -v git >/dev/null || fail "git nicht gefunden."
if [ "$github" = 1 ]; then
  gh="$(command -v gh || true)"
  # Git Bash: winget installs gh outside the bash PATH.
  win_gh="/c/Program Files/GitHub CLI/gh.exe"
  [ -z "$gh" ] && [ -x "$win_gh" ] && gh="$win_gh"
  [ -n "$gh" ] || fail "gh (GitHub CLI) nicht gefunden. Installation: winget install GitHub.cli (oder https://cli.github.com)"
  "$gh" auth status >/dev/null 2>&1 || fail "gh ist nicht angemeldet. Erst 'gh auth login' ausführen."
fi

# --- Copy (everything except .git and local Claude files) ---
# tar reads the whole source tree before the target exists inside it (target is never inside the template).
mkdir -p "$target"
tar -C "$template_root" \
  --exclude='./.git' \
  --exclude='./.claude/settings.local.json' \
  --exclude='./.claude/worktrees' \
  --exclude='./CLAUDE.local.md' \
  -cf - . | tar -C "$target" -xf -
echo "Template kopiert nach $target"

# --- Project name placeholder ---
escaped=$(printf '%s' "$name" | sed -e 's/[\/&|\\]/\\&/g')
for f in CLAUDE.md docs/PROJEKT.md; do
  p="$target/$f"
  [ -f "$p" ] || continue
  sed "s|{{PROJEKTNAME}}|$escaped|g" "$p" > "$p.tmp" && mv "$p.tmp" "$p"
done

# --- Git ---
cd "$target"
git init -q -b main
git -c core.safecrlf=false add -A
# Mark shell scripts executable in the index (Windows has no exec bit; Linux/macOS clones need it).
sh_files=()
while IFS= read -r f; do sh_files+=("$f"); done < <(git ls-files -- 'scripts/*.sh' '.claude/hooks/*.sh')
[ ${#sh_files[@]} -eq 0 ] || git update-index --chmod=+x -- "${sh_files[@]}"
git commit -q -m "chore: initialize project from template"
echo "Git-Repo angelegt (Branch main, Initial-Commit)."

if [ "$github" = 1 ]; then
  # GitHub repo names: only A-Z a-z 0-9 . _ -
  repo=$(printf '%s' "$name" | sed -e 's/ä/ae/g;s/ö/oe/g;s/ü/ue/g;s/Ä/Ae/g;s/Ö/Oe/g;s/Ü/Ue/g;s/ß/ss/g' \
    | LC_ALL=C sed -e 's/[^A-Za-z0-9._-]\{1,\}/-/g' -e 's/^-*//' -e 's/-*$//')
  [ -n "$repo" ] || fail "Kein gültiger Repo-Name aus '$name' ableitbar."
  "$gh" repo create "$repo" --private --source . --push
  echo "GitHub-Repo '$repo' (privat) erstellt und gepusht."
fi

# --- Sync folder warning ---
case "$target" in
  *Dropbox*|*OneDrive*)
    echo
    echo "WARNUNG: Das Projekt liegt in einem Sync-Ordner (Dropbox/OneDrive)."
    echo "  Empfehlung: Projekte AUSSERHALB von Sync-Ordnern ablegen (z. B. C:\Projekte\... bzw. ~/Projekte/...)"
    echo "  und zwischen PCs nur per git push/pull abgleichen. Grund: Sync-Dienst und Git sind zwei"
    echo "  konkurrierende Sync-Wege - halb synchronisierte .git-Ordner gehen kaputt, und"
    echo "  maschinenspezifische Ordner (.venv, node_modules, .pio, build) gehören nicht auf andere PCs."
    echo "  Wenn es doch hier bleiben muss: den GANZEN Projektordner von Dropbox ausnehmen (nicht nur .git):"
    winpath="$target"
    command -v cygpath >/dev/null 2>&1 && winpath="$(cygpath -w "$target")"
    echo "    Windows (PowerShell): Set-Content -Path '$winpath' -Stream com.dropbox.ignored -Value 1"
    echo "    macOS:                xattr -w com.dropbox.ignored 1 '$target'"
    echo "    Linux:                attr -s com.dropbox.ignored -V 1 '$target'"
    echo "  OneDrive kennt kein Ignore pro Ordner - dort das Projekt unbedingt verschieben."
    ;;
esac

echo
echo "Nächste Schritte:"
echo "  cd \"$target\""
echo "  claude"
echo "  /kickoff"
