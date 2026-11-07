#!/usr/bin/env bash
# Checks the systemd files in deploy/ with test values (Spec 008 AK10, Spec 009 AK4), the
# Caddyfile (Spec 013 AK7) and caddy's unit with the drop-in release.sh public writes (Spec 014 AK8).
# Needs systemd-analyze and caddy, so it runs in the Linux CI; deploy/release.sh verifies the units
# again on the server.
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
impressum=/etc/llm-trading-bot/impressum.md

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
  # Spec 014: the imprint path is always passed (the file may be missing), the public host only
  # with one.
  if [ "$variant" = public ]; then
    grep -q -- "--port 8787 --impressum $impressum --public-host $host --public-port 8788$" \
      "$out/llm-trading-bot-web.service"
  else
    grep -q -- "--port 8787 --impressum $impressum$" "$out/llm-trading-bot-web.service"
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

# Spec 014 AK8: caddy's own unit from the package plus the drop-in text of release.sh, verified the
# way systemd loads them (the drop-in directory next to the unit file). The text is the single-quoted
# value of CADDY_DROPIN_TEXT: from its first line to the next line that ends in a quote.
dropin="$(sed -n "/^CADDY_DROPIN_TEXT='/,/'\$/p" "$root/deploy/release.sh" \
  | sed -e "1s/^CADDY_DROPIN_TEXT='//" -e "\$s/'\$//")"
if ! grep -qx 'LogNamespace=llm-trading-bot' <<<"$dropin"; then
  echo "check-units: no CADDY_DROPIN_TEXT with LogNamespace=llm-trading-bot in deploy/release.sh" >&2
  exit 1
fi
caddy_unit=""
for candidate in /usr/lib/systemd/system/caddy.service /lib/systemd/system/caddy.service; do
  if [ -f "$candidate" ]; then
    caddy_unit="$candidate"
    break
  fi
done
if [ -n "$caddy_unit" ]; then
  # First a drop-in with an unknown key: it proves that systemd-analyze reads drop-ins from this
  # directory at all - otherwise a clean result for the real one would prove nothing.
  for variant in probe real; do
    dir="$work/caddy-$variant"
    mkdir -p "$dir/caddy.service.d"
    cp "$caddy_unit" "$dir/caddy.service"
    if [ "$variant" = probe ]; then
      printf '[Service]\nCheckUnitsProbe=1\n' > "$dir/caddy.service.d/10-llm-trading-bot.conf"
    else
      printf '%s\n' "$dropin" > "$dir/caddy.service.d/10-llm-trading-bot.conf"
    fi
    rc=0
    report="$(systemd-analyze verify "$dir/caddy.service" 2>&1)" || rc=$?
    if [ "$variant" = probe ]; then
      if ! grep -q 'CheckUnitsProbe' <<<"$report"; then
        printf '%s\n' "$report" >&2
        echo "check-units: systemd-analyze did not read the caddy drop-in - nothing verified" >&2
        exit 1
      fi
    elif [ "$rc" -ne 0 ] || grep -q '10-llm-trading-bot' <<<"$report"; then
      printf '%s\n' "$report" >&2
      echo "check-units: systemd-analyze rejected caddy.service with the drop-in" >&2
      exit 1
    fi
  done
  echo "check-units: caddy.service with the drop-in ok ($caddy_unit)"
elif [ -n "${CI:-}" ]; then
  echo "check-units: caddy.service is missing - the CI installs caddy before this script" >&2
  exit 1
else
  echo "check-units: caddy not installed - its unit and drop-in not verified (the CI does it)"
fi
