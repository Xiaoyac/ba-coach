#!/usr/bin/env bash
# Two-phase release. Derived indexes only: no migration/import/business writes.
set -Eeuo pipefail
action="$1"
release_id="$2"
expected_previous="$3"
[[ "$action" == prepare || "$action" == activate ]] || exit 2
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
target="/opt/bacoach/releases/$release_id"
[[ "$(readlink -f /opt/bacoach/current)" == "$expected_previous" ]] || { echo 'production changed; rebase required' >&2; exit 2; }
if [[ "$action" == prepare ]]; then
  [[ ! -e "$target" ]] || exit 2
  install -d -m 0750 -o bacoach -g bacoach "$target"
  tar -xzf "/tmp/bacoach-$release_id.tar.gz" -C "$target"
  ln -s /opt/bacoach/venvs/hybrid-20261002 "$target/backend/.venv"
  cat > "$target/backend/.env" <<'ENV'
KNOWLEDGE_HYBRID_STORAGE=/opt/bacoach/knowledge-hybrid
KNOWLEDGE_HYBRID_PRELOAD=true
ENV
  chown -R bacoach:bacoach "$target"
  "$target/backend/.venv/bin/python" -m pip check
  runuser -u bacoach -- bash -c "set -e; cd '$target/frontend'; NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci --no-audit --no-fund; NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build"
  set -a
  source <(sed 's/\r$//' /etc/bacoach/backend.env)
  source /etc/bacoach/workbench-safety.env
  set +a
  cd "$target/backend"
  .venv/bin/python -m compileall -q app
  .venv/bin/python scripts/check_knowledge_mediator.py --configuration-only
  .venv/bin/python scripts/check_hybrid_retrieval.py --compare --output "/opt/bacoach/acceptance/hybrid-$release_id.json"
  chown -R bacoach:bacoach /opt/bacoach/knowledge-hybrid
  touch "$target/.hybrid-ready"
  echo "PREPARED=$target"
  exit 0
fi
[[ -f "$target/.hybrid-ready" ]] || { echo 'prepare and acceptance must pass first' >&2; exit 2; }
switched=false
active_workers=()
for worker in bacoach-pa-push.service bacoach-pa-chat-reminders.service; do
  if systemctl is-active --quiet "$worker"; then active_workers+=("$worker"); fi
done
rollback() {
  code=$?
  if [[ $code -ne 0 && "$switched" == true ]]; then
    ln -sfn "$expected_previous" /opt/bacoach/current
    systemctl restart bacoach-backend bacoach-frontend || true
  fi
  for worker in "${active_workers[@]}"; do systemctl start "$worker" || true; done
  exit "$code"
}
trap rollback EXIT
for worker in "${active_workers[@]}"; do systemctl stop "$worker"; done
ln -sfn "$target" /opt/bacoach/current
switched=true
systemctl restart bacoach-backend
for _ in {1..60}; do curl --fail --silent http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:8000/health
systemctl restart bacoach-frontend
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:3000/ >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:3000/ >/dev/null
for worker in "${active_workers[@]}"; do systemctl start "$worker"; systemctl is-active --quiet "$worker"; done
trap - EXIT
echo "DEPLOYED=$target PREVIOUS=$expected_previous"
