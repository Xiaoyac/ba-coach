#!/usr/bin/env bash
# Code-only deployment: no migrations, backfills, or knowledge imports.
set -Eeuo pipefail
release_id="$1"
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
target="/opt/bacoach/releases/$release_id"
previous=$(readlink -f /opt/bacoach/current)
[[ "$previous" == /opt/bacoach/releases/* && ! -e "$target" ]] || exit 2
switched=false
push_was_active=false
if systemctl is-active --quiet bacoach-pa-push.service; then push_was_active=true; fi
rollback() {
  code=$?
  if [[ $code -ne 0 && "$switched" == true ]]; then
    ln -sfn "$previous" /opt/bacoach/current
    systemctl restart bacoach-backend bacoach-frontend || true
  fi
  if [[ $code -ne 0 && "$push_was_active" == true ]]; then
    systemctl restart bacoach-pa-push.service || true
  fi
  exit "$code"
}
trap rollback EXIT
install -d -m 0750 -o bacoach -g bacoach "$target"
tar -xzf "/tmp/bacoach-$release_id.tar.gz" -C "$target"
python3 - "$previous/backend/requirements.txt" "$target/backend/requirements.txt" <<'PY'
import pathlib,sys
assert pathlib.Path(sys.argv[1]).read_text().splitlines()==pathlib.Path(sys.argv[2]).read_text().splitlines()
PY
ln -s "$(readlink -f "$previous/backend/.venv")" "$target/backend/.venv"
chown -R bacoach:bacoach "$target"
runuser -u bacoach -- bash -c "set -e; cd '$target/frontend'; NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci --no-audit --no-fund; NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build"
set -a
source <(sed 's/\r$//' /etc/bacoach/backend.env)
source /etc/bacoach/workbench-safety.env
set +a
cd "$target/backend"
.venv/bin/python scripts/check_knowledge_mediator.py --configuration-only
if [[ "$push_was_active" == true ]]; then systemctl stop bacoach-pa-push.service; fi
ln -sfn "$target" /opt/bacoach/current
switched=true
systemctl restart bacoach-backend
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:8000/health
systemctl restart bacoach-frontend
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:3000/ >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:3000/ >/dev/null
if [[ "$push_was_active" == true ]]; then
  systemctl start bacoach-pa-push.service
  sleep 2
  systemctl is-active --quiet bacoach-pa-push.service
fi
trap - EXIT
echo "DEPLOYED=$target PREVIOUS=$previous"
