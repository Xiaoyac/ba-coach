#!/usr/bin/env bash
# Promote a tested backend-only fix using the already verified frontend build.
# No DB tasks. A distinct release directory preserves rollback.
set -Eeuo pipefail
previous=/opt/bacoach/releases/20260914T015618Z
test "$(readlink -f /opt/bacoach/current)" = "$previous"
test -f /tmp/bacoach-m1-program-0914.py
release_id=$(date -u +%Y%m%dT%H%M%SZ)
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
target="/opt/bacoach/releases/$release_id"
test ! -e "$target"
cp -a --reflink=auto "$previous" "$target"
install -m 0644 -o bacoach -g bacoach /tmp/bacoach-m1-program-0914.py "$target/backend/app/routes/program.py"
"$target/backend/.venv/bin/python" -m py_compile "$target/backend/app/routes/program.py"
rollback() {
  result=$?
  ln -sfn "$previous" /opt/bacoach/current
  systemctl restart bacoach-backend bacoach-frontend || true
  exit "$result"
}
trap rollback ERR
ln -sfn "$target" /opt/bacoach/current
systemctl restart bacoach-backend bacoach-frontend
for n in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8000/health
curl --fail --silent http://127.0.0.1:3000/ >/dev/null
trap - ERR
echo " verified_release=$target"
