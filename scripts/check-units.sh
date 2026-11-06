#!/usr/bin/env bash
# Checks the systemd files in deploy/ with test values (Spec 008 AK10).
# Needs systemd-analyze, so it runs in the Linux CI; the real installation follows in roadmap Nr. 6.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

app="$work/app"
data="$work/data"
mkdir -p "$app/.venv/bin" "$data"
# systemd-analyze verify insists that ExecStart points to an executable.
printf '#!/bin/sh\nexit 0\n' > "$app/.venv/bin/trading-bot"
chmod +x "$app/.venv/bin/trading-bot"

units=(llm-trading-bot.service llm-trading-bot-web.service)
for unit in "${units[@]}"; do
  sed -e "s|__SERVICE_USER__|nobody|g" -e "s|__APP_DIR__|$app|g" -e "s|__DATA_DIR__|$data|g" \
    "$root/deploy/$unit" > "$work/$unit"
done

if grep -n '__[A-Z_]*__' "$work"/*.service; then
  echo "check-units: placeholder left after substitution" >&2
  exit 1
fi

for unit in "${units[@]}"; do
  systemd-analyze verify "$work/$unit"
  echo "check-units: $unit ok"
done
