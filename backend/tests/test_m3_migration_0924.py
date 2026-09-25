import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import JSON, String, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


spec = importlib.util.spec_from_file_location("m3_migration", Path(__file__).parents[1] / "scripts/migrate_m3_recording_0924.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def old_columns():
    return {name:{} for name in ("id", "acceptance_status", "negotiated_record_plan", "feedback_mechanism")}


def test_additive_plan_does_not_infer_acceptance_from_old_attitude():
    plan = script.migration_plan(old_columns())
    assert len(plan) == 2 and "DEFAULT 'unknown'" in plan[0]
    assert all(word not in " ".join(plan) for word in ("UPDATE ", "DROP ", "DELETE ", "INSERT "))


def test_completed_and_partial_migration_are_idempotent():
    columns = old_columns()
    columns["recording_status"] = {"type":String(24), "nullable":False}
    assert len(script.migration_plan(columns)) == 1
    columns["recording_evidence"] = {"type":JSON(), "nullable":True}
    assert script.migration_plan(columns) == []


async def test_dry_run_and_apply_preserve_existing_user_rows(tmp_path):
    database = str(tmp_path / "m3.sqlite")
    url = "sqlite+aiosqlite:///" + database
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE module_three_record (id TEXT PRIMARY KEY, acceptance_status TEXT, negotiated_record_plan JSON, feedback_mechanism TEXT)"))
        await connection.execute(text("INSERT INTO module_three_record VALUES ('old','confirmed',NULL,'聊天反馈')"))
    await script.migrate(url, expected_database=database)
    async with engine.begin() as connection:
        columns = await connection.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("module_three_record")})
        assert "recording_status" not in columns
    await script.migrate(url, expected_database=database, apply=True)
    assert await script.migrate(url, expected_database=database, apply=True) == []
    async with engine.begin() as connection:
        row = (await connection.execute(text("SELECT * FROM module_three_record"))).mappings().one()
        assert row["acceptance_status"] == "confirmed" and row["recording_status"] == "unknown"
        assert row["negotiated_record_plan"] is None and row["recording_evidence"] is None
    await engine.dispose()


async def test_wrong_database_name_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="explicit expected target"):
        await script.migrate("sqlite+aiosqlite:///" + str(tmp_path / "m3.sqlite"), expected_database="other")
