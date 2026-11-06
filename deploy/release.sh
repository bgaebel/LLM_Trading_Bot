#!/usr/bin/env bash
# Install, update and roll back the bot on the server (Spec 009, ADR-0018).
# Origin: LLM-Trading-Arena@7563724 apps/arena/scripts/deploy_release.py (adapted): build the
# candidate while the old service runs, stop, back up, switch, health check, on failure roll back
# code and units only - never the database.
#
# Runs as root on the server, piped in via SSH (no copy stays behind); scripts/deploy.sh does this:
#   ssh <alias> "sudo bash -s -- install '<service>'" < deploy/release.sh
#   ssh <alias> "sudo bash -s -- deploy <sha> '<service>'" < deploy/release.sh   (archive uploaded first)
#   ssh <alias> "sudo bash -s -- rollback '<service>'" < deploy/release.sh
#   ssh <alias> "sudo bash -s -- status" < deploy/release.sh
#
# Real host names, IPs and user names never go into this file: the repository is public.
# LTB_* variables exist only for the sandbox tests (tests/deploy/test_release.py).
set -euo pipefail

ROOT="${LTB_ROOT:-}"
APP="$ROOT/opt/llm-trading-bot"
RELEASES="$APP/releases"
CURRENT="$APP/current"
PREVIOUS="$APP/previous"
INCOMING="$APP/incoming"
DATA="$ROOT/var/lib/llm-trading-bot"
BACKUPS="$ROOT/var/backups/llm-trading-bot"
UNIT_DIR="$ROOT/etc/systemd/system"
JOURNALD_DIR="$ROOT/etc/systemd"
UV_CACHE="$ROOT/var/cache/llm-trading-bot-uv"
LOCK="$ROOT/run/lock/llm-trading-bot-deploy.lock"
UV="${LTB_UV:-$ROOT/usr/local/bin/uv}"
SYSTEMCTL="${LTB_SYSTEMCTL:-systemctl}"
ANALYZE="${LTB_ANALYZE:-systemd-analyze}"
RUNUSER="${LTB_RUNUSER:-runuser}"
HEALTH_SECONDS="${LTB_HEALTH_SECONDS:-180}"
POLL_SECONDS="${LTB_POLL_SECONDS:-3}"
KEEP_RELEASES=3 # the current one and two before it

# uv in a fixed version; the checksum comes from the release page and the GitHub API (both agree).
UV_VERSION="0.13.5"
UV_URL="${LTB_UV_URL:-https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz}"
UV_SHA256="${LTB_UV_SHA256:-f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f}"

BOT=llm-trading-bot.service
WEB=llm-trading-bot-web.service
TIMER=llm-trading-bot-backup.timer
UNITS=(llm-trading-bot.service llm-trading-bot-web.service llm-trading-bot-backup.service
  llm-trading-bot-backup.timer)
JOURNALD_CONF=journald@llm-trading-bot.conf

changes=0

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

testing() { [ -n "$ROOT" ]; }

require_root() {
  testing || [ "$(id -u)" -eq 0 ] || die "must run as root"
}

valid_name() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "invalid user name: $1"
  testing || id -u "$1" >/dev/null 2>&1 || die "no such user: $1 (created by setup-server.sh)"
}

valid_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]] || die "not a full commit hash: $1"
}

# Ownership needs root; the sandbox tests run without it.
own() {
  testing || chown "$@"
}

as_service() {
  local user="$1"
  shift
  if testing; then
    "$@"
  else
    "$RUNUSER" -u "$user" -- "$@"
  fi
}

ensure_dir() {
  local path="$1" mode="$2" owner="$3"
  if [ -d "$path" ]; then
    log "unchanged  $path"
  else
    install -d -m "$mode" "$path"
    log "created    $path"
    changes=$((changes + 1))
  fi
  chmod "$mode" "$path"
  own "$owner" "$path"
}

# --------------------------------------------------------------------------------- install

install_uv() {
  if [ -x "$UV" ] && [ "$("$UV" --version 2>/dev/null | awk '{print $2}')" = "$UV_VERSION" ]; then
    log "unchanged  uv $UV_VERSION"
    return
  fi
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL --proto '=https,file' -o "$tmp/uv.tar.gz" "$UV_URL" || die "uv download failed"
  printf '%s  %s\n' "$UV_SHA256" "$tmp/uv.tar.gz" | sha256sum -c --quiet - \
    || die "uv checksum mismatch - nothing installed"
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  install -d -m 755 "$(dirname "$UV")"
  install -m 755 "$tmp/uv-x86_64-unknown-linux-gnu/uv" "$UV"
  rm -rf "$tmp"
  log "installed  uv $UV_VERSION"
  changes=$((changes + 1))
}

cmd_install() {
  local service="$1"
  require_root
  valid_name "$service"
  ensure_dir "$APP" 755 root:root
  ensure_dir "$RELEASES" 755 root:root
  ensure_dir "$INCOMING" 700 root:root
  ensure_dir "$DATA" 700 "$service:"
  ensure_dir "$BACKUPS" 700 "$service:"
  ensure_dir "$BACKUPS/daily" 700 "$service:"
  ensure_dir "$BACKUPS/pre-update" 700 "$service:"
  install_uv
  # ADR-0018: time-sync.target only waits for NTP when this service is enabled.
  if "$SYSTEMCTL" is-enabled --quiet systemd-time-wait-sync.service; then
    log "unchanged  systemd-time-wait-sync enabled"
  else
    "$SYSTEMCTL" enable systemd-time-wait-sync.service
    log "enabled    systemd-time-wait-sync"
    changes=$((changes + 1))
  fi
  log "changes: $changes"
}

# --------------------------------------------------------------------------------- releases

check_archive() {
  local archive="$1" listing
  listing="$(tar -tvzf "$archive")" || die "archive unreadable: $archive"
  # Links could point anywhere on the server; the repository has none.
  if printf '%s\n' "$listing" | awk '{print substr($1, 1, 1)}' | grep -qv '^[-d]$'; then
    die "archive contains links or special files - refused"
  fi
  local name
  while IFS= read -r name; do
    case "/$name" in
      */../* | */..) die "archive path leaves the release: $name" ;;
    esac
    [[ "$name" != /* ]] || die "absolute path in archive: $name"
    local base="${name##*/}"
    case "$base" in
      secrets.env | web-auth.json | .env | .env.* | *.db | *.db-* | *.db.* | *.sqlite*)
        die "archive contains a secret or database file: $name - refused"
        ;;
    esac
  done < <(tar -tzf "$archive")
}

build_release() {
  local sha="$1" archive="$INCOMING/$1.tar.gz" target="$RELEASES/$1"
  if [ -f "$target/.ready" ]; then
    log "release $sha already built"
    return
  fi
  [ -f "$archive" ] || die "no archive $archive - upload it first (scripts/deploy.sh)"
  check_archive "$archive"
  # Built in place, not moved afterwards: the venv's scripts carry its absolute path. A build
  # without .ready (interrupted) is thrown away and done again.
  rm -rf "$target"
  install -d -m 755 "$target"
  tar -xzf "$archive" -C "$target" --no-same-owner --no-same-permissions
  install -d -m 700 "$UV_CACHE"
  # System Python 3.12 only (no downloads); exactly the locked versions, no dev tools, the
  # project copied into the venv (not linked to the source tree).
  (cd "$target" && UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR="$UV_CACHE" \
    "$UV" sync --frozen --no-dev --no-editable --python python3.12 --quiet) </dev/null \
    || die "uv sync failed for $sha - nothing changed"
  chmod -R a+rX,go-w "$target"
  "$target/.venv/bin/trading-bot" --help >/dev/null || die "the new release does not start"
  touch "$target/.ready"
  log "built release $sha"
}

# Replace the placeholders of a release's deploy/ files; ExecStart points at $app.
render_units() {
  local release="$1" service="$2" app="$3" out="$4"
  install -d -m 755 "$out"
  local unit
  for unit in "${UNITS[@]}" "$JOURNALD_CONF"; do
    [ -f "$release/deploy/$unit" ] || die "release lacks deploy/$unit"
    sed -e "s|__SERVICE_USER__|$service|g" -e "s|__APP_DIR__|$app|g" \
      -e "s|__DATA_DIR__|$DATA|g" -e "s|__BACKUP_DIR__|$BACKUPS|g" \
      "$release/deploy/$unit" >"$out/$unit"
  done
  if grep -l '__[A-Z_]*__' "$out"/*; then
    die "placeholder left in the rendered units"
  fi
}

verify_units() {
  local release="$1" service="$2" tmp
  tmp="$(mktemp -d)"
  # Verify against the release itself: "current" may not exist yet on the first install.
  render_units "$release" "$service" "$release" "$tmp"
  local paths=()
  local unit
  for unit in "${UNITS[@]}"; do
    paths+=("$tmp/$unit")
  done
  "$ANALYZE" verify "${paths[@]}" || die "systemd-analyze rejected the units of $release"
  rm -rf "$tmp"
}

install_units() {
  local release="$1" service="$2" tmp unit
  tmp="$(mktemp -d)"
  render_units "$release" "$service" "$CURRENT" "$tmp"
  install -d -m 755 "$UNIT_DIR"
  for unit in "${UNITS[@]}"; do
    install -m 644 "$tmp/$unit" "$UNIT_DIR/$unit"
  done
  local journald="$JOURNALD_DIR/$JOURNALD_CONF"
  if ! cmp -s "$tmp/$JOURNALD_CONF" "$journald"; then
    install -m 644 "$tmp/$JOURNALD_CONF" "$journald"
    # The namespace's journald picks up new limits on its next start.
    "$SYSTEMCTL" try-restart "systemd-journald@llm-trading-bot.service" || true
  fi
  rm -rf "$tmp"
  "$SYSTEMCTL" daemon-reload
}

switch_to() {
  local sha="$1"
  ln -sfn "$RELEASES/$sha" "$APP/.current.new"
  mv -T "$APP/.current.new" "$CURRENT"
}

current_sha() {
  if [ -L "$CURRENT" ]; then basename "$(readlink "$CURRENT")"; fi
}

# What never changes during a deploy: the credentials and the admin's drop-ins.
fingerprint() {
  {
    if [ -f "$DATA/secrets.env" ]; then sha256sum "$DATA/secrets.env"; fi
    if [ -d "$UNIT_DIR" ]; then
      find "$UNIT_DIR" -path '*/llm-trading-bot*.service.d/*' -type f -exec sha256sum {} + | sort
    fi
  } | sha256sum
}

# --------------------------------------------------------------------------------- health

# Healthy: the status file names the new main process as running, with an ok round that
# started after $since (Spec 009 AK5). Read with the system Python (json, no venv needed).
healthy() {
  local since="$1" pid
  pid="$("$SYSTEMCTL" show -p MainPID --value "$BOT")"
  [ -n "$pid" ] && [ "$pid" != "0" ] || return 1
  "$SYSTEMCTL" is-active --quiet "$WEB" || return 1
  python3 - "$DATA/service-status.json" "$pid" "$since" <<'PY'
import json, sys
from datetime import datetime
path, pid, since = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
try:
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    started = datetime.fromisoformat(s["last_round_at"]).timestamp() if s["last_round_at"] else 0
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
ok = s["state"] == "running" and s["pid"] == pid and s["last_result"] == "ok" and started >= since
sys.exit(0 if ok else 1)
PY
}

wait_healthy() {
  local since="$1" deadline=$((SECONDS + HEALTH_SECONDS))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if healthy "$since"; then return 0; fi
    sleep "$POLL_SECONDS"
  done
  return 1
}

# --------------------------------------------------------------------------------- activate

stop_all() {
  "$SYSTEMCTL" stop "$TIMER" "$BOT" "$WEB" 2>/dev/null || true
}

pre_update_backup() {
  local release="$1" service="$2"
  [ -f "$DATA/bot.db" ] || return 0
  as_service "$service" "$release/.venv/bin/trading-bot" backup --db "$DATA/bot.db" \
    --dir "$BACKUPS/pre-update" --keep 5 </dev/null
}

# Units, switch, start. Returns 0 healthy, 1 unhealthy, 2 web only (no secrets.env yet).
start_release() {
  local sha="$1" service="$2" since
  install_units "$RELEASES/$sha" "$service"
  switch_to "$sha"
  "$SYSTEMCTL" enable --now "$WEB" "$TIMER" </dev/null
  if [ ! -f "$DATA/secrets.env" ]; then
    "$SYSTEMCTL" disable --now "$BOT" </dev/null 2>/dev/null || true
    return 2
  fi
  since="$(date +%s)"
  "$SYSTEMCTL" enable "$BOT" </dev/null
  "$SYSTEMCTL" restart "$BOT" </dev/null
  wait_healthy "$since"
}

activate() {
  local sha="$1" service="$2" before old result=0
  old="$(current_sha)"
  if [ -f "$DATA/bot.db" ]; then
    as_service "$service" "$RELEASES/$sha/.venv/bin/trading-bot" verify-db --db "$DATA/bot.db" \
      </dev/null || die "the release $sha cannot use the database - nothing changed"
  fi
  verify_units "$RELEASES/$sha" "$service"
  before="$(fingerprint)"
  stop_all
  if ! pre_update_backup "$RELEASES/$sha" "$service"; then
    [ -z "$old" ] || start_release "$old" "$service" || true
    die "backup before the update failed - stayed on ${old:-nothing}"
  fi
  start_release "$sha" "$service" || result=$?
  [ "$(fingerprint)" = "$before" ] || die "secrets.env or a drop-in changed during the deploy"
  if [ "$result" -eq 2 ]; then
    log "no secrets.env yet: only the web UI runs. Set password, key and depots through the"
    log "SSH tunnel (setup code: journalctl --namespace=llm-trading-bot -u $WEB), then deploy again."
  elif [ "$result" -ne 0 ]; then
    log "release $sha did not become healthy within $HEALTH_SECONDS s"
    if [ -z "$old" ] || [ "$old" = "$sha" ]; then
      stop_all
      die "no other release to fall back to - bot stopped, web UI stopped"
    fi
    stop_all
    log "rolling back to $old (code and units only, the database stays as it is)"
    if start_release "$old" "$service"; then
      die "deploy of $sha failed - rolled back to $old, which is healthy"
    fi
    die "deploy of $sha failed and $old is not healthy either - check the journal"
  fi
  if [ -n "$old" ] && [ "$old" != "$sha" ]; then
    ln -sfn "$RELEASES/$old" "$APP/.previous.new"
    mv -T "$APP/.previous.new" "$PREVIOUS"
  fi
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$sha" "active" >>"$APP/deployed.log"
  log "release $sha active${old:+ (previous: $old)}"
}

prune() {
  local keep=() sha
  keep+=("$(current_sha)")
  if [ -L "$PREVIOUS" ]; then keep+=("$(basename "$(readlink "$PREVIOUS")")"); fi
  local count=0
  while IFS= read -r sha; do
    count=$((count + 1))
    local kept=0 k
    for k in "${keep[@]}"; do [ "$k" = "$sha" ] && kept=1; done
    if [ "$kept" -eq 0 ] && [ "$count" -gt "$KEEP_RELEASES" ]; then
      rm -rf "${RELEASES:?}/$sha"
      log "removed old release $sha"
    fi
  done < <(find "$RELEASES" -mindepth 2 -maxdepth 2 -name .ready -printf '%T@ %h\n' \
    | sort -rn | awk '{print $2}' | xargs -r -n1 basename)
}

with_lock() {
  install -d -m 755 "$(dirname "$LOCK")"
  exec 9>"$LOCK"
  flock -n 9 || die "another deploy is running"
}

cmd_deploy() {
  local sha="$1" service="$2"
  require_root
  valid_sha "$sha"
  valid_name "$service"
  [ -x "$UV" ] || die "uv missing - run install first"
  with_lock
  build_release "$sha"
  activate "$sha" "$service"
  rm -f "$INCOMING/$sha.tar.gz"
  prune
}

cmd_rollback() {
  local service="$1" target
  require_root
  valid_name "$service"
  with_lock
  [ -L "$PREVIOUS" ] || die "no previous release recorded"
  target="$(basename "$(readlink "$PREVIOUS")")"
  [ -f "$RELEASES/$target/.ready" ] || die "previous release $target is gone"
  activate "$target" "$service"
}

cmd_status() {
  log "current:  $(current_sha)"
  if [ -L "$PREVIOUS" ]; then log "previous: $(basename "$(readlink "$PREVIOUS")")"; fi
  log "releases: $(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f ' 2>/dev/null)"
  local unit
  for unit in "$BOT" "$WEB" "$TIMER"; do
    log "$unit: $("$SYSTEMCTL" is-active "$unit" 2>/dev/null || true)"
  done
  if [ -f "$DATA/service-status.json" ]; then
    log "status file: $(tr -d '\n' <"$DATA/service-status.json" | cut -c1-300)"
  fi
  log "latest backups: $(find "$BACKUPS" -name 'bot-*.db' -printf '%P ' 2>/dev/null | tr ' ' '\n' \
    | sort | tail -n 3 | tr '\n' ' ')"
}

# Everything above is only definitions: bash has read the whole script before main runs, so a
# command that reads stdin cannot swallow the rest of the piped-in script.
main() {
  exec </dev/null
  local mode="${1:-}"
  case "$mode" in
    install) [ "$#" -eq 2 ] || die "usage: install <service-user>"; cmd_install "$2" ;;
    deploy) [ "$#" -eq 3 ] || die "usage: deploy <sha> <service-user>"; cmd_deploy "$2" "$3" ;;
    rollback) [ "$#" -eq 2 ] || die "usage: rollback <service-user>"; cmd_rollback "$2" ;;
    status) cmd_status ;;
    *) die "usage: install|deploy|rollback|status (see the header)" ;;
  esac
}

main "$@"
