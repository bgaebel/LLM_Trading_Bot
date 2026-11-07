#!/usr/bin/env bash
# Checks the systemd files in deploy/ with test values (Spec 008 AK10, Spec 009 AK4) and the
# Caddyfile (Spec 013 AK7). Needs systemd-analyze and caddy, so it runs in the Linux CI;
# deploy/release.sh verifies them again on the server.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

app="$work/app"
data="$work/data"
backup="$work/backup"
mkdir -p "$app/.venv/bin" "$data" "$backup"
# systemd-analyze verify insists that ExecStart points to an executable.
printf '#!/bin/sh\nexit 0\n' > "$app/.venv/bin/trading-bot"
chmod +x "$app/.venv/bin/trading-bot"

units=(llm-trading-bot.service llm-trading-bot-web.service llm-trading-bot-backup.service
  llm-trading-bot-backup.timer)
host=example.test

# Both ways release.sh renders the web unit: without and with a public host.
for variant in tunnel public; do
  out="$work/$variant"
  mkdir -p "$out"
  if [ "$variant" = public ]; then
    arg=(-e "s|__PUBLIC_HOST_ARG__|--public-host $host --public-port 8788|g")
  else
    arg=(-e "s| *__PUBLIC_HOST_ARG__||g")
  fi
  for unit in "${units[@]}"; do
    sed -e "s|__SERVICE_USER__|nobody|g" -e "s|__APP_DIR__|$app|g" -e "s|__DATA_DIR__|$data|g" \
      -e "s|__BACKUP_DIR__|$backup|g" "${arg[@]}" "$root/deploy/$unit" > "$out/$unit"
  done

  if grep -n '__[A-Z_]*__' "$out"/*.service "$out"/*.timer; then
    echo "check-units: placeholder left after substitution ($variant)" >&2
    exit 1
  fi
  if [ "$variant" = public ]; then
    grep -q -- "--port 8787 --public-host $host --public-port 8788$" "$out/llm-trading-bot-web.service"
  else
    grep -q -- "--port 8787$" "$out/llm-trading-bot-web.service"
  fi

  # One call: the timer names its service, which must resolve among the verified files.
  paths=()
  for unit in "${units[@]}"; do
    paths+=("$out/$unit")
  done
  systemd-analyze verify "${paths[@]}"
  for unit in "${units[@]}"; do
    echo "check-units: $unit ok ($variant)"
  done
done

sed -e "s|__PUBLIC_HOST__|$host|g" "$root/deploy/Caddyfile" > "$work/Caddyfile"
if grep -n '__[A-Z_]*__' "$work/Caddyfile"; then
  echo "check-units: placeholder left in the Caddyfile" >&2
  exit 1
fi
if command -v caddy >/dev/null 2>&1; then
  # Validation provisions the TLS app; its storage stays in the throwaway directory.
  XDG_DATA_HOME="$work/xdg-data" XDG_CONFIG_HOME="$work/xdg-config" \
    caddy validate --adapter caddyfile --config "$work/Caddyfile"
  echo "check-units: Caddyfile ok ($(caddy version | awk '{print $1}'))"
elif [ -n "${CI:-}" ]; then
  echo "check-units: caddy is missing - the CI installs it from the official repository" >&2
  exit 1
else
  echo "check-units: caddy not installed - Caddyfile not validated (the CI does it)"
fi
