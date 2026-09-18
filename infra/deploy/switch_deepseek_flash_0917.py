"""Explicit two-key model switch; retain a private rollback copy, never print secrets."""
import argparse
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime, timezone

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
path = Path("/etc/bacoach/backend.env")
if not path.is_file() or path.is_symlink():
    raise RuntimeError("Expected the regular production environment file")
original = path.read_text()
updated = original
for key in ("DEEPSEEK_MODEL", "DEEPSEEK_ROUTER_MODEL"):
    pattern = re.compile(r"^" + key + r"=([^\r\n]*)$", re.MULTILINE)
    matches = pattern.findall(updated)
    if len(matches) != 1 or matches[0].strip("\"'") not in ("deepseek-v4-pro", "deepseek-v4.1-flash"):
        raise RuntimeError(f"Unexpected {key}; no changes made")
    updated = pattern.sub(key + "=deepseek-v4.1-flash", updated)
    print(f"{key}: deepseek-v4.1-flash")
if args.apply and updated != original:
    backup = path.with_name(path.name + ".before-flash-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    stat = path.stat()
    fd, temporary = tempfile.mkstemp(prefix=".model-update-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(updated)
        os.chmod(temporary, stat.st_mode & 0o777)
        os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("APPLIED only the two model keys; protected backup: " + str(backup))
elif updated == original:
    print("Already configured; no changes")
else:
    print("Dry run; no changes")
