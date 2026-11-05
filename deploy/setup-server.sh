#!/usr/bin/env bash
# One-time hardening of a fresh Ubuntu 24.04 server (Spec 002, ADR-0005, ADR-0006).
# Idempotent: re-running reports "unchanged" for every setting that is already in place.
#
# Runs on the server and is piped in via SSH, so no copy stays behind:
#   Phase 1 (root and password login stay open so a mistake cannot lock you out):
#     ssh root@<server> 'bash -s' -- prepare <admin> <service> "<ssh-public-key>" < deploy/setup-server.sh
#   Check in a SECOND session: ssh <admin>@<server> sudo -n true
#   Phase 2 (via the admin user, which also proves that sudo works):
#     ssh <admin>@<server> 'sudo bash -s' -- lock <admin> < deploy/setup-server.sh
#
# Real host names, IPs and user names never go into this file: the repository is public.
set -euo pipefail

changes=0

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  [ "$(id -u)" -eq 0 ] || die "must run as root"
}

valid_name() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "invalid user name: $1"
}

# Write a file only if its content differs, so a second run changes nothing.
write_file() {
  local path="$1" mode="$2" content="$3"
  if [ -f "$path" ] && [ "$(cat "$path")" = "$content" ]; then
    log "unchanged  $path"
    return
  fi
  local tmp
  tmp="$(mktemp)"
  printf '%s\n' "$content" > "$tmp"
  install -m "$mode" -o root -g root "$tmp" "$path"
  rm -f "$tmp"
  log "written    $path"
  changes=$((changes + 1))
}

prepare() {
  local admin="$1" service="$2" pubkey="$3"
  valid_name "$admin"
  valid_name "$service"
  [ "$admin" != "$service" ] || die "admin and service user must differ"
  printf '%s\n' "$pubkey" | ssh-keygen -lf /dev/stdin >/dev/null 2>&1 || die "not a valid SSH public key"

  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get -y -qq upgrade
  apt-get -y -qq install --no-install-recommends ufw unattended-upgrades >/dev/null

  # Time: the market-data checks reject data on clock drift > 120 s (Spec 001).
  if [ "$(timedatectl show -p Timezone --value)" != "Etc/UTC" ]; then
    timedatectl set-timezone Etc/UTC
    log "set        timezone Etc/UTC"
    changes=$((changes + 1))
  fi
  timedatectl set-ntp true

  # ADR-0006: admin logs in by key only and uses passwordless sudo.
  if ! id -u "$admin" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --groups sudo "$admin"
    log "created    admin user"
    changes=$((changes + 1))
  fi
  # '*' (not '!'): no password can ever match, but sshd does not treat the account as locked.
  if [ "$(getent shadow "$admin" | cut -d: -f2)" != "*" ]; then
    usermod -p '*' "$admin"
    log "set        admin password field to '*'"
    changes=$((changes + 1))
  fi
  local home ssh_dir keys
  home="$(getent passwd "$admin" | cut -d: -f6)"
  ssh_dir="$home/.ssh"
  keys="$ssh_dir/authorized_keys"
  install -d -m 700 -o "$admin" -g "$admin" "$ssh_dir"
  touch "$keys"
  if ! grep -qxF "$pubkey" "$keys"; then
    printf '%s\n' "$pubkey" >> "$keys"
    log "added      admin SSH key"
    changes=$((changes + 1))
  fi
  chown "$admin:$admin" "$keys"
  chmod 600 "$keys"

  local sudoers="/etc/sudoers.d/90-admin-$admin" rule tmp
  rule="$admin ALL=(ALL) NOPASSWD:ALL"
  if [ ! -f "$sudoers" ] || [ "$(cat "$sudoers")" != "$rule" ]; then
    tmp="$(mktemp)"
    printf '%s\n' "$rule" > "$tmp"
    visudo -cqf "$tmp" || { rm -f "$tmp"; die "sudoers rule rejected by visudo"; }
    install -m 440 -o root -g root "$tmp" "$sudoers"
    rm -f "$tmp"
    log "written    $sudoers"
    changes=$((changes + 1))
  else
    log "unchanged  $sudoers"
  fi

  # ADR-0006: the bot runs as a system user without shell or sudo; data dirs come with the deployment.
  if ! id -u "$service" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$service"
    log "created    service user"
    changes=$((changes + 1))
  fi

  # ADR-0005: inbound only SSH, rate-limited; allow it before enabling so the session survives.
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw limit 22/tcp comment 'ssh' >/dev/null
  if ! ufw status | grep -q '^Status: active'; then
    ufw --force enable >/dev/null
    log "enabled    ufw"
    changes=$((changes + 1))
  fi

  write_file /etc/apt/apt.conf.d/20auto-upgrades 644 \
'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";'
  write_file /etc/apt/apt.conf.d/52-llm-trading-bot 644 \
'// Spec 002: security updates install automatically; reboot only when required, at a fixed quiet time.
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";'

  log "prepare done, changes: $changes"
  log "NEXT: in a second session run 'ssh <admin>@<server> sudo -n true', then phase 'lock'."
}

lock() {
  local admin="$1"
  valid_name "$admin"
  id -u "$admin" >/dev/null 2>&1 || die "admin user does not exist - run 'prepare' first"
  local home keys
  home="$(getent passwd "$admin" | cut -d: -f6)"
  keys="$home/.ssh/authorized_keys"
  [ -s "$keys" ] || die "admin has no authorized_keys - refusing to lock out root"
  [ -f "/etc/sudoers.d/90-admin-$admin" ] || die "admin has no sudo rule - refusing to lock out root"

  # Ubuntu cloud images set 'PasswordAuthentication yes' in 50-cloud-init.conf. sshd keeps the FIRST
  # value it reads, so this drop-in must sort before it.
  local conf=/etc/ssh/sshd_config.d/01-hardening.conf
  write_file "$conf" 644 \
'# Spec 002 / ADR-0006: key-only login, no root.
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes'
  sshd -t || { rm -f "$conf"; die "sshd config invalid - drop-in removed, nothing reloaded"; }
  systemctl reload ssh

  local effective
  effective="$(sshd -T 2>/dev/null)"
  for expected in "permitrootlogin no" "passwordauthentication no" "kbdinteractiveauthentication no" \
                  "pubkeyauthentication yes"; do
    grep -qx "$expected" <<<"$effective" || die "effective sshd setting missing: $expected"
  done
  log "lock done, changes: $changes"
}

main() {
  require_root
  case "${1:-}" in
    prepare) [ $# -eq 4 ] || die "usage: prepare <admin> <service> <ssh-public-key>"; prepare "$2" "$3" "$4" ;;
    lock)    [ $# -eq 2 ] || die "usage: lock <admin>"; lock "$2" ;;
    *)       die "usage: setup-server.sh prepare <admin> <service> <ssh-public-key> | lock <admin>" ;;
  esac
}

main "$@"
