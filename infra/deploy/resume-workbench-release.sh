#!/usr/bin/env bash
set -Eeuo pipefail
target=/opt/bacoach/releases/20260909T074652Z
previous=$(readlink -f /opt/bacoach/current)
[[ "$previous" == /opt/bacoach/releases/20260906T191709Z && -d "$target/frontend/.next" ]]
test ! -e /etc/systemd/system/bacoach-backend.service.d/90-workbench-safety.conf
install -m 0644 /tmp/workbench-safety.env /etc/bacoach/workbench-safety.env
install -d /etc/systemd/system/bacoach-backend.service.d
install -m 0644 /tmp/workbench-safety.conf /etc/systemd/system/bacoach-backend.service.d/90-workbench-safety.conf
systemctl daemon-reload
set -a
source <(sed 's/\r$//' /etc/bacoach/backend.env)
source /etc/bacoach/workbench-safety.env
set +a
cd "$target/backend"
.venv/bin/python scripts/check_knowledge_mediator.py --configuration-only
switched=false
rollback() {
  code=$?
  if [[ $code -ne 0 && "$switched" == true ]]; then
    ln -sfn "$previous" /opt/bacoach/current
    systemctl restart bacoach-backend bacoach-frontend || true
  fi
  exit "$code"
}
trap rollback EXIT
ln -sfn "$target" /opt/bacoach/current
switched=true
systemctl restart bacoach-backend
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:8000/health
systemctl restart bacoach-frontend
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:3000/ >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:3000/ >/dev/null
trap - EXIT
echo "DEPLOYED=$target PREVIOUS=$previous"
.venv/bin/python scripts/check_knowledge_mediator.py
