"""Guarded, backend-only latency patch. Run over SSH; never imports/updates DB data.

Usage: python3 deploy_chat_latency_patch.py RELEASE_ID /tmp/PATCH.tar.gz
Only the five allowlisted source files may differ from the live release.
"""
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

ROOT = Path('/opt/bacoach')
CURRENT = ROOT / 'current'
EXPECTED_PREVIOUS = ROOT / 'releases/20260917T142227Z'
BASE_HASHES = {
    'backend/app/config.py': '5d81e8c4542a5e8bf6be0a4110d6beaa2328ef5a2660c21142c11f6f9efab4d3',
    'backend/app/providers/deepseek.py': '863d08867cc96f229071fc27aec1b82b00bc8d9c03a7f4af83df46ae7fd2f6e8',
    'backend/app/providers/doubao.py': 'd032ff44fa029e9a7cbecce8970985d6263d024ccef8cc22390a4327273da896',
    'backend/app/retrieval_intent.py': '72be15a3c1f6a2c64fef84d197bcef23a86e994220d92c1330a44897292fb6ac',
}
ALLOWED = {*BASE_HASHES, 'backend/app/generation_policy.py'}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def switch_to(path, release_id):
    link = ROOT / ('.latency-switch-' + release_id)
    assert not link.exists() and not link.is_symlink()
    link.symlink_to(path, target_is_directory=True)
    os.replace(link, CURRENT)


def wait_health():
    for _ in range(30):
        result = subprocess.run(['curl', '-fsS', '--max-time', '2', '-o', '/dev/null',
                                 'http://127.0.0.1:8000/health'], capture_output=True)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError('Backend health check failed')


def main():
    release_id, archive_path = sys.argv[1:]
    assert re.fullmatch(r'\d{8}T\d{6}Z', release_id)
    assert CURRENT.is_symlink() and CURRENT.resolve(strict=True) == EXPECTED_PREVIOUS
    previous = CURRENT.resolve(strict=True)
    target = ROOT / 'releases' / release_id
    assert not target.exists() and not target.is_symlink()
    assert not (previous / 'backend/.env').exists()
    assert not (previous / 'backend/app/generation_policy.py').exists()
    for name, expected in BASE_HASHES.items():
        assert digest(previous / name) == expected, f'Live source changed: {name}'
    assert shutil.disk_usage(ROOT).free > 1024**3
    assert {p.name for p in (previous / 'backend').iterdir()} <= {'app', 'scripts', '.venv', 'requirements.txt'}

    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        assert len(members) == len(ALLOWED)
        assert {m.name for m in members} == ALLOWED
        assert all(m.isfile() and m.size < 1000000 for m in members)
        payload = {m.name: archive.extractfile(m).read() for m in members}
    for name, data in payload.items():
        compile(data, name, 'exec')

    target.mkdir(mode=0o750)
    (target / 'backend').mkdir()
    for name in ('app', 'scripts'):
        shutil.copytree(previous / 'backend' / name, target / 'backend' / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(previous / 'backend/requirements.txt', target / 'backend/requirements.txt')
    (target / 'backend/.venv').symlink_to((previous / 'backend/.venv').resolve(), target_is_directory=True)
    (target / 'frontend').symlink_to((previous / 'frontend').resolve(), target_is_directory=True)
    for name, data in payload.items():
        (target / name).write_bytes(data)
    old_tree, new_tree = tree_hashes(previous / 'backend/app'), tree_hashes(target / 'backend/app')
    changed = {p for p in old_tree.keys() | new_tree.keys() if old_tree.get(p) != new_tree.get(p)}
    assert {'backend/app/' + p for p in changed} == ALLOWED
    assert tree_hashes(previous / 'backend/scripts') == tree_hashes(target / 'backend/scripts')
    run('chown', '-hR', 'bacoach:bacoach', str(target))

    # Import-only check: no lifespan, migration, knowledge import or test accounts.
    preflight = '''from dotenv import load_dotenv
for file in ['/etc/bacoach/backend.env', '/etc/bacoach/pa-push.env', '/etc/bacoach/workbench-safety.env']:
    load_dotenv(file, override=True)
# systemd reads root-protected EnvironmentFiles before dropping to User=bacoach.
# Reproduce that order without loosening file permissions or printing secrets.
import os, pwd
account = pwd.getpwnam('bacoach')
os.initgroups(account.pw_name, account.pw_gid)
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
from app.config import get_settings
from app.schemas import Message
from app.generation_policy import main_thinking_options
from app.retrieval_intent import decide_retrieval
from app.main import app
s = get_settings()
assert s.database_schema_version == 'v2' and not s.startup_db_maintenance
assert s.chat_fast_ack_enabled and s.risk_gate_enabled and s.answer_validator_enabled
assert s.deepseek_reasoning_effort == 'provider_default'
assert s.module_router_reasoning_effort == 'provider_default'
for name in ['deepseek', 'doubao']:
    assert main_thinking_options(s, name, [Message(role='user',content='好的')])['thinking']['type'] == 'disabled'
    assert main_thinking_options(s, name, [Message(role='user',content='好的，但我不想活了')])['thinking']['type'] == 'enabled'
assert not decide_retrieval({'user_input':'好的', 'chat_history':[{'role':'assistant','content':'你愿意按这个安排试试吗？'}]}).retrieve
assert decide_retrieval({'user_input':'好的', 'chat_history':[{'role':'assistant','content':'你愿意听我解释行为激活的原理吗？'}]}).retrieve
print('PREFLIGHT_OK: full safety gates; router unchanged; no DB maintenance')
'''
    run(str(target / 'backend/.venv/bin/python'), '-c', preflight,
        cwd=target / 'backend')
    assert CURRENT.resolve(strict=True) == previous
    for name, expected in BASE_HASHES.items():
        assert digest(previous / name) == expected
    worker_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'bacoach-pa-push']).returncode == 0
    switched = False
    worker_stopped = False
    try:
        if worker_active:
            run('systemctl', 'stop', 'bacoach-pa-push')
            worker_stopped = True
        switch_to(target, release_id)
        switched = True
        run('systemctl', 'restart', 'bacoach-backend')
        wait_health()
        if worker_active:
            run('systemctl', 'start', 'bacoach-pa-push')
        run('systemctl', 'is-active', '--quiet', 'bacoach-backend', 'bacoach-frontend')
        if worker_active:
            run('systemctl', 'is-active', '--quiet', 'bacoach-pa-push')
        run('curl', '-fsS', '--max-time', '20', '-o', '/dev/null', 'https://bacoach.xyz/')
        auth_code = run('curl', '-sS', '--max-time', '20', '-o', '/dev/null', '-w', '%{http_code}',
                        'https://bacoach.xyz/api/conversations', capture_output=True, text=True).stdout
        assert auth_code == '401', auth_code
    except BaseException:
        if switched:
            switch_to(previous, release_id)
            run('systemctl', 'restart', 'bacoach-backend')
            wait_health()
        if worker_stopped:
            run('systemctl', 'restart', 'bacoach-pa-push')
        print('ROLLBACK_TO_PREVIOUS', previous, flush=True)
        raise
    print(json.dumps({'deployed': str(target), 'previous': str(previous),
        'changed_source_files': sorted(ALLOWED), 'frontend_unchanged': True,
        'database_tasks': False, 'auth_status': auth_code,
        'sha256': {name: digest(target / name) for name in sorted(ALLOWED)}}), flush=True)


if __name__ == '__main__':
    main()
