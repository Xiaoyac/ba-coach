"""Allow-listed code-only release; no database or knowledge import."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

FILES = ('backend/app/knowledge_references.py',
         'frontend/components/KnowledgeReferenceDetails.tsx',
         'frontend/components/ReasoningDetails.tsx', 'frontend/lib/conversations.ts')
PREVIOUS = '/opt/bacoach/releases/20260919T133000Z'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def server(release, archive_path):
    root = Path('/opt/bacoach')
    previous = Path(PREVIOUS)
    current = root / 'current'
    target = root / 'releases' / release
    assert current.resolve() == previous and not target.exists()
    assert release.isalnum() and shutil.disk_usage(root).free > 2_000_000_000
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        assert {m.name for m in members} == set(FILES) | {'manifest.json'}
        assert len(members) == len(FILES) + 1
        assert all(m.isfile() and m.size < 1_000_000 for m in members)
        payload = {m.name: archive.extractfile(m).read() for m in members}
    manifest = json.loads(payload.pop('manifest.json'))
    for name in FILES:
        assert digest(previous / name) == manifest['old'][name], name
        assert hashlib.sha256(payload[name]).hexdigest() == manifest['new'][name]
    target.mkdir(mode=0o750)
    shutil.copytree(previous / 'backend', target / 'backend', symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # Dereference only the frontend root; never overlay files through its old release symlink.
    shutil.copytree((previous / 'frontend').resolve(), target / 'frontend', symlinks=True,
                    ignore=shutil.ignore_patterns('node_modules', '.next'))
    for name, content in payload.items():
        (target / name).write_bytes(content)
    assert not (target / 'backend/.env').exists()
    assert not (target / 'backend/psychology.db').exists()
    run('chown', '-hR', 'bacoach:bacoach', str(target))
    run('runuser', '-u', 'bacoach', '--', 'npm', 'ci', '--include=dev', '--no-audit', '--no-fund',
        cwd=target / 'frontend')
    run(str(target / 'backend/.venv/bin/python'), '-c',
        'from app.knowledge_references import reference_snapshot, KnowledgeReferences; '
        'assert KnowledgeReferences(mediator_guidance="test").mediator_guidance == "test"; print("IMPORT_OK")',
        cwd=target / 'backend', env={**os.environ, 'PYTHONPATH': str(target / 'backend'), 'PYTHONDONTWRITEBYTECODE': '1'})
    run('runuser', '-u', 'bacoach', '--', 'env', 'NEXT_TELEMETRY_DISABLED=1', 'npm', 'run', 'build',
        cwd=target / 'frontend')
    for name in FILES:
        assert digest(previous / name) == manifest['old'][name]
        assert digest(target / name) == manifest['new'][name]
    assert current.resolve() == previous
    def switch(destination):
        link = root / ('.mediator-switch-' + release)
        assert not link.exists()
        link.symlink_to(destination)
        os.replace(link, current)
    def healthy(url):
        for _ in range(30):
            if subprocess.run(['curl', '-fsS', '--max-time', '2', '-o', '/dev/null', url],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError('Unhealthy: ' + url)
    worker = subprocess.run(['systemctl', 'is-active', '--quiet', 'bacoach-pa-push']).returncode == 0
    try:
        if worker:
            run('systemctl', 'stop', 'bacoach-pa-push')
        switch(target)
        run('systemctl', 'restart', 'bacoach-backend', 'bacoach-frontend')
        healthy('http://127.0.0.1:8000/health')
        healthy('http://127.0.0.1:3000/')
        healthy('https://bacoach.xyz/')
        if worker:
            run('systemctl', 'start', 'bacoach-pa-push')
        run('systemctl', 'is-active', '--quiet', 'bacoach-backend', 'bacoach-frontend')
    except BaseException:
        switch(previous)
        run('systemctl', 'restart', 'bacoach-backend', 'bacoach-frontend')
        if worker:
            run('systemctl', 'restart', 'bacoach-pa-push')
        raise
    print(json.dumps({'deployed': str(target), 'previous': str(previous),
                      'changed': FILES, 'database_tasks': False}), flush=True)


def local():
    root = Path(__file__).resolve().parents[2]
    work = root / 'work/mediator-guidance-release-0919'
    manifest = {'old': {}, 'new': {}}
    for name in FILES:
        manifest['old'][name] = digest(work / 'baseline' / name)
        manifest['new'][name] = digest(root / name)
    (work / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    archive = work / 'patch.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        for name in FILES:
            package.add(root / name, arcname=name, recursive=False)
        package.add(work / 'manifest.json', arcname='manifest.json')
    release = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    key = str(Path(os.environ['USERPROFILE']) / '.ssh/bacoach_deploy_20260907_ed25519')
    host = 'root@8.134.178.40'
    remote = '/tmp/mediator-guidance-' + release
    run('scp', '-i', key, '-o', 'BatchMode=yes', str(archive), host + ':' + remote + '.tar.gz')
    run('scp', '-i', key, '-o', 'BatchMode=yes', __file__, host + ':' + remote + '.py')
    run('ssh', '-i', key, '-o', 'BatchMode=yes', host,
        f'python3 {remote}.py --server {release} {remote}.tar.gz')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--server':
        server(*sys.argv[2:])
    else:
        local()
