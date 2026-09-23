"""Build a two-file release, or activate it using the existing guarded runner."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile

FILES = {"backend/app/dialogue_confirmation.py", "backend/app/v2_workflow.py"}
PREVIOUS = "/opt/bacoach/releases/20260919T090959Z"


def build():
    root = Path(__file__).resolve().parents[2]
    work = root / "work/goal-release-0919"
    base, candidate = work / "baseline", work / "candidate"
    assert not candidate.exists()
    shutil.copytree(base / "backend", candidate / "backend")
    for folder in ("tests", "evals"):
        shutil.copytree(root / "backend" / folder, candidate / "backend" / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(root / "backend/pytest.ini", candidate / "backend/pytest.ini")
    manifest = {"previous": PREVIOUS, "old": {}, "new": {}}
    for name in FILES:
        old, new = (base / name).read_bytes(), (root / name).read_bytes()
        compile(new, name, "exec")
        manifest["old"][name] = hashlib.sha256(old).hexdigest()
        manifest["new"][name] = hashlib.sha256(new).hexdigest()
        (candidate / name).write_bytes(new)
    (candidate / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with tarfile.open(work / "patch.tar.gz", "w:gz") as archive:
        for name in sorted(FILES | {"manifest.json"}):
            archive.add(candidate / name, arcname=name, recursive=False)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build()
    else:
        import deploy_consent_reply_0919 as runner
        runner.PREVIOUS = Path(PREVIOUS)
        runner.ALLOWED = FILES
        runner.main()
