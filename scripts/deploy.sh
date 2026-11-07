#!/usr/bin/env bash
# Deploy the bot from this machine to the server (Spec 009 AK6, ADR-0020). Runs in Git Bash.
#
#   scripts/deploy.sh <alias> <service-user> install            one-time layout, uv, time sync
#   scripts/deploy.sh <alias> <service-user> deploy [<commit>]  default: origin/main
#   scripts/deploy.sh <alias> <service-user> rollback           back to the previous release
#   scripts/deploy.sh <alias> <service-user> status
#   scripts/deploy.sh <alias> <service-user> public <domain>     HTTPS through Caddy (Spec 013)
#   scripts/deploy.sh <alias> <service-user> public off          back to the tunnel only
#   scripts/deploy.sh <alias> <service-user> impressum <file>    upload the imprint (Spec 014)
#
# Only a commit on origin/main with a green CI run is deployed. The server gets `git archive` of
# exactly that commit - nothing else from this working tree - and runs deploy/release.sh taken
# from the same commit. install, rollback, status, public and impressum run release.sh of the newest
# green commit on origin/main, so a red or still running CI on the tip never blocks a rollback.
# The imprint (operator's name and address) lives outside the repository and goes to the server only
# on stdin - never in a command line, the output or a log (Spec 014, ADR-0025).
# <alias> is the local SSH alias (~/.ssh/config), <service-user> the service account from
# setup-server.sh, <domain> the public host name: none of them is ever written into the repository.
# Never without the user's consent (CLAUDE.md).
set -euo pipefail
shopt -s inherit_errexit

GIT="${LTB_GIT:-git}"
GH="${LTB_GH:-gh}"
SSH="${LTB_SSH:-ssh}"
INCOMING=/opt/llm-trading-bot/incoming
SEARCH=30 # commits of origin/main searched for the newest green one

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '%s\n' "$*"; }

usage() {
  die "usage: scripts/deploy.sh <alias> <service-user>" \
    "install|deploy [<commit>]|rollback|status|public <domain>|public off|impressum <file>"
}

# The same pattern as in deploy/release.sh, which checks it again on the server.
HOST_RE='^[a-z0-9.-]+\.[a-z]{2,}$'
valid_host() {
  local LC_ALL=C # [a-z] means exactly a to z, whatever the locale
  [[ "$1" =~ $HOST_RE ]] && [[ "$1" != *..* ]] && [[ "$1" != [.-]* ]] \
    || die "invalid domain: $1 (lower-case, such as example.com)"
}

# The web UI's limit (Spec 014 AK3); deploy/release.sh checks everything again on the server.
IMPRESSUM_MAX=16384

# GNU grep in a UTF-8 locale: `.` matches no invalid byte sequence (overlong forms included), so a
# line that is not one match of '.*' is not UTF-8. Git Bash has no iconv, and python may be missing.
utf8_ok() {
  local rc=0
  # Without a working UTF-8 locale grep would let every byte through: prove it catches one first.
  LC_ALL=C.UTF-8 grep -aqxv '.*' <<<$'\377' \
    || die "this grep finds no invalid UTF-8 (no C.UTF-8 locale?) - cannot check the file"
  LC_ALL=C.UTF-8 grep -aqxv '.*' "$1" || rc=$?
  [ "$rc" -eq 1 ] || return 1 # 0: a line that is not UTF-8, 2: grep failed
  # Surrogates (U+D800-DFFF, bytes ED A0-BF) are no characters, but some greps let them through.
  rc=0
  LC_ALL=C grep -aq $'\xed[\xa0-\xbf]' "$1" || rc=$?
  [ "$rc" -eq 1 ]
}

# $1 normalised into $2 as the web UI reads it (no BOM, LF line ends), then checked.
prepare_impressum() {
  local src="$1" out="$2" size nul
  [ -f "$src" ] || die "not a file: $src"
  # Byte-wise (C locale): \xEF\xBB\xBF is the UTF-8 byte order mark.
  LC_ALL=C sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "$src" >"$out" || die "cannot read $src"
  size="$(wc -c <"$out")"
  size="${size//[[:space:]]/}"
  [ "$size" -le "$IMPRESSUM_MAX" ] \
    || die "$src is too big: $size B after normalising (at most $IMPRESSUM_MAX B)"
  nul="$(LC_ALL=C tr -cd '\000' <"$out" | wc -c)"
  [ "${nul//[[:space:]]/}" -eq 0 ] || die "$src contains a NUL byte"
  utf8_ok "$out" || die "$src is not UTF-8"
  LC_ALL=C grep -q '[^[:space:]]' "$out" || die "$src is empty"
  # deploy/impressum.example.md unfilled would put placeholders on a public legal page.
  if LC_ALL=C grep -qE '__[A-Z_]+__|<domain>' "$out"; then
    die "$src still holds placeholders of deploy/impressum.example.md - fill them in first"
  fi
}

[ "$#" -ge 3 ] || usage
alias_name="$1" service="$2" mode="$3"
[[ "$alias_name" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid SSH alias: $alias_name"
[[ "$service" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "invalid user name: $service"

green() {
  local runs
  runs="$("$GH" run list --commit "$1" --workflow CI --json status,conclusion \
    --jq '[.[] | select(.status == "completed" and .conclusion == "success")] | length')" \
    || die "could not ask GitHub for the CI result of $1"
  [ "${runs:-0}" -ge 1 ]
}

# The commit must be on origin/main and have a successful CI run (CLAUDE.md: main stays green).
checked_commit() {
  local ref="$1" sha
  "$GIT" fetch --quiet origin main || die "git fetch failed - origin/main may be stale"
  sha="$("$GIT" rev-parse --verify --quiet "$ref^{commit}")" || die "unknown commit: $ref"
  "$GIT" merge-base --is-ancestor "$sha" origin/main || die "$sha is not on origin/main - refused"
  green "$sha" || die "no successful CI run for $sha - refused"
  printf '%s\n' "$sha"
}

newest_green() {
  "$GIT" fetch --quiet origin main || die "git fetch failed - origin/main may be stale"
  local sha
  for sha in $("$GIT" rev-list --max-count="$SEARCH" origin/main); do
    if green "$sha"; then
      printf '%s\n' "$sha"
      return
    fi
  done
  die "no commit with a successful CI run among the last $SEARCH of origin/main"
}

# deploy/release.sh of a given commit, piped into sudo bash on the server.
remote() {
  local sha="$1"
  shift
  "$GIT" show "$sha:deploy/release.sh" | "$SSH" "$alias_name" "sudo bash -s -- $*"
}

case "$mode" in
  install | rollback | status)
    [ "$#" -eq 3 ] || usage
    sha="$(newest_green)"
    if [ "$mode" = status ]; then remote "$sha" status; else remote "$sha" "$mode" "'$service'"; fi
    ;;
  deploy)
    [ "$#" -le 4 ] || usage
    sha="$(checked_commit "${4:-origin/main}")"
    log "deploying $sha to $alias_name"
    # Upload first (one connection), then run (a second one): the ufw rate limit allows 6 in 30 s.
    "$GIT" archive --format=tar.gz "$sha" \
      | "$SSH" "$alias_name" "sudo install -d -m 700 $INCOMING && sudo tee $INCOMING/$sha.tar.gz >/dev/null"
    remote "$sha" deploy "$sha" "'$service'"
    ;;
  public)
    [ "$#" -eq 4 ] || usage
    target="$4"
    if [ "$target" != off ]; then valid_host "$target"; fi
    sha="$(newest_green)"
    # One connection: release.sh takes the deploy lock and does everything in order (Spec 013 AK8).
    if [ "$target" = off ]; then
      remote "$sha" public-off "'$service'"
    else
      remote "$sha" public "'$target'" "'$service'"
    fi
    ;;
  impressum)
    [ "$#" -eq 4 ] || usage
    normalised="$(mktemp)"
    trap 'rm -f "$normalised"' EXIT
    prepare_impressum "$4" "$normalised"
    digest="$(sha256sum <"$normalised" | awk '{print $1}')"
    sha="$(newest_green)" # before the upload: a refusal leaves nothing on the server
    # Right after a merge the newest green commit may predate Spec 014: its release.sh would refuse
    # the mode only after the personal data was uploaded.
    "$GIT" show "$sha:deploy/release.sh" | grep -q '^    impressum)' \
      || die "the newest green commit ${sha:0:7} has no impressum mode - wait for CI on main"
    log "uploading the imprint ($(wc -c <"$normalised" | tr -d '[:space:]') B, sha256 ${digest:0:12})" \
      "to $alias_name"
    # Like a release: upload outside the deploy lock (one connection), install under it (a second one).
    "$SSH" "$alias_name" \
      "sudo install -d -m 700 $INCOMING && sudo tee $INCOMING/impressum-$digest.md >/dev/null" \
      <"$normalised"
    remote "$sha" impressum "$digest"
    ;;
  *) usage ;;
esac
