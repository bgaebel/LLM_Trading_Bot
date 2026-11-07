#!/usr/bin/env bash
# Deploy the bot from this machine to the server (Spec 009 AK6, ADR-0020). Runs in Git Bash.
#
#   scripts/deploy.sh <alias> <service-user> install            one-time layout, uv, time sync
#   scripts/deploy.sh <alias> <service-user> deploy [<commit>]  default: origin/main
#   scripts/deploy.sh <alias> <service-user> rollback           back to the previous release
#   scripts/deploy.sh <alias> <service-user> status
#
# Only a commit on origin/main with a green CI run is deployed. The server gets `git archive` of
# exactly that commit - nothing else from this working tree - and runs deploy/release.sh taken
# from the same commit. install, rollback and status run release.sh of the newest green commit on
# origin/main, so a red or still running CI on the tip never blocks a rollback.
# <alias> is the local SSH alias (~/.ssh/config), <service-user> the service account from
# setup-server.sh: neither is ever written into the repository.
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

usage() { die "usage: scripts/deploy.sh <alias> <service-user> install|deploy [<commit>]|rollback|status"; }

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
  *) usage ;;
esac
