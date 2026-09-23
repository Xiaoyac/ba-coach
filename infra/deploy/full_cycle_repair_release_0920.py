"""Five-file, code-only cycle repair; reuse the guarded release activator."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile

PREVIOUS = '/opt/bacoach/releases/20260919T130000Z'
FILES = {'backend/app/answer_validator.py', 'backend/app/dialogue_confirmation.py',
         'backend/app/goal_contract.py', 'backend/app/reasoning.py', 'backend/app/v2_workflow.py'}


def build(revision=''):
    root = Path(__file__).resolve().parents[2]
    baseline = root / 'work/full-cycle-acceptance-0920/release/baseline'
    work = root / ('work/full-cycle-acceptance-0920/release' + revision)
    candidate = work / 'candidate'
    work.mkdir(parents=True, exist_ok=True)
    assert not candidate.exists()
    shutil.copytree(baseline / 'backend', candidate / 'backend',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for folder in ('tests', 'evals', 'scripts'):
        shutil.copytree(root / 'backend' / folder, candidate / 'backend' / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(root / 'backend/pytest.ini', candidate / 'backend/pytest.ini')
    manifest = {'previous': PREVIOUS, 'old': {}, 'new': {}}
    for name in sorted(FILES):
        old, new = (baseline / name).read_bytes(), (root / name).read_bytes()
        assert old != new, name
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
    if sys.argv[1] in {'build', 'build-r2'}:
        build('-r2' if sys.argv[1] == 'build-r2' else '')
    elif sys.argv[1] == 'rollback':
        import deploy_consent_reply_0919 as runner
        current = Path('/opt/bacoach/current')
        expected = sys.argv[2] if len(sys.argv) > 2 else '20260920T030000Z'
        assert expected in {'20260920T030000Z', '20260920T031500Z'}
        assert current.resolve() == Path('/opt/bacoach/releases') / expected
        runner.switch(Path(PREVIOUS), 'rollback0920')
        runner.run('systemctl', 'restart', 'bacoach-backend')
        runner.health()
        runner.run('systemctl', 'restart', 'bacoach-pa-push')
        print('ROLLED_BACK_TO', PREVIOUS, flush=True)
    else:
        import deploy_consent_reply_0919 as runner
        runner.PREVIOUS = Path(PREVIOUS)
        runner.ALLOWED = FILES
        runner.main()
