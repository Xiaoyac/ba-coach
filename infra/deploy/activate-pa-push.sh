#!/usr/bin/env bash
# Initial activation only, after code deployment and explicit push-only migration.
# No business data import, no test notification, no VAPID key rotation.
set -Eeuo pipefail
if [[ $# -ne 2 || ! "$1" =~ ^[0-9]{8}T[0-9]{6}Z$ || "$EUID" -ne 0 ]]; then
  echo 'usage (root): activate-pa-push.sh RELEASE_ID STAGED_INFRA_DIRECTORY' >&2
  exit 2
fi
release="/opt/bacoach/releases/$1"
infra="$2"
[[ "$(readlink -f /opt/bacoach/current)" == "$release" ]]
[[ -f "$infra/configure_pa_push.py" && -f "$infra/bacoach-pa-push.service" && -f "$infra/bacoach-backend-pa-push.conf" ]]
[[ ! -e /etc/bacoach/pa-push.env && ! -L /etc/bacoach/pa-push.env ]]
[[ ! -e /etc/systemd/system/bacoach-backend.service.d/40-pa-push.conf ]]
[[ ! -e /etc/systemd/system/bacoach-pa-push.service ]]

config_created=false
backend_changed=false
rollback() {
  result=$?
  if [[ "$result" != 0 && "$config_created" == true ]]; then
    echo 'Activation failed; disabling push, preserving keys/tables and existing website.' >&2
    "$release/backend/.venv/bin/python" "$infra/configure_pa_push.py" --disable || true
    systemctl disable --now bacoach-pa-push.service 2>/dev/null || true
    if [[ "$backend_changed" == true ]]; then
      systemctl daemon-reload
      systemctl restart bacoach-backend.service || true
    fi
  fi
  exit "$result"
}
trap rollback EXIT

[[ ! -L /opt/bacoach/secrets ]]
install -d -m 0700 -o bacoach -g bacoach /opt/bacoach/secrets
if [[ -e /opt/bacoach/secrets/pa-vapid-private.pem || -e /opt/bacoach/secrets/pa-vapid-public.txt ]]; then
  [[ -f /opt/bacoach/secrets/pa-vapid-private.pem && -f /opt/bacoach/secrets/pa-vapid-public.txt ]]
  echo 'Reusing existing server-only VAPID pair; key match checked below.'
else
  runuser -u bacoach -- "$release/backend/.venv/bin/python" "$release/backend/scripts/generate_pa_vapid.py" --directory /opt/bacoach/secrets
fi
"$release/backend/.venv/bin/python" "$infra/configure_pa_push.py"
config_created=true

systemd-run --quiet --wait --pipe --collect --uid=bacoach --gid=bacoach \
  --working-directory="$release/backend" -p Environment=PYTHONPATH=. \
  -p EnvironmentFile=/etc/bacoach/backend.env \
  -p EnvironmentFile=/etc/bacoach/workbench-safety.env \
  -p EnvironmentFile=/etc/bacoach/pa-push.env \
  "$release/backend/.venv/bin/python" scripts/pa_push_worker.py --check

install -d -m 0755 /etc/systemd/system/bacoach-backend.service.d
install -m 0644 "$infra/bacoach-pa-push.service" /etc/systemd/system/bacoach-pa-push.service
install -m 0644 "$infra/bacoach-backend-pa-push.conf" /etc/systemd/system/bacoach-backend.service.d/40-pa-push.conf
backend_changed=true
systemctl daemon-reload
systemctl restart bacoach-backend.service
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null
systemctl enable --now bacoach-pa-push.service
sleep 3
systemctl is-active --quiet bacoach-pa-push.service
trap - EXIT
echo 'PA push API and worker activated. No test subscriptions or notifications created.'
