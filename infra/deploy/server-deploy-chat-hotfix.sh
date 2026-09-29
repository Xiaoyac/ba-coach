#!/usr/bin/env bash
# Code-only deployment: no migrations, backfills, or knowledge imports.
set -Eeuo pipefail
release_id="$1"
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
target="/opt/bacoach/releases/$release_id"
previous=$(readlink -f /opt/bacoach/current)
[[ "$previous" == /opt/bacoach/releases/* && ! -e "$target" ]] || exit 2
expected_previous="${2:-$previous}"
[[ "$previous" == "$expected_previous" ]] || { echo 'production changed; rebase required' >&2; exit 2; }
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
"$previous/backend/.venv/bin/python" - "$previous/backend/requirements.txt" "$target/backend/requirements.txt" <<'PY'
import pathlib, sys
from importlib.metadata import version
from packaging.specifiers import SpecifierSet
def requirements(path):
    return {line.split('#', 1)[0].strip() for line in pathlib.Path(path).read_text().splitlines()
            if line.split('#', 1)[0].strip()}
old, new = (requirements(path) for path in sys.argv[1:])
# This release makes an already-installed LangGraph dependency explicit.
# Do not upgrade the shared environment or accept any other dependency change.
assert not old - new and new - old <= {'langchain-core>=1.6.2,<2'}, 'requires full deployment'
assert version('langchain-core') in SpecifierSet('>=1.6.2,<2')
print('validated existing langchain-core', version('langchain-core'))
PY
"$previous/backend/.venv/bin/python" -m pip check
ln -s "$(readlink -f "$previous/backend/.venv")" "$target/backend/.venv"
chown -R bacoach:bacoach "$target"
runuser -u bacoach -- bash -c "set -e; cd '$target/frontend'; NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci --no-audit --no-fund; NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build"
set -a
source <(sed 's/\r$//' /etc/bacoach/backend.env)
source /etc/bacoach/workbench-safety.env
set +a
cd "$target/backend"
.venv/bin/python -m compileall -q app
.venv/bin/python scripts/check_knowledge_mediator.py --configuration-only
.venv/bin/python - <<'PY'
from app.main import app
from app.graph import get_graph
from app.context_pipeline import prepare_context
from app.conversation_time import utc_now
context = prepare_context(system=[], history=[], user_input='release-check',
                          max_history_messages=80, user_created_at=utc_now())
assert context.metrics['engine'] == 'langchain'
assert 'Asia/Shanghai' in context.system[-1].text
print('context and server clock ready')
PY
[[ "$(readlink -f /opt/bacoach/current)" == "$expected_previous" ]] || { echo 'production changed during build' >&2; exit 2; }
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
