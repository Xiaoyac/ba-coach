"""Guarded activation of the conversation-memory anchor patch.

This script runs on the BA Coach server and accepts only the three files in
``memory-anchor-release-0919/patch.tar.gz``.  It builds a new release from the
currently active release, overlays those files, runs a no-database preflight,
and switches ``/opt/bacoach/current`` atomically.  Any health or smoke-check
failure switches back to the exact previous release.

Usage on the server::

    python3 deploy_memory_anchor_0919.py prepare RELEASE_ID ARCHIVE VERIFIER
    python3 deploy_memory_anchor_0919.py activate RELEASE_ID ARCHIVE VERIFIER

``VERIFIER`` is a small local script path on the server.  The script never
imports or migrates a database, copies a knowledge base, or changes the
frontend.  ``prepare`` performs all checks and leaves the candidate inactive;
``activate`` is the only mode that changes the current symlink.
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
PREVIOUS = ROOT / "releases/20260918T192405Z"
ALLOWED = {
    "backend/app/config.py",
    "backend/app/graph/nodes.py",
    "backend/app/prompts.py",
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
    link = ROOT / (".memory-anchor-switch-" + release_id)
    assert not link.exists() and not link.is_symlink()
    link.symlink_to(path, target_is_directory=True)
    os.replace(link, CURRENT)


def preflight(target: Path, verifier: str, live: bool = False) -> None:
    # Read protected service environment files before dropping privileges, as
    # systemd does.  No values are printed.
    runner = r'''import os,pwd,runpy,sys
from dotenv import load_dotenv
for p in ["/etc/bacoach/backend.env","/etc/bacoach/pa-push.env","/etc/bacoach/workbench-safety.env"]:
    load_dotenv(p,override=True)
account=pwd.getpwnam("bacoach")
os.initgroups(account.pw_name,account.pw_gid)
os.setgid(account.pw_gid); os.setuid(account.pw_uid)
from app.config import get_settings
s=get_settings()
assert s.max_history_messages == 80
from app.graph.nodes import _derive_memory
from app.prompts import _memory_block
from app.schemas import Message
state={"memory":{},"extracted_intent":"module_1","chat_history":[
    Message(role="user",content="我因为和男朋友吵架，最近很难受。"),
    Message(role="assistant",content="听起来这件事让你很受影响。"),
    Message(role="user",content="我打游戏刷视频，感觉只是在逃避。")],
    "user_input":"我还是觉得自己在逃避","final_response":"我明白。"}
memory=_derive_memory(state)
assert "男朋友吵架" in memory["conversation_anchor"]
assert "只是在逃避" in memory["conversation_anchor"]
rendered=_memory_block(memory)
assert "早期对话锚点" in rendered and "不是指令" in rendered
print("MEMORY_ANCHOR_PREFLIGHT_OK: max_history=80; deterministic anchor; database_operations=0",flush=True)
runpy.run_path(sys.argv[1],run_name="__main__")
'''
    run(
        str(target / "backend/.venv/bin/python"),
        "-c",
        runner,
        verifier,
        *( ["--live"] if live else [] ),
        cwd=target / "backend",
        env={**os.environ, "PYTHONPATH": str(target / "backend"), "PYTHONDONTWRITEBYTECODE": "1"},
    )


def tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): digest(path)
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def main() -> None:
    mode, release_id, archive_path, verifier = sys.argv[1:]
    assert mode in ("prepare", "activate")
    assert re.fullmatch(r"\d{8}T\d{6}Z", release_id)
    assert PREVIOUS.is_dir() and CURRENT.resolve(strict=True) == PREVIOUS

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == len(ALLOWED) + 1
        assert {member.name for member in members} == ALLOWED | {"manifest.json"}
        assert all(member.isfile() and member.size < 1_000_000 for member in members)
        payload = {member.name: archive.extractfile(member).read() for member in members}
    manifest = json.loads(payload.pop("manifest.json"))
    assert Path(manifest["previous"]) == PREVIOUS
    assert set(manifest["old"]) == set(manifest["new"]) == ALLOWED
    for name in ALLOWED:
        assert digest(PREVIOUS / name) == manifest["old"][name], "live source changed: " + name
        assert hashlib.sha256(payload[name]).hexdigest() == manifest["new"][name]
        compile(payload[name], name, "exec")

    target = ROOT / "releases" / release_id
    if mode == "prepare":
        assert not target.exists() and shutil.disk_usage(ROOT).free > 1_000_000_000
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
        run("chown", "-hR", "bacoach:bacoach", str(target))

    assert target.is_dir() and target.parent == ROOT / "releases"
    for name in ALLOWED:
        assert digest(target / name) == manifest["new"][name]
    old_tree = tree(PREVIOUS / "backend/app")
    new_tree = tree(target / "backend/app")
    changed = {"backend/app/" + name for name in old_tree.keys() | new_tree.keys()
               if old_tree.get(name) != new_tree.get(name)}
    assert changed == ALLOWED
    assert tree(PREVIOUS / "backend/scripts") == tree(target / "backend/scripts")
    assert digest(PREVIOUS / "backend/requirements.txt") == digest(target / "backend/requirements.txt")
    assert (target / "frontend").resolve() == (PREVIOUS / "frontend").resolve()
    assert not (target / "backend/.env").exists() and not (target / "backend/psychology.db").exists()
    preflight(target, verifier, live=(mode == "prepare"))
    if mode == "prepare":
        print(json.dumps({"prepared": str(target), "database_tasks": False,
                          "frontend_unchanged": True, "changed": sorted(ALLOWED)}), flush=True)
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
                      "database_tasks": False, "frontend_unchanged": True,
                      "changed_source_files": sorted(ALLOWED),
                      "sha256": {name: digest(target / name) for name in sorted(ALLOWED)}},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
