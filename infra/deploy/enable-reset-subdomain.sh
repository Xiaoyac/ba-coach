#!/usr/bin/env bash
# Run on the Guangzhou server after reset.bacoach.xyz has an A record pointing
# at 8.134.178.40. Expands the existing certificate, then installs the tracked
# two-host Nginx configuration.
set -Eeuo pipefail

expected_ip="8.134.178.40"
resolved_ip="$(getent ahostsv4 reset.bacoach.xyz | awk 'NR==1 {print $1}')"
if [[ "$resolved_ip" != "$expected_ip" ]]; then
  echo "reset.bacoach.xyz resolves to '${resolved_ip:-nothing}', expected $expected_ip" >&2
  exit 1
fi

certbot --nginx --non-interactive --cert-name bacoach.xyz --expand \
  -d bacoach.xyz -d reset.bacoach.xyz
nginx -t
systemctl reload nginx
echo "reset.bacoach.xyz HTTPS enabled"
