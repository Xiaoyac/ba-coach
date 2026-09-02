#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: server-deploy.sh RELEASE_ID ARCHIVE" >&2
  exit 2
fi

release_id="$1"
archive="$2"
root_dir="/opt/bacoach"
release_dir="$root_dir/releases/$release_id"
current_link="$root_dir/current"
previous_target=""

if [[ ! "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "invalid release id: $release_id" >&2
  exit 2
fi
if [[ ! -f "$archive" ]]; then
  echo "archive not found: $archive" >&2
  exit 2
fi
if [[ -e "$release_dir" ]]; then
  echo "release already exists: $release_dir" >&2
  exit 2
fi

if [[ -L "$current_link" ]]; then
  previous_target="$(readlink -f "$current_link")"
fi

rollback() {
  local status=$?
  if [[ $status -ne 0 && -n "$previous_target" && -d "$previous_target" ]]; then
    echo "deployment failed; restoring $previous_target" >&2
    ln -sfn "$previous_target" "$current_link"
    systemctl restart bacoach-backend.service bacoach-frontend.service || true
  fi
  exit "$status"
}
trap rollback EXIT

install -d -m 0750 -o bacoach -g bacoach "$release_dir"
tar -xzf "$archive" -C "$release_dir"
chown -R bacoach:bacoach "$release_dir"

echo "creating backend virtual environment"
runuser -u bacoach -- python3 -m venv "$release_dir/backend/.venv"
runuser -u bacoach -- env \
  PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
  "$release_dir/backend/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-cache-dir \
  -r "$release_dir/backend/requirements.txt"

echo "installing and building frontend"
runuser -u bacoach -- bash -lc "cd '$release_dir/frontend' && NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci && NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build && NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm prune --omit=dev"

echo "running idempotent app-owned schema migrations"
runuser -u bacoach -- bash -lc "set -a; source <(sed 's/\r$//' /etc/bacoach/backend.env); set +a; cd '$release_dir/backend' && PYTHONPATH='$release_dir/backend' .venv/bin/python scripts/add_latency_telemetry.py"

echo "importing curated shared knowledge base"
runuser -u bacoach -- bash -lc "set -a; source <(sed 's/\r$//' /etc/bacoach/backend.env); set +a; cd '$release_dir/backend' && PYTHONPATH='$release_dir/backend' .venv/bin/python scripts/import_project_knowledge.py"

ln -sfn "$release_dir" "$current_link"
systemctl daemon-reload
systemctl restart bacoach-backend.service

for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null

systemctl restart bacoach-frontend.service
for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:3000/ >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:3000/ >/dev/null

trap - EXIT
rm -f "$archive"
echo "deployed $release_id"
