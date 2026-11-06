#!/usr/bin/env bash
# Checks the systemd files in deploy/ with test values (Spec 008 AK10, Spec 009 AK4).
# Needs systemd-analyze, so it runs in the Linux CI; deploy/release.sh verifies them again on the server.
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
for unit in "${units[@]}"; do
  sed -e "s|__SERVICE_USER__|nobody|g" -e "s|__APP_DIR__|$app|g" -e "s|__DATA_DIR__|$data|g" \
    -e "s|__BACKUP_DIR__|$backup|g" "$root/deploy/$unit" > "$work/$unit"
done

if grep -n '__[A-Z_]*__' "$work"/*.service "$work"/*.timer; then
  echo "check-units: placeholder left after substitution" >&2
  exit 1
fi

# One call: the timer names its service, which must resolve among the verified files.
paths=()
for unit in "${units[@]}"; do
  paths+=("$work/$unit")
done
systemd-analyze verify "${paths[@]}"
for unit in "${units[@]}"; do
  echo "check-units: $unit ok"
done
