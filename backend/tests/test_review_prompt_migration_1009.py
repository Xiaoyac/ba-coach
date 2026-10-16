import importlib.util
from pathlib import Path
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models import PromptOverride

spec = importlib.util.spec_from_file_location('prompt_patch', Path(__file__).parents[1] / 'scripts/align_review_prompts_1009.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


@pytest.fixture
async def prompts(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as conn:
        await conn.run_sync(PromptOverride.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(patch, 'get_sessionmaker', lambda: maker)
    async with maker() as db:
        db.add_all([PromptOverride(prompt_key=k, content=f'{k} original admin text', updated_by='admin-original') for k in ['module_3', 'module_4']])
        await db.commit()
    from app.prompt_history import initialize_history
    await initialize_history(engine)
    yield maker
    await engine.dispose()


async def values(maker):
    async with maker() as db:
        return {r.prompt_key: (r.content, r.updated_by) for r in (await db.execute(select(PromptOverride))).scalars()}


def pack():
    return {k: {'before_sha256': patch.digest(f'{k} original admin text'), 'content': f'{k} revised'} for k in ['module_3', 'module_4']}


async def test_dry_run_and_conditional_restore(prompts, tmp_path):
    before = await values(prompts)
    report = await patch.apply_pack(pack())
    assert not any(r['applied'] for r in report)
    assert await values(prompts) == before
    backup = tmp_path / 'backup.json'
    await patch.apply_pack(pack(), apply=True, backup=backup)
    assert (await values(prompts))['module_4'] == ('module_4 revised', 'system:requirements-1009')
    await patch.apply_pack(json.loads(backup.read_text()), apply=True, backup=tmp_path / 'rollback-backup.json')
    assert await values(prompts) == before


async def test_concurrent_admin_edit_aborts_all_changes(prompts, tmp_path):
    change = pack()
    change['module_4']['before_sha256'] = 'stale'
    before = await values(prompts)
    with pytest.raises(ValueError, match='changed'):
        await patch.apply_pack(change, apply=True, backup=tmp_path / 'unused.json')
    assert await values(prompts) == before
    assert not (tmp_path / 'unused.json').exists()


async def test_rollback_does_not_overwrite_newer_admin_edit(prompts, tmp_path):
    backup = tmp_path / 'backup.json'
    await patch.apply_pack(pack(), apply=True, backup=backup)
    async with prompts() as db:
        row = (await db.execute(select(PromptOverride).where(PromptOverride.prompt_key == 'module_4'))).scalar_one()
        row.content = 'new edit after deployment'
        await db.commit()
    before = await values(prompts)
    with pytest.raises(ValueError, match='changed'):
        await patch.apply_pack(json.loads(backup.read_text()), apply=True, backup=tmp_path / 'rollback.json')
    assert await values(prompts) == before
