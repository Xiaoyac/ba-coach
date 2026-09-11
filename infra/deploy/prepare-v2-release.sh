#!/usr/bin/env bash
# Build only. Does not switch services or mutate production tables.
set -Eeuo pipefail
release_id="$1"
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
target="/opt/bacoach/releases/$release_id"
previous=$(readlink -f /opt/bacoach/current)
[[ "$previous" == /opt/bacoach/releases/* && ! -e "$target" ]] || exit 2
install -d -m 0750 -o bacoach -g bacoach "$target"
tar -xzf "/tmp/bacoach-$release_id.tar.gz" -C "$target"
python3 - "$previous/backend/requirements.txt" "$target/backend/requirements.txt" <<'PY'
import pathlib,sys
assert pathlib.Path(sys.argv[1]).read_text().splitlines()==pathlib.Path(sys.argv[2]).read_text().splitlines()
PY
ln -s "$(readlink -f "$previous/backend/.venv")" "$target/backend/.venv"
chown -R bacoach:bacoach "$target"
runuser -u bacoach -- bash -c "set -e; cd '$target/frontend'; NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci --no-audit --no-fund; NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build"
cd "$target/backend"
.venv/bin/python scripts/smoke_v2_application.py
echo "PREPARED=$target PRODUCTION_UNCHANGED=true"
