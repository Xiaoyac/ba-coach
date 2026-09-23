"""Build and deploy only the five reviewed Router-priority source files."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile

PREVIOUS = '/opt/bacoach/releases/20260919T122054Z'
FILES = {'backend/app/m1_contract.py', 'backend/app/v2_workflow.py',
         'backend/app/router_agent.py', 'backend/app/graph/nodes.py', 'backend/app/prompts.py'}


def build():
    root = Path(__file__).resolve().parents[2]
    work = root / 'work/router-priority-0919'
    baseline, candidate = work / 'baseline', work / 'candidate'
    assert not candidate.exists()
    shutil.copytree(baseline / 'backend', candidate / 'backend', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for folder in ('tests', 'evals'):
        shutil.copytree(root / 'backend' / folder, candidate / 'backend' / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(root / 'backend/pytest.ini', candidate / 'backend/pytest.ini')
    manifest = {'previous': PREVIOUS, 'old': {}, 'new': {}}
    for name in FILES:
        old = (baseline / name).read_bytes()
        assert old == (work / 'before' / name).read_bytes(), 'Unrelated local changes: ' + name
        new = (root / name).read_bytes()
        compile(new, name, 'exec')
        manifest['old'][name] = hashlib.sha256(old).hexdigest()
        manifest['new'][name] = hashlib.sha256(new).hexdigest()
        (candidate / name).write_bytes(new)
    (candidate / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    with tarfile.open(work / 'patch.tar.gz', 'w:gz') as archive:
        for name in sorted(FILES | {'manifest.json'}):
            archive.add(candidate / name, arcname=name, recursive=False)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    if sys.argv[1] == 'build':
        build()
    else:
        import deploy_consent_reply_0919 as runner
        runner.PREVIOUS = Path(PREVIOUS)
        runner.ALLOWED = FILES
        runner.main()
