#!/usr/bin/env bash
# Code-only, guarded, two-phase release. No migrations, secret copies or imports.
set -Eeuo pipefail
action="$1"; release_id="$2"; previous="$3"
[[ "$action" == prepare || "$action" == activate ]] || exit 2
[[ "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 2
target="/opt/bacoach/releases/$release_id"
exec 9>/opt/bacoach/.deploy.lock
flock -n 9 || { echo 'another deployment is active'; exit 2; }
[[ "$(readlink -f /opt/bacoach/current)" == "$previous" ]] || { echo 'production changed; rebase required'; exit 2; }
reference=/etc/bacoach/lead-k3-reference.env
dropin=/etc/systemd/system/bacoach-backend.service.d/99-lead-k3-reference.conf
[[ ! -e "$reference" && ! -e "$dropin" ]] || { echo 'configuration target already exists; inspect before retry'; exit 2; }
check_hashes() {
 python3 - "$1" "$2" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]); spec=json.loads(Path(sys.argv[2]).read_text())
for file,digest in spec.items():
 p=root/file
 assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==digest, file
print('verified',len(spec),'source hashes')
PY
}
check_hashes "$previous" "/tmp/bacoach-$release_id-baseline.json"
if [[ "$action" == prepare ]]; then
 [[ ! -e "$target" ]] || exit 2
 install -d -m 0750 -o bacoach -g bacoach "$target"
 tar -xzf "/tmp/bacoach-$release_id.tar.gz" -C "$target"
 check_hashes "$target" "$target/RELEASE_SOURCE_HASHES.json"
 ln -s "$(readlink -f "$previous/backend/.venv")" "$target/backend/.venv"
 # Reference the existing config in place. Never print/copy secrets into artifacts.
 if [[ -e "$previous/backend/.env" ]]; then ln -s "$previous/backend/.env" "$target/backend/.env"; fi
 chown -R bacoach:bacoach "$target"
 "$target/backend/.venv/bin/python" -m pip check
 "$target/backend/.venv/bin/python" -m compileall -q "$target/backend/app"
 runuser -u bacoach -- bash -c "set -e; cd '$target/frontend'; NPM_CONFIG_REGISTRY=https://registry.npmmirror.com /usr/local/bin/npm ci --ignore-scripts --no-audit --no-fund; NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/npm run build"
 cd "$target/backend"
 .venv/bin/python - "$target/infra/deploy/lead-k3-reference.env" <<'PY'
from dotenv import load_dotenv
import sys
for p in ['/etc/bacoach/backend.env','/etc/bacoach/pa-push.env','/etc/bacoach/workbench-safety.env',sys.argv[1]]:load_dotenv(p,override=True)
from app.config import get_settings
from app.providers import get_provider
from app.provisional_reply import select_lead_provider
from app.generation_policy import is_ark_kimi
s=get_settings();p=get_provider('deepseek');lead=select_lead_provider(p,s)
assert not s.startup_db_maintenance, 'startup DB maintenance must already be disabled'
assert lead is p and is_ark_kimi(s,'deepseek',model=lead.model)
assert s.deepseek_api_key and not s.reply_lead_api_key and not s.reply_lead_base_url
assert s.reply_lead_max_tokens==2048 and s.reply_lead_model=='kimi-k3'
print('PASS: existing Ark K3 provider reference; no new secret; DB maintenance disabled')
PY
 touch "$target/.router-lead-ready"
 echo "PREPARED=$target"
 exit 0
fi
[[ -f "$target/.router-lead-ready" ]]
check_hashes "$target" "$target/RELEASE_SOURCE_HASHES.json"
workers=(); switched=false; configured=false
for worker in bacoach-pa-push.service bacoach-pa-chat-reminders.service; do
 if systemctl is-active --quiet "$worker"; then workers+=("$worker"); fi
done
rollback() {
 code=$?
 if [[ "$code" -ne 0 ]]; then
  if [[ "$switched" == true && "$(readlink -f /opt/bacoach/current)" == "$target" ]]; then ln -sfn "$previous" /opt/bacoach/current; fi
  if [[ "$configured" == true ]]; then rm -f "$dropin" "$reference"; systemctl daemon-reload; fi
  systemctl restart bacoach-backend bacoach-frontend || true
  for worker in "${workers[@]}"; do systemctl start "$worker" || true; done
  echo "ROLLED_BACK=$previous"
 fi
 exit "$code"
}
trap rollback EXIT
for worker in "${workers[@]}"; do systemctl stop "$worker"; done
install -m 0640 -o root -g bacoach "$target/infra/deploy/lead-k3-reference.env" "$reference"
configured=true
install -d -m 0755 "$(dirname "$dropin")"
printf '[Service]\nEnvironmentFile=%s\n' "$reference" > "$dropin"
chmod 0644 "$dropin"
systemctl daemon-reload
ln -sfn "$target" /opt/bacoach/current
switched=true
systemctl restart bacoach-backend
for _ in {1..60}; do curl --fail --silent http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:8000/health >/dev/null
systemctl restart bacoach-frontend
for _ in {1..40}; do curl --fail --silent http://127.0.0.1:3000/ >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:3000/ >/dev/null
curl --fail --silent https://bacoach.xyz/ >/dev/null
systemctl is-active bacoach-backend bacoach-frontend
for worker in "${workers[@]}"; do systemctl start "$worker"; systemctl is-active --quiet "$worker"; done
trap - EXIT
echo "DEPLOYED=$target PREVIOUS=$previous COMMIT=$(cat "$target/RELEASE_COMMIT")"
