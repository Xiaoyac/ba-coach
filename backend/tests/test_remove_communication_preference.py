"""Retired UI preference must not survive in APIs, profile prompts or caches."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import v2_profile
from app.clinical_store import load_profile_context
from app.config import get_settings
from app.context_epochs import stable_state_context
from app.database_v2_schema import metadata
from app.db import Base
from app.models import ConversationContextCheckpoint
from app.models_business import UserProfile
from app.prompts import SystemPromptSegment
from app.schemas import ProfileOut, ProfileUpdate, RegisterRequest
from test_main_prefix_cache import epoch_db  # noqa: F401


def test_public_contracts_and_new_databases_do_not_offer_the_setting():
    for schema in (ProfileOut, ProfileUpdate, RegisterRequest):
        assert "communication_preference" not in schema.model_json_schema()["properties"]
    assert "communication_preference" not in UserProfile.__table__.c
    assert "communication_style" not in metadata.tables["user_preferences"].c


def test_old_client_patch_cannot_restore_the_setting(client, register):
    headers = register(username="retiredstyle", nickname="保留昵称")
    response = client.patch("/api/profile", headers=headers, json={
        "communication_preference": "温柔引导", "nickname": "新昵称",
        "content_taboo": ["不谈工作"]})
    assert response.status_code == 200
    result = client.get("/api/profile", headers=headers).json()
    assert "communication_preference" not in result
    assert result["nickname"] == "新昵称" and result["content_taboo"] == ["不谈工作"]


async def test_legacy_column_is_not_read_into_profile_prompt(db_sessionmaker):
    async with db_sessionmaker() as db:
        await db.execute(text("ALTER TABLE user_profile ADD COLUMN communication_preference TEXT"))
        db.add(UserProfile(uuid="old-style", nickname="测试", physical_condition="易疲劳"))
        await db.flush()
        await db.execute(text("UPDATE user_profile SET communication_preference='轻松幽默'"))
        await db.commit()
    result = "\n".join(await load_profile_context(db_sessionmaker, user_id="old-style"))
    assert "轻松幽默" not in result and "沟通偏好" not in result
    assert "易疲劳" in result


async def test_v2_read_write_and_prompt_ignore_existing_column(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_schema_version", "v2")
    engine = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE user_preferences ADD COLUMN communication_style TEXT"))
        async with maker() as db:
            payload = RegisterRequest(username="retiredv2", password="test-password-long", invitation_code="A" * 32,
                email="retired@example.com", nickname="档案测试", birth_year=1996, research_id="P-v2", phone="13800138000",
                communication_preference="理性分析", physical_condition=["易疲劳"])
            await v2_profile.create(db, "retired-v2", payload)
            assert await db.scalar(text("SELECT communication_style FROM user_preferences")) is None
            await db.execute(text("UPDATE user_preferences SET communication_style='温柔引导'"))
            await db.commit()
            result = await v2_profile.patch(db, "retired-v2", ProfileUpdate(
                communication_preference="直接明了", content_taboo=["不谈工作"],
                reminder_frequency="每周一次"))
            assert "communication_preference" not in result.model_dump()
            assert result.reminder_frequency == "每周一次" and result.content_taboo == ["不谈工作"]
            assert await db.scalar(text("SELECT communication_style FROM user_preferences")) == "温柔引导"
        context = "\n".join(await load_profile_context(maker, user_id="retired-v2"))
        for retired in ("communication_preference", "communication_style", "温柔引导", "直接明了"):
            assert retired not in context
        assert "易疲劳" in context and "不谈工作" in context
    finally:
        await engine.dispose()


async def test_old_frozen_profile_is_rebuilt_without_losing_history(epoch_db):
    db, rows = epoch_db
    checkpoint = ConversationContextCheckpoint(conversation_id=rows[0].conversation_id,
        through_message_id=rows[3].id, source_digest="preserved", summary="旧计划已确认",
        state_baseline={"001:00000": '用户档案：{"communication_preference":"温柔引导"}'})
    db.add(checkpoint)
    await db.commit()
    system = [SystemPromptSegment("规则"), SystemPromptSegment("用户档案：易疲劳", False)]
    result, metrics = await stable_state_context(db, subject_id="owner", session_id="epoch-owned", system=system)
    await db.commit()
    assert metrics["state_baseline_reset"]
    assert "温柔引导" not in "\n".join(s.text for s in result)
    assert checkpoint.summary == "旧计划已确认" and checkpoint.through_message_id == rows[3].id
    again, metrics = await stable_state_context(db, subject_id="owner", session_id="epoch-owned", system=system)
    assert result == again and not metrics["state_baseline_reset"]


def test_column_migration_is_scoped_backed_up_idempotent_and_reversible(tmp_path):
    script = Path(__file__).parents[1] / "scripts/remove_communication_preference_1015.py"
    spec = importlib.util.spec_from_file_location("remove_communication", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    backup = tmp_path / "backup.json"
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE user_profile (uuid TEXT PRIMARY KEY, nickname TEXT, communication_preference TEXT)"))
        conn.execute(text("INSERT INTO user_profile VALUES ('u','保留昵称','温柔引导')"))
        conn.execute(text("CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, reminder_frequency TEXT, communication_style TEXT)"))
        conn.execute(text("INSERT INTO user_preferences VALUES ('u','每周一次','理性分析')"))
        assert not module.migrate(conn)["changed"] and not backup.exists()
        assert module.migrate(conn, apply=True, backup=backup)["changed"]
        assert backup.stat().st_mode & 0o777 == 0o600
        assert conn.execute(text("SELECT * FROM user_profile")).one() == ("u", "保留昵称")
        assert conn.execute(text("SELECT * FROM user_preferences")).one() == ("u", "每周一次")
        assert not module.migrate(conn, apply=True, backup=backup)["changed"]
        assert module.restore(conn, backup)["restored_columns"] == 2
        assert conn.execute(text("SELECT communication_style FROM user_preferences")).scalar_one() == "理性分析"
        with pytest.raises(FileExistsError):
            module.migrate(conn, apply=True, backup=backup)
        assert "communication_preference" in {c["name"] for c in inspect(conn).get_columns("user_profile")}
    engine.dispose()
