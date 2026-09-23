"""Deploy only the production frontend spinner reconciliation patch.

This intentionally clones the active release, overlays one allow-listed
frontend source file, builds the Next.js app, and switches only the release
symlink. No backend source, database migration, knowledge import, or test
data is uploaded.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
PATCH_FILE = ROOT / "frontend/components/ConversationWorkspace.tsx"
SERVER = "root@8.134.178.40"
IDENTITY = Path(os.environ.get("USERPROFILE", "")) / ".ssh/bacoach_deploy_20260907_ed25519"


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    release = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    remote_archive = f"/tmp/bacoach-frontend-spinner-{release}.tar.gz"
    remote_script = f"/tmp/bacoach-frontend-spinner-{release}.sh"
    with tempfile.TemporaryDirectory(prefix="bacoach-spinner-") as tmp:
        archive = Path(tmp) / "patch.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(PATCH_FILE, arcname="frontend/components/ConversationWorkspace.tsx")
        run("scp", "-i", str(IDENTITY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=25",
            str(archive), f"{SERVER}:{remote_archive}")

        # The remote runner is self-contained and keeps the old release
        # available for immediate rollback until health checks pass.
        script = f'''#!/usr/bin/env bash
set -Eeuo pipefail
release="{release}"
archive="{remote_archive}"
root=/opt/bacoach
previous="$(readlink -f "$root/current")"
target="$root/releases/$release"
[[ "$previous" == "$root/releases/"* && ! -e "$target" ]]
mkdir -p "$target"
cp -a "$previous/backend" "$target/backend"
cp -a "$previous/frontend" "$target/frontend"
tar -xzf "$archive" -C "$target"
chown -R bacoach:bacoach "$target"
runuser -u bacoach -- bash -lc "cd '$target/frontend' && NEXT_TELEMETRY_DISABLED=1 npm run build"
rollback() {{
  status=$?
  if [[ $status -ne 0 ]]; then
    ln -sfn "$previous" "$root/current"
    systemctl restart bacoach-frontend.service || true
  fi
  rm -f "$archive"
  exit "$status"
}}
trap rollback EXIT
ln -sfn "$target" "$root/current"
systemctl restart bacoach-frontend.service
for _ in {{1..30}}; do
  curl --fail --silent --show-error http://127.0.0.1:3000/ >/dev/null && break
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:3000/ >/dev/null
systemctl is-active --quiet bacoach-backend.service bacoach-frontend.service
echo "deployed=$target previous=$previous patch_sha256={sha256(PATCH_FILE)} backend_unchanged=true database_tasks=false"
trap - EXIT
rm -f "$archive"
'''
        local_script = Path(tmp) / "deploy.sh"
        local_script.write_text(script, encoding="utf-8", newline="\n")
        run("scp", "-i", str(IDENTITY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=25",
            str(local_script), f"{SERVER}:{remote_script}")
        output = run("ssh", "-i", str(IDENTITY), "-o", "BatchMode=yes", SERVER,
                     f"install -m 0755 '{remote_script}' /tmp/bacoach-frontend-spinner-runner.sh && "
                     f"/tmp/bacoach-frontend-spinner-runner.sh && rm -f '{remote_script}'", capture=True)
        print(output.strip())


if __name__ == "__main__":
    main()
