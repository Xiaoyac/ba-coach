import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

spec = importlib.util.spec_from_file_location('daily_history_migration', Path(__file__).parents[1] / 'scripts/migrate_daily_history_0928.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


async def test_dry_run_apply_and_repeat_preserve_existing_row(tmp_path):
    path = str(tmp_path / 'old.db')
    url = 'sqlite+aiosqlite:///' + path
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text('CREATE TABLE assessment_entries (id INTEGER PRIMARY KEY, subject_id TEXT, recorded_on DATE, scale_version INTEGER)'))
        await conn.execute(text("INSERT INTO assessment_entries VALUES (1, 'existing', '2020-01-02', 1)"))
    await script.migrate(url, expected_database=path)
    async with engine.connect() as conn:
        assert not await conn.run_sync(lambda c: inspect(c).has_table('assessment_revisions'))
    with pytest.raises(RuntimeError):
        await script.migrate(url, expected_database='wrong', apply=True)
    await script.migrate(url, expected_database=path, apply=True)
    await script.migrate(url, expected_database=path, apply=True)
    async with engine.connect() as conn:
        row = (await conn.execute(text('SELECT * FROM assessment_entries'))).one()
        assert tuple(row) == (1, 'existing', '2020-01-02', 1, 1)
        assert await conn.scalar(text('SELECT COUNT(*) FROM assessment_revisions')) == 0
    await engine.dispose()
