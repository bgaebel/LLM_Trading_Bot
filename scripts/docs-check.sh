#!/usr/bin/env bash
# Documentation hygiene check. Errors exit non-zero, warnings never fail.
# Usage: bash scripts/docs-check.sh [repo-root]   (default: parent of this script's dir)
set -u

root="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$root" || { echo "ERROR: cannot cd to $root" >&2; exit 2; }

errors=0
warnings=0
gha="${GITHUB_ACTIONS:-}"

err() {
  errors=$((errors + 1))
  if [ -n "$gha" ]; then echo "::error file=$1::$2"; else echo "FEHLER  $1: $2"; fi
}
warn() {
  warnings=$((warnings + 1))
  if [ -n "$gha" ]; then echo "::warning file=$1::$2"; else echo "WARNUNG $1: $2"; fi
}
# Line count that also counts a last line without trailing newline.
count_lines() { awk 'END { print NR }' "$1"; }

# (a) ADR files <-> INDEX.md
adr_dir="docs/entscheidungen"
index="$adr_dir/INDEX.md"
if [ -d "$adr_dir" ]; then
  if [ ! -f "$index" ]; then
    err "$index" "fehlt"
  else
    for f in "$adr_dir"/[0-9][0-9][0-9][0-9]-*.md; do
      [ -e "$f" ] || continue
      name="$(basename "$f")"
      [ "$name" = "0000-vorlage.md" ] && continue
      grep -qF -e "($name)" -e "(./$name)" "$index" || err "$index" "ADR $name ist nicht im Index verlinkt"
    done
    # Every link target in the index must exist.
    while IFS= read -r link; do
      [ -n "$link" ] || continue
      [ -f "$adr_dir/$link" ] || err "$index" "verlinktes ADR $link existiert nicht"
    done < <(tr -d '\r' < "$index" | grep -oE '\((\./)?[0-9]{4}-[^)]*\.md\)' | sed -E 's#^\((\./)?##; s#\)$##' | sort -u)
  fi
fi

# (b) STAND.md length
stand="docs/STAND.md"
if [ -f "$stand" ]; then
  n=$(count_lines "$stand")
  if [ "$n" -gt 60 ]; then err "$stand" "$n Zeilen (max. 60) – kürzen, Altes nach Specs/ROADMAP verschieben"
  elif [ "$n" -gt 40 ]; then warn "$stand" "$n Zeilen (Ziel: max. 40)"
  fi
else
  err "$stand" "fehlt"
fi

# (c) CLAUDE.md length
if [ -f "CLAUDE.md" ]; then
  n=$(count_lines "CLAUDE.md")
  [ "$n" -gt 150 ] && warn "CLAUDE.md" "$n Zeilen (Ziel: max. ~120, Warnung ab 150)"
fi

# Status line check: check_status <file> <ERE for allowed value> <human-readable list>
# Reads the first "- **Status:**" line; the ERE is anchored at the start only (prefix match).
check_status() {
  local f="$1" re="$2" list="$3" line value
  line="$(tr -d '\r' < "$f" | grep -m1 -E '^- \*\*Status:\*\*' || true)"
  if [ -z "$line" ]; then
    err "$f" "Zeile '- **Status:** ...' fehlt"
    return
  fi
  value="$(printf '%s' "$line" | sed -E 's/^- \*\*Status:\*\*[[:space:]]*//; s/[[:space:]]+$//')"
  printf '%s' "$value" | grep -qE "$re" || err "$f" "ungültiger Status '$value' (erlaubt: $list)"
}

# (d) Spec status (same glob as .claude/hooks/session-start.sh)
for f in docs/specs/[0-9][0-9][0-9]*-*.md; do
  [ -e "$f" ] || continue
  check_status "$f" '^(Entwurf|freigegeben|in Arbeit|fertig|verworfen)$' \
    'Entwurf | freigegeben | in Arbeit | fertig | verworfen'
done

# (e) ADR status ("abgelöst durch ..." is a prefix match)
for f in docs/entscheidungen/[0-9][0-9][0-9][0-9]-*.md; do
  [ -e "$f" ] || continue
  [ "$(basename "$f")" = "0000-vorlage.md" ] && continue
  check_status "$f" '^(vorgeschlagen$|angenommen$|verworfen$|abgelöst durch )' \
    'vorgeschlagen | angenommen | verworfen | abgelöst durch …'
done

# (f) Debug protocol status (prefix match, e.g. "aufgegeben (Grund)")
for f in docs/debug/[0-9][0-9][0-9]*-*.md; do
  [ -e "$f" ] || continue
  check_status "$f" '^(offen|pausiert|gelöst|aufgegeben)([[:space:](]|$)' \
    'offen | pausiert | gelöst | aufgegeben (Grund)'
done

# (g) Git conflict markers in docs/ ("=======" alone is skipped: valid Markdown heading underline)
if [ -d docs ]; then
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    err "${hit%%:*}" "Git-Konfliktmarker: ${hit#*:}"
  done < <(grep -rnE '^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)( |$)' docs 2>/dev/null | tr -d '\r')
fi

echo "docs-check: $errors Fehler, $warnings Warnungen"
[ "$errors" -eq 0 ]
