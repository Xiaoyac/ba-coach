"""Build from exact live sources; exclude all other dirty-worktree changes."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work/route-empty-0919"
BASE = WORK / "baseline"
OUT = WORK / "candidate-final"
FILES = {"backend/app/m1_contract.py", "backend/app/providers/deepseek.py",
         "backend/app/reply_recovery.py", "backend/app/graph/nodes.py",
         "backend/app/prompts.py", "backend/app/conversation_store.py"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def candidate(name):
    local = (ROOT / name).read_text(encoding="utf-8")
    base = (BASE / name).read_text(encoding="utf-8") if (BASE / name).exists() else ""
    if name.endswith(("m1_contract.py", "providers/deepseek.py", "reply_recovery.py")):
        return local
    if name.endswith("nodes.py"):
        marker = "        if context.settings.answer_validator_enabled or authoritative:\n            authority = await reply_authority()  # recheck after generation, including other-chat updates"
        start = local.index("        # A reasoning budget exhausted without a visible answer")
        end = local.index(marker, start)
        assert base.count(marker) == 1
        return base.replace(marker, local[start:end] + marker, 1)
    if name.endswith("prompts.py"):
        pattern = r'M1_RUNTIME_CONTRACT = """\\\n.*?\n"""'
        block = re.search(pattern, local, re.S).group(0)
        patched, count = re.subn(pattern, lambda _: block, base, count=1, flags=re.S)
        assert count == 1
        return patched
    marker = '            "time_to_first_content_token_ms": metrics.get("time_to_first_content_token_ms"),'
    assert base.count(marker) == 1
    return base.replace(marker, marker + '\n            "reply_recovery": metrics.get("reply_recovery"),', 1)


def main():
    assert not OUT.exists(), "Preserve previous candidate; choose a fresh directory"
    shutil.copytree(BASE / "backend/app", OUT / "checkout/backend/app",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "backend/tests", OUT / "checkout/backend/tests",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "backend/evals", OUT / "checkout/backend/evals")
    shutil.copy2(ROOT / "backend/pytest.ini", OUT / "checkout/backend/pytest.ini")
    for name in sorted(FILES):
        data = candidate(name).encode("utf-8")
        compile(data, name, "exec")
        target = OUT / "payload" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        (OUT / "checkout" / name).write_bytes(data)
    manifest = {"previous": "/opt/bacoach/releases/20260918T195630Z",
        "old": {name: digest(BASE / name) for name in sorted(FILES)},
        "new": {name: digest(OUT / "payload" / name) for name in sorted(FILES)}}
    (OUT / "payload/manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with tarfile.open(OUT / "patch.tar.gz", "w:gz") as archive:
        for name in sorted(FILES | {"manifest.json"}):
            archive.add(OUT / "payload" / name, arcname=name, recursive=False)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
