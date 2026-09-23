"""Guarded code-only release: prepare/preflight, then explicit activate.

Never runs migrations, imports knowledge, or copies a local database.
Usage: python3 SCRIPT prepare|activate RELEASE_ID ARCHIVE VERIFIER
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
ALLOWED = {
    'backend/app/config.py', 'backend/app/knowledge_mediator.py',
    'backend/app/providers/deepseek.py', 'backend/app/providers/doubao.py',
    'backend/app/providers/claude.py', 'backend/app/providers/deadline.py',
    'backend/app/graph/nodes.py', 'backend/app/routes/chat.py',
    'backend/app/routes/conversations.py', 'backend/requirements.txt',
}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def tree(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def switch(path, release_id):
    link = ROOT / ('.latency-switch-' + release_id)
    assert not link.exists() and not link.is_symlink()
    link.symlink_to(path, target_is_directory=True)
    os.replace(link, CURRENT)


def health():
    for _ in range(30):
        result = subprocess.run(['curl', '-fsS', '--max-time', '2', '-o', '/dev/null',
                                 'http://127.0.0.1:8000/health'], capture_output=True)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError('Backend health failed')


def verify(target, script, live=False):
    # Mirror systemd: read protected env files BEFORE dropping privileges.
    runner = '''import os,pwd,runpy,sys
from dotenv import load_dotenv
for p in ['/etc/bacoach/backend.env','/etc/bacoach/pa-push.env','/etc/bacoach/workbench-safety.env']:
    load_dotenv(p,override=True)
account=pwd.getpwnam('bacoach')
os.initgroups(account.pw_name,account.pw_gid)
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
from app.config import get_settings
s=get_settings()
assert s.database_schema_version=='v2' and not s.startup_db_maintenance
assert s.risk_gate_enabled and s.answer_validator_enabled and s.knowledge_mediator_enabled
assert s.knowledge_mediator_include_reasoning is False
assert s.provider_request_timeout_seconds==60 and s.router_request_timeout_seconds==12
assert s.background_model_timeout_seconds==30 and s.provider_max_retries==0
assert s.routing_wait_timeout_seconds==2
assert s.knowledge_result_cache_ttl_seconds==300
from app.main import app
print('PREFLIGHT_CONFIG_OK: no lifespan, database maintenance, or data import',flush=True)
runpy.run_path(sys.argv[1],run_name='__main__')
'''
    run(str(target / 'backend/.venv/bin/python'), '-c', runner, script,
        *(['--live'] if live else []), cwd=target / 'backend',
        env={**os.environ, 'PYTHONPATH':str(target / 'backend'), 'PYTHONDONTWRITEBYTECODE':'1'})


def main():
    mode, release_id, archive_path, verifier = sys.argv[1:]
    assert mode in ('prepare','activate') and re.fullmatch(r'\d{8}T\d{6}Z',release_id)
    with tarfile.open(archive_path, 'r:gz') as archive:
        members=archive.getmembers()
        assert len(members)==len(ALLOWED)+1
        assert {m.name for m in members}==ALLOWED|{'manifest.json'}
        assert all(m.isfile() and m.size<1000000 for m in members)
        payload={m.name:archive.extractfile(m).read() for m in members}
    manifest=json.loads(payload.pop('manifest.json'))
    assert set(manifest['old'])==set(manifest['new'])==ALLOWED
    previous=Path(manifest['previous'])
    assert previous==ROOT/'releases/20260918T121211Z'
    assert CURRENT.resolve(strict=True)==previous
    for name in ALLOWED:
        assert digest(previous/name)==manifest['old'][name], 'Live source changed: '+name
        assert hashlib.sha256(payload[name]).hexdigest()==manifest['new'][name]
        if name.endswith('.py'):
            compile(payload[name],name,'exec')
    target=ROOT/'releases'/release_id
    if mode=='prepare':
        assert not target.exists() and shutil.disk_usage(ROOT).free>1024**3
        target.mkdir(mode=0o750)
        (target/'backend').mkdir()
        for name in ('app','scripts'):
            shutil.copytree(previous/'backend'/name,target/'backend'/name,
                ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copy2(previous/'backend/requirements.txt',target/'backend/requirements.txt')
        (target/'backend/.venv').symlink_to((previous/'backend/.venv').resolve(),target_is_directory=True)
        (target/'frontend').symlink_to((previous/'frontend').resolve(),target_is_directory=True)
        for name,data in payload.items():
            (target/name).write_bytes(data)
        run('chown','-hR','bacoach:bacoach',str(target))
    assert target.is_dir() and target.parent==ROOT/'releases'
    for name in ALLOWED:
        assert digest(target/name)==manifest['new'][name], 'Candidate changed: '+name
    old_tree,new_tree=tree(previous/'backend/app'),tree(target/'backend/app')
    changed={'backend/app/'+p for p in old_tree.keys()|new_tree.keys() if old_tree.get(p)!=new_tree.get(p)}
    assert changed==ALLOWED-{'backend/requirements.txt'}
    assert tree(previous/'backend/scripts')==tree(target/'backend/scripts')
    assert not (target/'backend/.env').exists() and not (target/'backend/psychology.db').exists()
    assert (target/'frontend').resolve()==(previous/'frontend').resolve()
    verify(target,verifier,live=(mode=='prepare'))
    if mode=='prepare':
        print(json.dumps({'prepared':str(target),'previous':str(previous),'changed':sorted(ALLOWED),'database_tasks':False}),flush=True)
        return
    assert CURRENT.resolve(strict=True)==previous
    worker=subprocess.run(['systemctl','is-active','--quiet','bacoach-pa-push']).returncode==0
    switched=False
    try:
        if worker:
            run('systemctl','stop','bacoach-pa-push')
        switch(target,release_id)
        switched=True
        run('systemctl','restart','bacoach-backend')
        health()
        if worker:
            run('systemctl','start','bacoach-pa-push')
        run('systemctl','is-active','--quiet','bacoach-backend','bacoach-frontend')
        if worker:
            run('systemctl','is-active','--quiet','bacoach-pa-push')
        for url,wanted in [('https://bacoach.xyz/','200'),('https://bacoach.xyz/api/conversations','401')]:
            code=run('curl','-sS','--max-time','20','-o','/dev/null','-w','%{http_code}',url,capture_output=True,text=True).stdout
            assert code==wanted,(url,code)
    except BaseException:
        if switched:
            switch(previous,release_id)
            run('systemctl','restart','bacoach-backend')
            health()
        if worker:
            run('systemctl','restart','bacoach-pa-push')
        print('ROLLED_BACK',str(previous),flush=True)
        raise
    print(json.dumps({'deployed':str(target),'previous':str(previous),'frontend_unchanged':True,
        'database_tasks':False,'source_sha256':manifest['new']}),flush=True)


if __name__=='__main__':
    main()
