#!/usr/bin/env bash
set -Eeuo pipefail

upload="/tmp/bacoach-backend.env.upload"
target="/etc/bacoach/backend.env"
temp="$(mktemp /etc/bacoach/backend.env.XXXXXX)"
trap 'rm -f "$temp"' EXIT

if [[ ! -f "$upload" ]]; then
  echo "uploaded backend environment file is missing" >&2
  exit 1
fi

# Windows writes CRLF; systemd EnvironmentFile expects ordinary Unix lines.
sed 's/\r$//' "$upload" > "$temp"

upsert() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$temp"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$temp"
  else
    printf '%s=%s\n' "$key" "$value" >> "$temp"
  fi
}

upsert DEBUG false
upsert CORS_ORIGINS https://bacoach.xyz
upsert CORS_ORIGIN_REGEX ''
upsert ADMIN_RESET_KEY "$(openssl rand -hex 32)"

install -m 0640 -o root -g bacoach "$temp" "$target"
printf '%s\n' \
  'NODE_ENV=production' \
  'NEXT_TELEMETRY_DISABLED=1' \
  'BACKEND_ORIGIN=http://127.0.0.1:8000' \
  | install -m 0640 -o root -g bacoach /dev/stdin /etc/bacoach/frontend.env

if command -v shred >/dev/null 2>&1; then
  shred -u "$upload"
else
  rm -f "$upload"
fi

trap - EXIT
rm -f "$temp"
echo "production environment installed"
