#!/usr/bin/env bash
# Install, update and roll back the bot on the server (Spec 009, ADR-0020).
# Origin: LLM-Trading-Arena@7563724 apps/arena/scripts/deploy_release.py (adapted): build the
# candidate while the old service runs, stop, back up, switch, health check, on failure roll back
# code and units only - never the database.
#
# Runs as root on the server, piped in via SSH (no copy stays behind); scripts/deploy.sh does this:
#   ssh <alias> "sudo bash -s -- install '<service>'" < deploy/release.sh
#   ssh <alias> "sudo bash -s -- deploy <sha> '<service>'" < deploy/release.sh   (archive uploaded first)
#   ssh <alias> "sudo bash -s -- rollback '<service>'" < deploy/release.sh
#   ssh <alias> "sudo bash -s -- status" < deploy/release.sh
#   ssh <alias> "sudo bash -s -- public '<domain>' '<service>'" < deploy/release.sh   (Spec 013)
#   ssh <alias> "sudo bash -s -- public-off '<service>'" < deploy/release.sh
#   ssh <alias> "sudo bash -s -- impressum <sha256>" < deploy/release.sh   (Spec 014, file uploaded first)
#
# Public HTTPS (Spec 013, ADR-0024): the domain lives only in /etc/llm-trading-bot/public-host. With
# that file, deploy renders the release's deploy/Caddyfile (optional per release), validates it before
# anything stops and installs it only once the new release is active; a rollback leaves caddy alone.
#
# Imprint (Spec 014, ADR-0025): the operator's name and address live only in
# /etc/llm-trading-bot/impressum.md; the web UI reads it on every request. Its content never goes to
# the output or deploy.log.
#
# Observations (Spec 015, ADR-0027): observations.db next to bot.db holds measurements only. Its
# backup before an update is one more copy that may fail with a warning, never a stop; a release
# from before 015 neither backs it up nor reads it, and a rollback leaves the file and its copies.
#
# Real host names, IPs, domains and user names never go into this file: the repository is public.
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
# In the app's own directory: /run/lock belongs to the system (1777) and must not be touched.
LOCK="$APP/.deploy.lock"
LOGFILE="$APP/deploy.log"
UV="${LTB_UV:-$ROOT/usr/local/bin/uv}"
SYSTEMCTL="${LTB_SYSTEMCTL:-systemctl}"
ANALYZE="${LTB_ANALYZE:-systemd-analyze}"
RUNUSER="${LTB_RUNUSER:-runuser}"
HEALTH_SECONDS="${LTB_HEALTH_SECONDS:-180}"
POLL_SECONDS="${LTB_POLL_SECONDS:-3}"
KEEP_RELEASES=3 # the current one and two before it

# uv in a fixed version; the checksum comes from the release page and the GitHub API (both agree).
# It must satisfy the uv_build bound in pyproject.toml, so the build backend is uv itself and
# nothing unpinned is fetched from PyPI (tests/deploy/test_release.py checks this).
UV_VERSION="0.13.7"
UV_URL="${LTB_UV_URL:-https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz}"
UV_SHA256="${LTB_UV_SHA256:-c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83}"

BOT=llm-trading-bot.service
WEB=llm-trading-bot-web.service
TIMER=llm-trading-bot-backup.timer
UNITS=(llm-trading-bot.service llm-trading-bot-web.service llm-trading-bot-backup.service
  llm-trading-bot-backup.timer)
JOURNALD_CONF=journald@llm-trading-bot.conf

# Spec 015 AK6: its own backup profile and directories; the daily copies come from the backup unit.
OBS_DB=observations.db
OBS_BACKUPS="$BACKUPS/observations"
# Fewer than bot.db's 5: the file only grows, and the 14 daily copies keep its history anyway.
OBS_KEEP_PRE_UPDATE=3
# The services are stopped meanwhile: a copy that hangs must not keep the bot down.
OBS_BACKUP_SECONDS=600

# Spec 013: Caddy in front of the web UI's public listener (127.0.0.1 only, like the tunnel port).
PUBLIC_HOST_FILE="$ROOT/etc/llm-trading-bot/public-host"
PUBLIC_PORT=8788
# The same pattern as in scripts/deploy.sh (tests/test_deploy_files.py checks both).
HOST_RE='^[a-z0-9.-]+\.[a-z]{2,}$'
CADDY="${LTB_CADDY:-caddy}"
UFW="${LTB_UFW:-ufw}"
APT_GET="${LTB_APT_GET:-apt-get}"
APT_CACHE="${LTB_APT_CACHE:-apt-cache}"
GPG="${LTB_GPG:-gpg}"
CADDY_UNIT=caddy.service
CADDYFILE="$ROOT/etc/caddy/Caddyfile"
CADDY_DROPIN="$UNIT_DIR/caddy.service.d/10-llm-trading-bot.conf"
# The official repository as caddyserver.com/docs/install documents it (Cloudsmith, signed), but
# with the source line written here instead of downloaded. Its Release file says this origin.
CADDY_KEY_URL="${LTB_CADDY_KEY_URL:-https://dl.cloudsmith.io/public/caddy/stable/gpg.key}"
CADDY_KEYRING=/usr/share/keyrings/caddy-stable-archive-keyring.gpg
CADDY_LIST=/etc/apt/sources.list.d/caddy-stable.list
CADDY_REPO="deb [signed-by=$CADDY_KEYRING] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main"
CADDY_ORIGIN=cloudsmith/caddy/stable
UPGRADES_CONF=/etc/apt/apt.conf.d/53-llm-trading-bot-caddy
JOURNALCTL="${LTB_JOURNALCTL:-journalctl}"

# Spec 014: the path is fixed in deploy/llm-trading-bot-web.service (--impressum); the limit is the web
# UI's (web/legal.py reads at most 16 KB).
IMPRESSUM_FILE="$ROOT/etc/llm-trading-bot/impressum.md"
IMPRESSUM_MAX=16384
IMPRESSUM_INCOMING="" # deleted on exit, whatever happened (cmd_impressum)
# Spec 014 AK8: the system journal (sshd, kernel, and caddy before its drop-in) keeps visitors' IP
# addresses at most 14 + 1 days, like the bot's namespace (deploy/journald@llm-trading-bot.conf).
SYSTEM_JOURNALD_DROPIN="$ROOT/etc/systemd/journald.conf.d/50-llm-trading-bot.conf"
# The same limits for the namespace caddy logs into, as a drop-in: deploy/journald@llm-trading-bot.conf
# comes from the active release, and a rollback to one before 014 would install it without them.
NAMESPACE_JOURNALD_DROPIN="$ROOT/etc/systemd/journald@llm-trading-bot.conf.d/50-llm-trading-bot.conf"

changes=0
PUBLIC_HOST="" # set by load_public_host (deploy, rollback) or the public command
CADDY_WORK=""

# Every line also goes to $LOGFILE. Writes to the terminal may fail once the SSH session is gone;
# that must never stop the script halfway through an update (HUP and PIPE are ignored in main).
note() {
  if [ -d "$APP" ]; then printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOGFILE" 2>/dev/null || true; fi
}
log() {
  note "$*"
  printf '%s\n' "$*" 2>/dev/null || true
}
warn() {
  note "ERROR: $*"
  printf 'ERROR: %s\n' "$*" >&2 2>/dev/null || true
}
die() {
  warn "$@"
  exit 1
}

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

# A plain lower-case domain: it goes into sed, the Caddyfile and the web unit's command line.
host_ok() {
  local LC_ALL=C # [a-z] means exactly a to z, whatever locale sudo brings along
  [[ "$1" =~ $HOST_RE ]] && [[ "$1" != *..* ]] && [[ "$1" != [.-]* ]]
}

valid_host() {
  host_ok "$1" || die "invalid public host: $1 (lower-case domain such as example.com) - nothing changed"
}

# PUBLIC_HOST from the server's file, empty without it. Read once and before anything stops: a bad
# file must never end a deploy halfway.
load_public_host() {
  PUBLIC_HOST=""
  [ -e "$PUBLIC_HOST_FILE" ] || return 0
  local lines=()
  mapfile -t lines <"$PUBLIC_HOST_FILE" || die "cannot read $PUBLIC_HOST_FILE - nothing changed"
  [ "${#lines[@]}" -eq 1 ] || die "$PUBLIC_HOST_FILE must hold exactly one line - nothing changed"
  valid_host "${lines[0]}"
  PUBLIC_HOST="${lines[0]}"
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
  ensure_dir "$OBS_BACKUPS" 700 "$service:"
  ensure_dir "$OBS_BACKUPS/daily" 700 "$service:"
  ensure_dir "$OBS_BACKUPS/pre-update" 700 "$service:"
  install_uv
  # ADR-0020: time-sync.target only waits for NTP when this service is enabled.
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

# Replace the placeholders of a release's deploy/ files; ExecStart points at $app. The web unit gets
# the public listener only with a public host (a release before Spec 013 has no such placeholder).
render_units() {
  local release="$1" service="$2" app="$3" out="$4"
  install -d -m 755 "$out"
  local public=(-e "s| *__PUBLIC_HOST_ARG__||g")
  if [ -n "$PUBLIC_HOST" ]; then
    public=(-e "s|__PUBLIC_HOST_ARG__|--public-host $PUBLIC_HOST --public-port $PUBLIC_PORT|g")
  fi
  local unit
  for unit in "${UNITS[@]}" "$JOURNALD_CONF"; do
    [ -f "$release/deploy/$unit" ] || die "release lacks deploy/$unit"
    sed -e "s|__SERVICE_USER__|$service|g" -e "s|__APP_DIR__|$app|g" \
      -e "s|__DATA_DIR__|$DATA|g" -e "s|__BACKUP_DIR__|$BACKUPS|g" "${public[@]}" \
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
  quietly "$ANALYZE" verify "${paths[@]}" || die "systemd-analyze rejected the units of $release"
  rm -rf "$tmp"
}

# Called from start_release, which runs inside `if`/`||` where set -e is off: every step that
# can fail returns 1 itself, so a half-installed release is never switched to or started.
install_units() {
  local release="$1" service="$2" tmp unit
  tmp="$(mktemp -d)" || return 1
  render_units "$release" "$service" "$CURRENT" "$tmp" || return 1
  install -d -m 755 "$UNIT_DIR" || return 1
  for unit in "${UNITS[@]}"; do
    install -m 644 "$tmp/$unit" "$UNIT_DIR/$unit" || return 1
  done
  local journald="$JOURNALD_DIR/$JOURNALD_CONF"
  if ! cmp -s "$tmp/$JOURNALD_CONF" "$journald"; then
    install -m 644 "$tmp/$JOURNALD_CONF" "$journald" || return 1
    # The namespace's journald picks up new limits on its next start.
    "$SYSTEMCTL" try-restart "systemd-journald@llm-trading-bot.service" || true
  fi
  rm -rf "$tmp"
  "$SYSTEMCTL" daemon-reload || return 1
}

switch_to() {
  local sha="$1"
  ln -sfn "$RELEASES/$sha" "$APP/.current.new" || return 1
  mv -T "$APP/.current.new" "$CURRENT" || return 1
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

# Healthy: the status file names the new main process, with a round that started after $since
# and was ok - or incomplete only because of market data (Spec 009 AK5, user 2026-11-07).
# Read with the system Python (json, no venv needed).
# LEARNING(#marktdaten): Nachts fehlen ETH-EUR-Minutenkerzen – eine Stunde ohne ETH-Entscheidung
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
# A round blocked only by market data (Bitvavo leaves out minutes without trades, so ETH-EUR can be
# without decisions for up to an hour at night) still proves the new code ran a whole round.
data_gap = s["last_result"] == "incomplete" and "MarketDataError:" in (s.get("last_error") or "")
ran = s["last_result"] == "ok" or data_gap
ok = s["state"] in ("running", "degraded") and s["pid"] == pid and ran and started >= since
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

# --------------------------------------------------------------------------------- caddy

caddy_installed() {
  "$CADDY" version >/dev/null 2>&1
}

caddy_version() {
  "$CADDY" version 2>/dev/null | awk '{print $1}'
}

# The release's Caddyfile with the domain filled in.
render_caddyfile() {
  local release="$1" out="$2"
  sed -e "s|__PUBLIC_HOST__|$PUBLIC_HOST|g" "$release/deploy/Caddyfile" >"$out" || return 1
  ! grep -q '__[A-Z_]*__' "$out"
}

# Validation provisions the TLS app: its storage goes to a throwaway directory, not root's home.
validate_caddyfile() {
  local file="$1" tmp rc=0
  caddy_installed || die "caddy is not installed - set up the public address with" \
    "scripts/deploy.sh <alias> <service-user> public <domain> (or remove $PUBLIC_HOST_FILE)" \
    "- nothing changed"
  tmp="$(mktemp -d)"
  quietly env XDG_DATA_HOME="$tmp/data" XDG_CONFIG_HOME="$tmp/config" \
    "$CADDY" validate --adapter caddyfile --config "$file" || rc=$?
  rm -rf "$tmp"
  return "$rc"
}

# Install $src as $path (root, $mode) unless it is there already; counts a change.
put_file() {
  local path="$1" mode="$2" src="$3"
  if [ -f "$path" ] && cmp -s "$src" "$path"; then
    log "unchanged  $path"
  else
    install -D -m "$mode" "$src" "$path"
    log "written    $path"
    changes=$((changes + 1))
  fi
  chmod "$mode" "$path"
  own root:root "$path"
}

put_text() {
  local path="$1" mode="$2" text="$3" tmp
  tmp="$(mktemp)"
  printf '%s\n' "$text" >"$tmp"
  put_file "$path" "$mode" "$tmp"
  rm -f "$tmp"
}

# Only once the new release is active (deploy, never rollback). A failure puts the previous
# Caddyfile back and reports it; the bot is never rolled back for it.
apply_caddyfile() {
  local new="$1" old="$CADDY_WORK/Caddyfile.old"
  if [ -f "$CADDYFILE" ] && cmp -s "$new" "$CADDYFILE"; then
    log "unchanged  $CADDYFILE"
    return 0
  fi
  if [ -f "$CADDYFILE" ]; then cp -p "$CADDYFILE" "$old" || return 1; fi
  if install -D -m 644 "$new" "$CADDYFILE"; then
    own root:root "$CADDYFILE"
    if ! "$SYSTEMCTL" is-active --quiet "$CADDY_UNIT"; then
      log "installed  $CADDYFILE (caddy is not running - it takes effect on its next start)"
      return 0
    fi
    if quietly "$SYSTEMCTL" reload "$CADDY_UNIT"; then
      log "installed  $CADDYFILE, caddy reloaded"
      return 0
    fi
  fi
  if [ -f "$old" ]; then install -m 644 "$old" "$CADDYFILE"; else rm -f "$CADDYFILE"; fi
  warn "caddy did not take the new Caddyfile - the previous one is back in place" \
    "(journalctl --namespace=llm-trading-bot -u caddy; systemd's own lines: journalctl -u caddy);" \
    "the bot release stays"
  return 1
}

# --------------------------------------------------------------------------------- activate

stop_all() {
  "$SYSTEMCTL" stop "$TIMER" "$BOT" "$WEB" </dev/null >/dev/null 2>&1 || true
}

# Run a command with its output captured (no pipe to a terminal that may be gone), then log it.
quietly() {
  local out rc=0
  out="$("$@" </dev/null 2>&1)" || rc=$?
  [ -z "$out" ] || log "$out"
  return "$rc"
}

pre_update_backup() {
  local release="$1" service="$2"
  [ -f "$DATA/bot.db" ] || return 0
  quietly as_service "$service" "$release/.venv/bin/trading-bot" backup --db "$DATA/bot.db" \
    --dir "$BACKUPS/pre-update" --keep 5
}

# A release from Spec 015 on: its backup unit copies observations.db (the CLI of an older release
# has no --profile and would fail). The comment above that line in the unit does not count.
knows_observations() {
  grep -qsE '^ExecStart=-?[^ ]*trading-bot backup .*--profile observations' \
    "$1/deploy/llm-trading-bot-backup.service"
}

# Spec 015 AK6 (ADR-0027): after the trade history is safe, while nothing writes. Never fails the
# deploy: observations are measurements, and the daily copies are there as well.
pre_update_observations() {
  local release="$1" service="$2"
  if [ ! -f "$DATA/$OBS_DB" ]; then
    log "no $OBS_DB yet - nothing to back up besides bot.db"
  elif ! knows_observations "$release"; then
    log "release $(basename "$release") is from before Spec 015: it neither backs up nor reads" \
      "$OBS_DB - the file and its backups stay as they are"
  elif ! quietly as_service "$service" timeout "$OBS_BACKUP_SECONDS" \
    "$release/.venv/bin/trading-bot" backup --profile observations --db "$DATA/$OBS_DB" \
    --dir "$OBS_BACKUPS/pre-update" --keep "$OBS_KEEP_PRE_UPDATE"; then
    log "WARNING: the backup of $OBS_DB before the update failed - the deploy goes on" \
      "(bot.db is backed up; the recording is measurements only)"
  fi
  return 0
}

wait_web() {
  local deadline=$((SECONDS + 15))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if "$SYSTEMCTL" is-active --quiet "$WEB"; then return 0; fi
    sleep "$POLL_SECONDS"
  done
  return 1
}

# Units, switch, start. Returns 0 healthy, 1 unhealthy, 2 web UI only (no secrets.env yet), 3 the
# web UI did not start. Sets FP_CHANGED when secrets.env or a drop-in differs from FP_BEFORE.
start_release() {
  local sha="$1" service="$2" since
  install_units "$RELEASES/$sha" "$service" || return 1
  switch_to "$sha" || return 1
  # Compared before the web UI runs again: from then on a saved setting is a legitimate change.
  [ "$(fingerprint)" = "$FP_BEFORE" ] || FP_CHANGED=1
  quietly "$SYSTEMCTL" enable --now "$WEB" "$TIMER"
  wait_web || return 3
  if [ ! -f "$DATA/secrets.env" ]; then
    quietly "$SYSTEMCTL" disable --now "$BOT" || true
    return 2
  fi
  since="$(date +%s)"
  quietly "$SYSTEMCTL" enable "$BOT"
  # Type=notify: restart returns once the new process said READY=1 (or failed).
  quietly "$SYSTEMCTL" restart "$BOT" || true
  wait_healthy "$since"
}

# The release to fall back to: the one running before, or - redeploying the same commit - the
# recorded previous one. Empty when there is none.
fallback_for() {
  local sha="$1" old="$2"
  if [ -n "$old" ] && [ "$old" != "$sha" ]; then
    printf '%s\n' "$old"
  elif [ -L "$PREVIOUS" ]; then
    local prev
    prev="$(basename "$(readlink "$PREVIOUS")")"
    if [ "$prev" != "$sha" ] && [ -f "$RELEASES/$prev/.ready" ]; then printf '%s\n' "$prev"; fi
  fi
}

# $3 = caddy: also bring the release's Caddyfile in (deploy); anything else leaves caddy alone (rollback).
activate() {
  local sha="$1" service="$2" with_caddy="$3" old fallback result=0 caddy_new="" caddy_failed=0
  load_public_host
  old="$(current_sha)"
  fallback="$(fallback_for "$sha" "$old")"
  if [ -f "$DATA/bot.db" ]; then
    quietly as_service "$service" "$RELEASES/$sha/.venv/bin/trading-bot" verify-db \
      --db "$DATA/bot.db" || die "the release $sha cannot use the database - nothing changed"
  fi
  verify_units "$RELEASES/$sha" "$service"
  # A fallback that cannot be started must be known before anything stops.
  [ -z "$fallback" ] || verify_units "$RELEASES/$fallback" "$service"
  if [ "$with_caddy" = caddy ] && [ -n "$PUBLIC_HOST" ]; then
    if [ -f "$RELEASES/$sha/deploy/Caddyfile" ]; then
      # Checked now, installed only once the release is active: an invalid one changes nothing.
      CADDY_WORK="$(mktemp -d)"
      caddy_new="$CADDY_WORK/Caddyfile"
      render_caddyfile "$RELEASES/$sha" "$caddy_new" \
        || die "placeholder left in the Caddyfile of $sha - nothing changed"
      validate_caddyfile "$caddy_new" || die "caddy validate rejected the Caddyfile of $sha - nothing changed"
    else
      log "release $sha has no deploy/Caddyfile: caddy stays as it is (the public address needs a"
      log "release from Spec 013 on; the tunnel works as before)"
    fi
  fi
  FP_BEFORE="$(fingerprint)"
  FP_CHANGED=0
  stop_all
  if ! pre_update_backup "$RELEASES/$sha" "$service"; then
    if [ -n "$old" ]; then start_release "$old" "$service" || true; fi
    die "backup before the update failed - stayed on ${old:-nothing}"
  fi
  pre_update_observations "$RELEASES/$sha" "$service"
  start_release "$sha" "$service" || result=$?
  if [ "$result" -eq 2 ]; then
    log "no secrets.env yet: only the web UI runs. Set password, key and depots through the"
    log "SSH tunnel (setup code: journalctl --namespace=llm-trading-bot -u $WEB), then deploy again."
  elif [ "$result" -ne 0 ]; then
    log "release $sha did not become healthy within $HEALTH_SECONDS s"
    if [ -z "$fallback" ]; then
      # Nothing to go back to: stop only the bot. The web UI is where settings are fixed, and the
      # daily backups keep running.
      quietly "$SYSTEMCTL" disable --now "$BOT" || true
      quietly "$SYSTEMCTL" enable --now "$WEB" "$TIMER" || true
      die "no other release to fall back to - the bot is stopped and disabled, the web UI and the" \
        "backups keep running. Check the journal, fix the settings through the tunnel, deploy again."
    fi
    stop_all
    log "rolling back to $fallback (code and units only, the database stays as it is)"
    if start_release "$fallback" "$service"; then
      die "deploy of $sha failed - rolled back to $fallback, which is healthy"
    fi
    die "deploy of $sha failed and $fallback is not healthy either - check the journal"
  fi
  if [ -n "$old" ] && [ "$old" != "$sha" ]; then
    ln -sfn "$RELEASES/$old" "$APP/.previous.new"
    mv -T "$APP/.previous.new" "$PREVIOUS"
  fi
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$sha" "active" >>"$APP/deployed.log"
  if [ -n "$old" ] && [ "$old" != "$sha" ]; then
    log "release $sha active (previous: $old)"
  else
    log "release $sha active"
  fi
  if [ -n "$caddy_new" ]; then
    apply_caddyfile "$caddy_new" || caddy_failed=1
    rm -rf "$CADDY_WORK"
  fi
  [ "$FP_CHANGED" -eq 0 ] || die "secrets.env or a drop-in changed while the services were stopped" \
    "- the release $sha is active, but check them"
  [ "$caddy_failed" -eq 0 ] || die "the release $sha is active, but caddy still runs its previous" \
    "Caddyfile - fix it and deploy again"
}

prune() {
  local keep=() sha
  keep+=("$(current_sha)")
  if [ -L "$PREVIOUS" ]; then keep+=("$(basename "$(readlink "$PREVIOUS")")"); fi
  local active=0 failed=0
  while IFS= read -r sha; do
    local kept=0 k
    for k in "${keep[@]}"; do [ "$k" = "$sha" ] && kept=1; done
    if grep -q " $sha active$" "$APP/deployed.log" 2>/dev/null; then
      active=$((active + 1))
      [ "$active" -le "$KEEP_RELEASES" ] && kept=1
    else
      failed=$((failed + 1))
      [ "$failed" -le 1 ] && kept=1
    fi
    if [ "$kept" -eq 0 ]; then
      rm -rf "${RELEASES:?}/$sha"
      log "removed old release $sha"
    fi
  done < <(find "$RELEASES" -mindepth 2 -maxdepth 2 -name .ready -printf '%T@ %h\n' \
    | sort -rn | awk '{print $2}' | xargs -r -n1 basename)
}

with_lock() {
  [ -d "$APP" ] || die "$APP missing - run install first"
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
  activate "$sha" "$service" caddy
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
  # ADR-0024 / Spec 013: a rollback changes the bot only, never caddy.
  activate "$target" "$service" keep-caddy
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
  log "observations: $(observations_state)"
  log "$OBS_DB: $(observations_file_state)"
  log "latest observations backup: $(latest_observations_backup)"
  log "public host: $(public_host_state)"
  if caddy_installed; then
    log "caddy: $(caddy_version), $("$SYSTEMCTL" is-active "$CADDY_UNIT" 2>/dev/null || true)"
  else
    log "caddy: not installed"
  fi
  log "impressum: $(impressum_state)"
  log "web unit: $(web_impressum_state)"
  # Caddy shares the namespace's 200 MB with the bot (Spec 014): attack noise would show up here.
  log "journal: $("$JOURNALCTL" --namespace=llm-trading-bot --disk-usage 2>/dev/null || echo unknown)"
}

# Spec 015 AK5: the recording's counters from the status file. A status file of a release before
# 015 has none - the recording is off, which is no error. Read with the system Python (no venv).
observations_state() {
  local status="$DATA/service-status.json"
  if [ ! -f "$status" ]; then
    echo "no status file"
    return
  fi
  python3 - "$status" <<'PY' || echo "status file unreadable"
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        s = json.load(f)
except (OSError, ValueError):
    sys.exit(1)
if not isinstance(s, dict) or "observations_written" not in s:
    print("no counters in the status file (release before Spec 015) - not recorded")
    sys.exit(0)
error = " ".join(str(s.get("observations_last_error") or "none").split())[:200]
print(
    f"written {s.get('observations_written')}, failed {s.get('observations_failed')},"
    f" last error: {error}"
)
PY
}

observations_file_state() {
  if [ -f "$DATA/$OBS_DB" ]; then
    echo "$(stat -c %s "$DATA/$OBS_DB") B"
  else
    echo "missing (no round of a release from Spec 015 yet)"
  fi
}

# The newest copy by the time in its name, daily or pre-update.
latest_observations_backup() {
  local latest
  latest="$(find "$OBS_BACKUPS" -type f -name 'observations-*.db' -printf '%f\t%P\t%s\n' \
    2>/dev/null | sort | tail -n 1)" || true
  if [ -z "$latest" ]; then
    echo "none"
  else
    printf 'observations/%s (%s B)\n' "$(cut -f2 <<<"$latest")" "$(cut -f3 <<<"$latest")"
  fi
}

# Spec 014 AK9: never the content, only whether the web UI can serve it.
impressum_state() {
  local problem size hash
  problem="$(impressum_problem "$IMPRESSUM_FILE" installed)"
  case "$problem" in
    "")
      size="$(stat -c %s "$IMPRESSUM_FILE")"
      hash="$(sha256sum <"$IMPRESSUM_FILE" | cut -c1-12)"
      echo "ok ($size B, sha256 $hash)"
      ;;
    missing)
      echo "missing - /impressum answers 503 (scripts/deploy.sh <alias> <service-user> impressum <file>)"
      ;;
    *) echo "INVALID ($problem) - /impressum answers 503" ;;
  esac
}

# A release from before Spec 014 (e.g. after a rollback) does not pass the file to the web UI.
web_impressum_state() {
  if [ ! -f "$UNIT_DIR/$WEB" ]; then
    echo "not installed"
  elif grep -Eq "^ExecStart=.* --impressum ${IMPRESSUM_FILE#"$ROOT"}( |$)" "$UNIT_DIR/$WEB"; then
    echo "passes --impressum"
  else
    echo "without --impressum (release before Spec 014) - /impressum is not served"
  fi
}

# Like load_public_host, but only describes the file (status never stops on it).
public_host_state() {
  if [ ! -e "$PUBLIC_HOST_FILE" ]; then
    echo "off (tunnel only)"
    return
  fi
  local lines=()
  mapfile -t lines <"$PUBLIC_HOST_FILE" 2>/dev/null || true
  if [ "${#lines[@]}" -eq 1 ] && host_ok "${lines[0]}"; then
    echo "${lines[0]}"
  else
    echo "INVALID $PUBLIC_HOST_FILE - deploy refuses to run until it is fixed"
  fi
}

# --------------------------------------------------------------------------------- public (Spec 013)

# /run/caddy for the admin socket: the package's unit runs caddy as its own user without it.
# LogNamespace (Spec 014, ADR-0025): caddy's warnings name visitors' IP addresses; in the bot's
# namespace they expire after 15 days and never reach syslog. scripts/check-units.sh verifies this
# text against the package's caddy.service - keep it one quoted block ending in a quote.
CADDY_DROPIN_TEXT='# Spec 013/014 (deploy/release.sh): the admin API is a unix socket in /run/caddy; the log goes
# to the namespace llm-trading-bot (journalctl --namespace=llm-trading-bot -u caddy).
[Service]
RuntimeDirectory=caddy
RuntimeDirectoryMode=0750
LogNamespace=llm-trading-bot'

SYSTEM_JOURNALD_TEXT='# Spec 014 (deploy/release.sh public): old entries with IP addresses expire by themselves.
# Whole files are dropped, so at most MaxRetentionSec + MaxFileSec = 15 days.
[Journal]
MaxRetentionSec=14day
MaxFileSec=1day'

NAMESPACE_JOURNALD_TEXT='# Spec 014 (deploy/release.sh public): caddy logs the IP addresses of visitors here; at most
# MaxRetentionSec + MaxFileSec = 15 days, nothing goes on to syslog. Kept across rollbacks.
[Journal]
MaxRetentionSec=14day
MaxFileSec=1day
ForwardToSyslog=no'

UPGRADES_TEXT="// Spec 013 (deploy/release.sh): caddy is the only service facing the internet - its updates
// install automatically like the security updates.
Unattended-Upgrade::Origins-Pattern {
	\"origin=$CADDY_ORIGIN\";
};"

# The official repository with its signing key, then the package. Nothing is fetched again when
# key, source and package are already there.
install_caddy() {
  local work="$1" before=$changes fresh=0 policy
  curl -fsSL --proto '=https,file' -o "$work/caddy.asc" "$CADDY_KEY_URL" \
    || die "download of the caddy repository key failed - nothing changed"
  "$GPG" --batch --yes --dearmor -o "$work/caddy.gpg" "$work/caddy.asc" \
    || die "the caddy repository key could not be read - nothing changed"
  put_file "$ROOT$CADDY_KEYRING" 644 "$work/caddy.gpg"
  put_text "$ROOT$CADDY_LIST" 644 "$CADDY_REPO"
  caddy_installed || fresh=1
  if [ "$fresh" -eq 1 ] || [ "$changes" -ne "$before" ]; then
    quietly "$APT_GET" -o DPkg::Lock::Timeout=300 -qq update || die "apt-get update failed"
    quietly "$APT_GET" -o DPkg::Lock::Timeout=300 -y -qq install --no-install-recommends caddy \
      || die "apt-get install caddy failed"
    caddy_installed || die "caddy is missing after apt-get install"
  fi
  if [ "$fresh" -eq 1 ]; then
    # The package starts caddy with its welcome site on :80; it stays off until our Caddyfile is in.
    quietly "$SYSTEMCTL" stop "$CADDY_UNIT" || true
    log "installed  caddy $(caddy_version)"
    changes=$((changes + 1))
  else
    log "unchanged  caddy $(caddy_version)"
  fi
  # unattended-upgrades picks packages by the origin of their repository; it must be the one
  # UPGRADES_TEXT names. Captured first: grep -q would cut the pipe short (pipefail).
  policy="$("$APT_CACHE" policy 2>/dev/null)" || die "apt-cache policy failed"
  grep -qF "o=$CADDY_ORIGIN," <<<"$policy" \
    || die "apt lists no repository with origin $CADDY_ORIGIN - caddy would get no automatic updates"
}

# The active release's units with the current PUBLIC_HOST; only what differs is written.
refresh_units() {
  local service="$1" tmp unit
  tmp="$(mktemp -d)"
  render_units "$CURRENT" "$service" "$CURRENT" "$tmp"
  for unit in "${UNITS[@]}"; do
    put_file "$UNIT_DIR/$unit" 644 "$tmp/$unit"
  done
  rm -rf "$tmp"
}

open_port() {
  local out
  out="$("$UFW" allow "$1" 2>&1)" || die "ufw allow $1 failed: $out"
  if grep -q 'Rule added' <<<"$out"; then
    log "allowed    $1"
    changes=$((changes + 1))
  else
    log "unchanged  ufw allow $1"
  fi
}

close_port() {
  local out
  out="$("$UFW" delete allow "$1" 2>&1)" || true
  if grep -q 'Rule deleted' <<<"$out"; then
    log "closed     $1"
    changes=$((changes + 1))
  else
    log "unchanged  ufw: $1 not allowed (${out:-no output})"
  fi
}

# Spec 014 AK8: journald reads its drop-ins only at start; restarted only when the file changed.
# Services keep their log streams across the restart (journald holds them in systemd's fd store).
journal_limits() {
  local before=$changes
  ensure_dir "$(dirname "$NAMESPACE_JOURNALD_DROPIN")" 755 root:root
  put_text "$NAMESPACE_JOURNALD_DROPIN" 644 "$NAMESPACE_JOURNALD_TEXT"
  if [ "$changes" -ne "$before" ]; then
    quietly "$SYSTEMCTL" try-restart "systemd-journald@llm-trading-bot.service" || true
    log "restarted  systemd-journald@llm-trading-bot"
  fi
  before=$changes
  put_text "$SYSTEM_JOURNALD_DROPIN" 644 "$SYSTEM_JOURNALD_TEXT"
  [ "$changes" -ne "$before" ] || return 0
  if quietly "$SYSTEMCTL" restart systemd-journald.service; then
    log "restarted  systemd-journald"
  else
    warn "systemd-journald did not restart - the limits take effect on its next start (reboot)"
  fi
}

# Spec 014 AK8: the firewall logs every rejected packet with its source IP to kern.log, ufw.log and
# syslog, which rotate for weeks. Asked first, so a second run changes nothing.
ufw_logging_off() {
  local out
  out="$("$UFW" status verbose 2>&1)" || die "ufw status verbose failed: $out"
  if grep -q '^Logging: off' <<<"$out"; then
    log "unchanged  ufw logging off"
    return
  fi
  out="$("$UFW" logging off 2>&1)" || die "ufw logging off failed: $out"
  log "disabled   ufw logging"
  changes=$((changes + 1))
}

restart_web() {
  quietly "$SYSTEMCTL" restart "$WEB" || true
  wait_web || die "the web UI did not come back - check its journal"
  log "restarted  $WEB"
}

# Spec 013 AK8 order: repository and package, automatic updates, domain file, units, firewall,
# Caddyfile, restart caddy and the web UI. A second run changes and restarts nothing. Spec 014 AK8
# adds the log limits (caddy's namespace in its drop-in, the system journal, no ufw logging).
cmd_public() {
  local host="$1" service="$2" work before started=0
  require_root
  valid_host "$host"
  valid_name "$service"
  with_lock
  [ -L "$CURRENT" ] || die "no active release - deploy first"
  [ -f "$CURRENT/deploy/Caddyfile" ] || die "the active release $(current_sha) has no" \
    "deploy/Caddyfile - deploy a release from Spec 013 first - nothing changed"
  PUBLIC_HOST="$host"
  work="$(mktemp -d)"
  render_caddyfile "$CURRENT" "$work/Caddyfile" || die "placeholder left in the Caddyfile - nothing changed"
  verify_units "$CURRENT" "$service"
  export DEBIAN_FRONTEND=noninteractive
  install_caddy "$work"
  # Checked as soon as caddy exists, before the domain, the units or the firewall change.
  validate_caddyfile "$work/Caddyfile" || die "caddy validate rejected the Caddyfile - only the" \
    "caddy package was installed"
  put_text "$ROOT$UPGRADES_CONF" 644 "$UPGRADES_TEXT"
  ensure_dir "$(dirname "$PUBLIC_HOST_FILE")" 755 root:root
  put_text "$PUBLIC_HOST_FILE" 644 "$host"
  before=$changes
  refresh_units "$service"
  put_text "$CADDY_DROPIN" 644 "$CADDY_DROPIN_TEXT"
  [ "$changes" -eq "$before" ] || quietly "$SYSTEMCTL" daemon-reload
  # Before caddy (re)starts: its first lines with IP addresses already fall under the limits.
  journal_limits
  # Let's Encrypt needs port 80 open before caddy starts (5 failed attempts per hour).
  open_port 80/tcp
  open_port 443/tcp
  ufw_logging_off
  put_file "$CADDYFILE" 644 "$work/Caddyfile"
  rm -rf "$work"
  if ! "$SYSTEMCTL" is-enabled --quiet "$CADDY_UNIT"; then
    quietly "$SYSTEMCTL" enable "$CADDY_UNIT"
    log "enabled    caddy"
    changes=$((changes + 1))
  fi
  if [ "$changes" -gt 0 ] || ! "$SYSTEMCTL" is-active --quiet "$CADDY_UNIT"; then
    quietly "$SYSTEMCTL" restart "$CADDY_UNIT" || die "caddy did not start -" \
      "journalctl --namespace=llm-trading-bot -u caddy (caddy's messages), journalctl -u caddy (systemd's)"
    log "restarted  caddy"
    started=1
  fi
  # A rerun after a failed caddy start changes no file, but the web UI still runs without the
  # public listener from the first try (its unit was rewritten, the restart never came).
  if [ "$changes" -gt 0 ] || [ "$started" -eq 1 ]; then restart_web; fi
  log "public address: https://$host/ - the tunnel works as before"
  log "changes: $changes"
}

cmd_public_off() {
  local service="$1" before
  require_root
  valid_name "$service"
  with_lock
  [ -L "$CURRENT" ] || die "no active release - deploy first"
  PUBLIC_HOST=""
  verify_units "$CURRENT" "$service"
  if [ -e "$PUBLIC_HOST_FILE" ]; then
    rm -f "$PUBLIC_HOST_FILE"
    log "removed    $PUBLIC_HOST_FILE"
    changes=$((changes + 1))
  else
    log "unchanged  no $PUBLIC_HOST_FILE"
  fi
  if "$SYSTEMCTL" is-enabled --quiet "$CADDY_UNIT" || "$SYSTEMCTL" is-active --quiet "$CADDY_UNIT"; then
    quietly "$SYSTEMCTL" disable --now "$CADDY_UNIT" || die "caddy could not be stopped"
    log "disabled   caddy"
    changes=$((changes + 1))
  else
    log "unchanged  caddy off"
  fi
  close_port 80/tcp
  close_port 443/tcp
  before=$changes
  refresh_units "$service"
  [ "$changes" -eq "$before" ] || quietly "$SYSTEMCTL" daemon-reload
  [ "$changes" -eq 0 ] || restart_web
  # The log limits (journald drop-ins, ufw logging off) stay: they protect SSH and firewall logs too.
  log "public address off - only the tunnel"
  log "changes: $changes"
}

# --------------------------------------------------------------------------------- impressum (Spec 014)

# Prints why $1 is no imprint the web UI can serve, nothing when it is one. The rules of its reader
# (web/legal.py, Spec 014 AK3): a regular file, no link, at most IMPRESSUM_MAX bytes, UTF-8, no NUL,
# not empty. "installed" also needs it readable for the service user. Never prints the content.
impressum_problem() {
  python3 - "$1" "$IMPRESSUM_MAX" "${2:-}" <<'PY'
import os, stat, sys
path, limit, installed = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "installed"
def done(reason):
    print(reason)
    sys.exit(0)
try:
    st = os.lstat(path)
except FileNotFoundError:
    done("missing")
except OSError:
    done("unreadable")
if stat.S_ISLNK(st.st_mode):
    done("a symlink")
if not stat.S_ISREG(st.st_mode):
    done("not a regular file")
if st.st_size > limit:
    done(f"too big: {st.st_size} B, at most {limit} B")
if installed and not st.st_mode & stat.S_IROTH:
    done(f"not readable for the web UI: mode {stat.S_IMODE(st.st_mode):o}")
try:
    with open(path, "rb") as f:
        data = f.read(limit + 1)
except OSError:
    done("unreadable")
if b"\0" in data:
    done("contains a NUL byte")
try:
    text = data.decode("utf-8")
except UnicodeDecodeError:
    done("not UTF-8")
if not text.lstrip("\ufeff").strip():
    done("empty")
PY
}

# The upload from scripts/deploy.sh: incoming/impressum-<sha256>.md (root only, 700), checked against
# its hash and the reader's rules, then swapped in with one rename - a request in between sees the old
# or the new file, never a half-written one. The incoming copy holds name and address: always deleted.
cmd_impressum() {
  local digest="$1" problem dir tmp target="$IMPRESSUM_FILE"
  require_root
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || die "not a sha256: $digest - nothing changed"
  IMPRESSUM_INCOMING="$INCOMING/impressum-$digest.md"
  trap 'rm -f "$IMPRESSUM_INCOMING"' EXIT
  with_lock
  # Copies of earlier attempts (a failed second connection, an edited file) go too: personal data
  # never lingers in incoming.
  find "$INCOMING" -maxdepth 1 -name 'impressum-*.md' ! -name "impressum-$digest.md" -delete \
    2>/dev/null || true
  [ -f "$IMPRESSUM_INCOMING" ] && [ ! -L "$IMPRESSUM_INCOMING" ] \
    || die "no uploaded imprint for sha256 ${digest:0:12} - upload it with scripts/deploy.sh" \
      "- nothing changed"
  [ "$(sha256sum <"$IMPRESSUM_INCOMING" | awk '{print $1}')" = "$digest" ] \
    || die "the uploaded imprint does not match its sha256 (upload cut short?) - nothing changed"
  problem="$(impressum_problem "$IMPRESSUM_INCOMING")"
  [ -z "$problem" ] || die "the uploaded imprint is invalid: $problem - nothing changed"
  dir="$(dirname "$target")"
  ensure_dir "$dir" 755 root:root
  [ ! -d "$target" ] || [ -L "$target" ] || die "$target is a directory - nothing changed"
  if [ -f "$target" ] && [ ! -L "$target" ] && cmp -s "$IMPRESSUM_INCOMING" "$target"; then
    log "unchanged  $target"
    chmod 644 "$target"
    own root:root "$target"
  else
    # Same directory, so mv is a rename; -T replaces a link itself instead of following it.
    tmp="$dir/.impressum.md.new"
    rm -f "$tmp"
    if testing; then
      install -m 644 "$IMPRESSUM_INCOMING" "$tmp"
    else
      install -m 644 -o root -g root "$IMPRESSUM_INCOMING" "$tmp"
    fi
    mv -fT "$tmp" "$target"
    log "written    $target ($(stat -c %s "$target") B, sha256 ${digest:0:12})" \
      "- served from the next request on"
    changes=$((changes + 1))
  fi
  log "web unit: $(web_impressum_state)"
  log "changes: $changes"
}

# Everything above is only definitions: bash has read the whole script before main runs, so a
# command that reads stdin cannot swallow the rest of the piped-in script.
main() {
  exec </dev/null
  # An SSH session that drops must not kill an update halfway (before a rollback could run).
  trap '' HUP PIPE
  local mode="${1:-}"
  case "$mode" in
    install) [ "$#" -eq 2 ] || die "usage: install <service-user>"; cmd_install "$2" ;;
    deploy) [ "$#" -eq 3 ] || die "usage: deploy <sha> <service-user>"; cmd_deploy "$2" "$3" ;;
    rollback) [ "$#" -eq 2 ] || die "usage: rollback <service-user>"; cmd_rollback "$2" ;;
    status) cmd_status ;;
    public) [ "$#" -eq 3 ] || die "usage: public <domain> <service-user>"; cmd_public "$2" "$3" ;;
    public-off) [ "$#" -eq 2 ] || die "usage: public-off <service-user>"; cmd_public_off "$2" ;;
    impressum) [ "$#" -eq 2 ] || die "usage: impressum <sha256>"; cmd_impressum "$2" ;;
    *) die "usage: install|deploy|rollback|status|public|public-off|impressum (see the header)" ;;
  esac
}

main "$@"
