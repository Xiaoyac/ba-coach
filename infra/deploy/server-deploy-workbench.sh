#!/usr/bin/env bash
# Scoped additive release: no business migrations or knowledge replacement.
set -Eeuo pipefail
release_id="$1"
archive="$2"
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
[[ "$archive" == "/tmp/bacoach-$release_id.tar.gz" && -f "$archive" ]] || exit 2
previous="$(readlink -f /opt/bacoach/current)"
target="/opt/bacoach/releases/$release_id"
[[ "$previous" == /opt/bacoach/releases/* && ! -e "$target" ]] || exit 2
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
install -d -m 0750 -o bacoach -g bacoach "$target"
tar -xzf "$archive" -C "$target"
# Dependencies are unchanged; reuse the existing pinned environment without upgrading it.
python3 - "$previous/backend/requirements.txt" "$target/backend/requirements.txt" <<'PY'
import pathlib,sys
assert pathlib.Path(sys.argv[1]).read_text().splitlines()==pathlib.Path(sys.argv[2]).read_text().splitlines(), 'Backend dependencies differ'
PY
ln -s "$previous/backend/.venv" "$target/backend/.venv"
chown -R bacoach:bacoach "$target"
echo "Installing/building frontend; current service remains online"
runuser -u bacoach -- bash -c "cd '$target/frontend'; NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci --no-audit --no-fund; NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build"
echo "Creating only three evaluation tables"
runuser -u bacoach -- bash -c "cd '$target/backend'; .venv/bin/python scripts/create_evaluation_tables.py --env-file /etc/bacoach/backend.env --expected-database ba_coach_260908 --apply"
runuser -u bacoach -- bash -c "set -a; source <(sed 's/\r$//' /etc/bacoach/backend.env); set +a; cd '$target/backend'; .venv/bin/python scripts/check_knowledge_mediator.py --configuration-only"
echo "Switching release"
ln -sfn "$target" /opt/bacoach/current
switched=true
systemctl restart bacoach-backend
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:8000/health >/dev/null
systemctl restart bacoach-frontend
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:3000/ >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:3000/ >/dev/null
trap - EXIT
echo "DEPLOYED=$target PREVIOUS=$previous"
