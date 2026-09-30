#!/usr/bin/env bash
# Sharing release: create only the additive share table; no backfills/imports.
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: server-deploy-sharing.sh RELEASE_ID ARCHIVE EXPECTED_DATABASE" >&2
  exit 2
fi

release_id="$1"
archive="$2"
expected_database="$3"
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "invalid release id" >&2; exit 2; }
[[ "$archive" == "/tmp/bacoach-$release_id.tar.gz" && -f "$archive" ]] || { echo "invalid release archive" >&2; exit 2; }
[[ "$expected_database" =~ ^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$ ]] || { echo "invalid expected database name" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "run as root on the deployment server" >&2; exit 2; }

target="/opt/bacoach/releases/$release_id"
previous="$(readlink -f /opt/bacoach/current)"
[[ -L /opt/bacoach/current && "$previous" == /opt/bacoach/releases/* && -d "$previous" ]] || exit 2
[[ ! -e "$target" && ! -L "$target" ]] || exit 2
[[ -f /etc/bacoach/backend.env && -f /etc/bacoach/workbench-safety.env ]] || exit 2
[[ -x /usr/local/bin/npm ]] || exit 2

switched=false
push_stopped=false
push_was_active=false
if systemctl is-active --quiet bacoach-pa-push.service; then push_was_active=true; fi

rollback() {
  local status=$?
  trap - EXIT
  if [[ $status -ne 0 ]]; then
    if [[ "$switched" == true ]]; then
      echo "deployment failed; restoring $previous (share table retained)" >&2
      if [[ "$push_was_active" == true ]]; then systemctl stop bacoach-pa-push.service || true; fi
      ln -sfn "$previous" /opt/bacoach/current
      systemctl restart bacoach-backend.service bacoach-frontend.service || true
    fi
    if [[ "$push_was_active" == true && "$push_stopped" == true ]]; then
      systemctl start bacoach-pa-push.service || true
    fi
  fi
  exit "$status"
}
trap rollback EXIT

install -d -m 0750 -o bacoach -g bacoach "$target"
tar -xzf "$archive" -C "$target"
python3 - "$previous/backend/requirements.txt" "$target/backend/requirements.txt" <<'PY'
import pathlib
import sys

assert pathlib.Path(sys.argv[1]).read_text().splitlines() == pathlib.Path(sys.argv[2]).read_text().splitlines(), "Backend dependencies differ; do not reuse the previous environment"
PY
previous_venv="$(readlink -f "$previous/backend/.venv")"
[[ -x "$previous_venv/bin/python" && ! -e "$target/backend/.venv" ]] || exit 2
ln -s "$previous_venv" "$target/backend/.venv"
chown -R bacoach:bacoach "$target"

echo "Building the new frontend while the current release stays online"
runuser -u bacoach -- bash -c '
  set -e
  cd "$1"
  NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci --no-audit --no-fund
  NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build
' bash "$target/frontend"

# Environment values stay inside this subshell and its child. No secret file
# is copied into a release, printed, or passed as a command-line argument.
run_backend_task() (
  set -a
  source <(sed 's/\r$//' /etc/bacoach/backend.env)
  source <(sed 's/\r$//' /etc/bacoach/workbench-safety.env)
  set +a
  cd "$target/backend"
  export PYTHONPATH="$target/backend"
  runuser -u bacoach --preserve-environment -- "$target/backend/.venv/bin/python" "$@"
)

echo "Checking mediator configuration without a model call or user-data write"
run_backend_task scripts/check_knowledge_mediator.py --configuration-only
echo "Previewing the additive share-table migration"
run_backend_task scripts/create_conversation_shares.py --expected-database "$expected_database"
echo "Creating or verifying only conversation_shares"
run_backend_task scripts/create_conversation_shares.py --expected-database "$expected_database" --apply
# Re-inspect the resulting schema before any live service uses the new code.
run_backend_task scripts/create_conversation_shares.py --expected-database "$expected_database"

if [[ "$push_was_active" == true ]]; then
  systemctl stop bacoach-pa-push.service
  push_stopped=true
fi
ln -sfn "$target" /opt/bacoach/current
switched=true
systemctl restart bacoach-backend.service
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null

systemctl restart bacoach-frontend.service
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:3000/ >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:3000/ >/dev/null

if [[ "$push_was_active" == true ]]; then
  systemctl start bacoach-pa-push.service
  sleep 2
  systemctl is-active --quiet bacoach-pa-push.service
fi
trap - EXIT
echo "DEPLOYED=$target PREVIOUS=$previous"
