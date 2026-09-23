#!/usr/bin/env bash
set -Eeuo pipefail

release_id="20260920T153818Z"
root_dir="/opt/bacoach"
release_dir="$root_dir/releases/$release_id"
current_link="$root_dir/current"
archive="/tmp/bacoach-$release_id.tar.gz"
previous_target="$(readlink -f "$current_link")"
push_was_active="false"
deploy_env_file="$release_dir/.deploy-backend.env"

if systemctl is-active --quiet bacoach-pa-push.service; then
  push_was_active="true"
fi
if [[ "$previous_target" != "$root_dir/releases/20260919T130000Z" ]]; then
  echo "unexpected current release: $previous_target" >&2
  exit 1
fi
if [[ ! -x "$release_dir/backend/.venv/bin/python" || ! -f "$release_dir/frontend/.next/BUILD_ID" ]]; then
  echo "release build is incomplete" >&2
  exit 1
fi

rollback() {
  local status=$?
  rm -f "$deploy_env_file"
  if [[ $status -ne 0 && -d "$previous_target" ]]; then
    echo "resume failed; restoring $previous_target" >&2
    if [[ "$push_was_active" == "true" ]]; then
      systemctl stop bacoach-pa-push.service || true
    fi
    ln -sfn "$previous_target" "$current_link"
    systemctl restart bacoach-backend.service bacoach-frontend.service || true
    if [[ "$push_was_active" == "true" ]]; then
      systemctl start bacoach-pa-push.service || true
    fi
  fi
  exit "$status"
}
trap rollback EXIT

install -o bacoach -g bacoach -m 0600 /etc/bacoach/backend.env "$deploy_env_file"
runuser -u bacoach -- bash -lc "set -a; source <(sed 's/\r$//' '$deploy_env_file'); set +a; cd '$release_dir/backend' && PYTHONPATH='$release_dir/backend' .venv/bin/python scripts/add_latency_telemetry.py"
runuser -u bacoach -- bash -lc "set -a; source <(sed 's/\r$//' '$deploy_env_file'); set +a; cd '$release_dir/backend' && PYTHONPATH='$release_dir/backend' .venv/bin/python scripts/import_project_knowledge.py"
rm -f "$deploy_env_file"

if [[ "$push_was_active" == "true" ]]; then
  systemctl stop bacoach-pa-push.service
fi
ln -sfn "$release_dir" "$current_link"
systemctl daemon-reload
systemctl restart bacoach-backend.service
for _ in {1..30}; do
  curl --fail --silent http://127.0.0.1:8000/health >/dev/null && break
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null
systemctl restart bacoach-frontend.service
for _ in {1..30}; do
  curl --fail --silent http://127.0.0.1:3000/ >/dev/null && break
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:3000/ >/dev/null
if [[ "$push_was_active" == "true" ]]; then
  systemctl restart bacoach-pa-push.service
  systemctl is-active --quiet bacoach-pa-push.service
fi

trap - EXIT
rm -f "$archive"
echo "resumed and deployed $release_id"
