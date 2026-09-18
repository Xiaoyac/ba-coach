"""Opt in to catalog RAG for the verified release; preserve all other settings."""
import argparse
import os
from pathlib import Path
import re
import tempfile
import json

parser = argparse.ArgumentParser()
parser.add_argument("--expected-release", required=True)
parser.add_argument("--backup", required=True)
args = parser.parse_args()
current = Path("/opt/bacoach/current").resolve()
if current.name != args.expected_release or current.parent != Path("/opt/bacoach/releases"):
    raise RuntimeError("Release changed; inspect again")
from app.config import get_settings
settings = get_settings()
assert settings.database_schema_version == "v2"
assert settings.default_provider == "deepseek"
assert settings.deepseek_model == settings.deepseek_router_model == "deepseek-v4.1-flash"
assert settings.knowledge_mediator_enabled
target = Path("/etc/bacoach/backend.env")
original = target.read_bytes()
backup = Path(args.backup)
fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "wb") as handle:
    handle.write(original)
content = original.decode("utf-8")
line = "KNOWLEDGE_RETRIEVAL_MODE=catalog"
if re.search(r"(?m)^KNOWLEDGE_RETRIEVAL_MODE=.*$", content):
    content = re.sub(r"(?m)^KNOWLEDGE_RETRIEVAL_MODE=.*$", line, content)
else:
    content = content.rstrip() + "\n" + line + "\n"
fd, temporary = tempfile.mkstemp(prefix="catalog-", dir=target.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    stat = target.stat()
    os.chmod(temporary, stat.st_mode)
    os.chown(temporary, stat.st_uid, stat.st_gid)
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print(json.dumps({"previous_mode": settings.knowledge_retrieval_mode, "new_mode": "catalog", "backup": str(backup), "restart_required": True}))
