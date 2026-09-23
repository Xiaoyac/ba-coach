"""Guarded code-only deployment for the M1 -> M2 progression repair.

This script never migrates, imports, or copies a database/knowledge base.
The archive must contain an explicit manifest generated from the current
production release.  Usage on the server:

    python3 deploy_m1_progression_0919.py prepare|activate RELEASE_ID ARCHIVE VERIFIER
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path("/opt/bacoach")
CURRENT = ROOT / "current"
PREVIOUS = ROOT / "releases/20260918T173903Z"
ALLOWED = {
    "backend/app/m1_contract.py",
    "backend/app/clinical_fields.py",
    "backend/app/dialogue_confirmation.py",
    "backend/app/v2_workflow.py",
    "backend/app/prompts.py",
    "backend/app/router_agent.py",
    "backend/app/graph/nodes.py",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def health() -> None:
    for _ in range(30):
        result = subprocess.run(
            ["curl", "-fsS", "--max-time", "2", "-o", "/dev/null", "http://127.0.0.1:8000/health"],
            capture_output=True,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("backend health check failed")


def switch(path: Path, release_id: str) -> None:
    link = ROOT / (".m1-switch-" + release_id)
    assert not link.exists() and not link.is_symlink()
    link.symlink_to(path, target_is_directory=True)
    os.replace(link, CURRENT)


def preflight(target: Path, verifier: str, live: bool = False) -> None:
    # Load protected EnvironmentFiles before dropping to the service account,
    # matching systemd without printing secrets.
    runner = r'''import os,pwd,runpy,sys
from dotenv import load_dotenv
for p in ["/etc/bacoach/backend.env","/etc/bacoach/pa-push.env","/etc/bacoach/workbench-safety.env"]:
    load_dotenv(p,override=True)
account=pwd.getpwnam("bacoach")
os.initgroups(account.pw_name,account.pw_gid)
os.setgid(account.pw_gid); os.setuid(account.pw_uid)
from app.config import get_settings
s=get_settings()
assert s.database_schema_version == "v2" and not s.startup_db_maintenance
assert s.risk_gate_enabled and s.answer_validator_enabled and s.knowledge_mediator_enabled
assert s.provider_request_timeout_seconds == 60 and s.provider_max_retries == 0
assert not s.knowledge_mediator_include_reasoning
assert s.routing_wait_timeout_seconds == 2 and s.background_model_timeout_seconds == 30
from app.main import app
from app.m1_contract import consent_is_current,indexed_transcript
turns=[("assistant","你愿意进入目标设定吗？"),("user","愿意"),("assistant","好的")]
assert indexed_transcript(turns).find('"turn": 1') >= 0
assert consent_is_current(turns,1)
print("M1_PREFLIGHT_OK: contract evidence and consent checks; database_operations=0",flush=True)
runpy.run_path(sys.argv[1], run_name="__main__")
'''
    run(str(target / "backend/.venv/bin/python"), "-c", runner, verifier,
        *(["--live"] if live else []),
        cwd=target / "backend",
        env={**os.environ, "PYTHONPATH": str(target / "backend"), "PYTHONDONTWRITEBYTECODE": "1"})


def main() -> None:
    mode, release_id, archive_path, verifier = sys.argv[1:]
    assert mode in ("prepare", "activate") and re.fullmatch(r"\d{8}T\d{6}Z", release_id)
    assert CURRENT.resolve(strict=True) == PREVIOUS
    target = ROOT / "releases" / release_id
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == len(ALLOWED) + 1
        assert {m.name for m in members} == ALLOWED | {"manifest.json"}
        assert all(m.isfile() and m.size < 1_000_000 for m in members)
        payload = {m.name: archive.extractfile(m).read() for m in members}
    manifest = json.loads(payload.pop("manifest.json"))
    assert Path(manifest["previous"]) == PREVIOUS
    assert set(manifest["old"]) == set(manifest["new"]) == ALLOWED
    for name in ALLOWED:
        assert digest(PREVIOUS / name) == manifest["old"][name], "live source changed: " + name
        assert hashlib.sha256(payload[name]).hexdigest() == manifest["new"][name]
        compile(payload[name], name, "exec")
    if mode == "prepare":
        assert not target.exists() and shutil.disk_usage(ROOT).free > 1024**3
        target.mkdir(mode=0o750)
        (target / "backend").mkdir()
        for name in ("app", "scripts"):
            shutil.copytree(PREVIOUS / "backend" / name, target / "backend" / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(PREVIOUS / "backend/requirements.txt", target / "backend/requirements.txt")
        (target / "backend/.venv").symlink_to((PREVIOUS / "backend/.venv").resolve(), target_is_directory=True)
        (target / "frontend").symlink_to((PREVIOUS / "frontend").resolve(), target_is_directory=True)
        for name, data in payload.items():
            (target / name).write_bytes(data)
        (target / "m1-release-manifest.json").write_text(json.dumps(manifest, indent=2))
        run("chown", "-hR", "bacoach:bacoach", str(target))
    for name in ALLOWED:
        assert digest(target / name) == manifest["new"][name]
    def tree(path):
        return {str(p.relative_to(path)): digest(p) for p in path.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    old_tree, new_tree = tree(PREVIOUS / "backend/app"), tree(target / "backend/app")
    changed = {"backend/app/" + p for p in old_tree.keys() | new_tree.keys()
               if old_tree.get(p) != new_tree.get(p)}
    assert changed == ALLOWED
    assert tree(PREVIOUS / "backend/scripts") == tree(target / "backend/scripts")
    assert digest(PREVIOUS / "backend/requirements.txt") == digest(target / "backend/requirements.txt")
    assert (target / "frontend").resolve() == (PREVIOUS / "frontend").resolve()
    assert not (target / "backend/.env").exists() and not (target / "backend/psychology.db").exists()
    preflight(target, verifier, live=(mode == "prepare"))
    if mode == "prepare":
        print(json.dumps({"prepared": str(target), "database_tasks": False}), flush=True)
        return
    assert CURRENT.resolve(strict=True) == PREVIOUS
    worker = subprocess.run(["systemctl", "is-active", "--quiet", "bacoach-pa-push"]).returncode == 0
    switched = False
    try:
        if worker:
            run("systemctl", "stop", "bacoach-pa-push")
        switch(target, release_id)
        switched = True
        run("systemctl", "restart", "bacoach-backend")
        health()
        if worker:
            run("systemctl", "start", "bacoach-pa-push")
        run("systemctl", "is-active", "--quiet", "bacoach-backend", "bacoach-frontend")
        if worker:
            run("systemctl", "is-active", "--quiet", "bacoach-pa-push")
        run("curl", "-fsS", "--max-time", "20", "-o", "/dev/null", "https://bacoach.xyz/")
        code = run("curl", "-sS", "--max-time", "20", "-o", "/dev/null", "-w", "%{http_code}",
                   "https://bacoach.xyz/api/conversations", capture_output=True, text=True).stdout
        assert code == "401", code
    except BaseException:
        if switched:
            switch(PREVIOUS, release_id)
            run("systemctl", "restart", "bacoach-backend")
            health()
        if worker:
            run("systemctl", "restart", "bacoach-pa-push")
        print("ROLLED_BACK", str(PREVIOUS), flush=True)
        raise
    print(json.dumps({"deployed": str(target), "previous": str(PREVIOUS),
        "changed_source_files": sorted(ALLOWED), "frontend_unchanged": True,
        "database_tasks": False,
        "sha256": {name: digest(target / name) for name in sorted(ALLOWED)}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
