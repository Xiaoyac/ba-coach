import pytest
from sqlalchemy import insert, select, inspect
from sqlalchemy.ext.asyncio import create_async_engine
from app.database_v2_schema import metadata as schema
from app.models import Conversation, ConversationMessage
from app.goal_contract import capture_activities, evidence_messages, public_activities
from app.v2_workflow import runtime_for
from app.v2_repository import create_goal, start_cycle
from app.v2_deletion import detach_conversation
from scripts.migrate_goal_details_0914 import migrate, TABLE_NAMES


@pytest.mark.asyncio
async def test_mysql_migration_matches_parent_encoding_without_mutating_app_schema():
    from sqlalchemy import MetaData
    from unittest.mock import AsyncMock, MagicMock
    from scripts.migrate_goal_details_0914 import align_mysql_foreign_keys
    copied = MetaData()
    for table in schema.sorted_tables:
        table.to_metadata(copied)
    result = MagicMock()
    result.mappings.return_value.one.return_value = {
        'DATA_TYPE': 'varchar', 'CHARACTER_MAXIMUM_LENGTH': 36,
        'CHARACTER_SET_NAME': 'utf8mb4', 'COLLATION_NAME': 'utf8mb4_unicode_ci'}
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=result)
    target = copied.tables['pa_goal_details']
    await align_mysql_foreign_keys(connection, target)
    assert target.c.goal_id.type.collation == 'utf8mb4_unicode_ci'
    assert schema.tables['pa_goal_details'].c.goal_id.type.collation is None
    result.mappings.return_value.one.return_value['CHARACTER_MAXIMUM_LENGTH'] = 64
    with pytest.raises(RuntimeError, match='Unexpected parent type'):
        await align_mysql_foreign_keys(connection, target)
from test_goal_overview import goal_api
from test_conversation_forgetting import deletion_db


@pytest.mark.asyncio
async def test_correction_preserves_history_but_removes_old_fact_from_context(goal_api):
    _, db, _ = goal_api
    await db.execute(insert(ConversationMessage), [
        {'id': 10, 'conversation_id': 1, 'position': 0, 'role': 'user', 'content': '昨天散步了'},
        {'id': 11, 'conversation_id': 1, 'position': 1, 'role': 'assistant', 'content': '谢谢反馈'}])
    conv, state = await runtime_for(db, 'chat-a')
    await capture_activities(db, user_id='a', conversation=conv, state=state,
        raw=[{'activity_content': '散步', 'event_kind': 'performed', 'source_quote': '昨天散步了'}],
        messages=await evidence_messages(db, 1, 'a'))
    await db.execute(insert(ConversationMessage), [
        {'id': 12, 'conversation_id': 1, 'position': 2, 'role': 'user', 'content': '刚才记错了，昨天没散步'},
        {'id': 13, 'conversation_id': 1, 'position': 3, 'role': 'assistant', 'content': '已了解更正'}])
    for _ in range(2):
        await capture_activities(db, user_id='a', conversation=conv, state=state,
            raw=[{'activity_content': '散步', 'event_kind': 'not_performed', 'source_quote': '刚才记错了，昨天没散步'}],
            corrections=[{'prior_quote': '昨天散步了', 'source_quote': '刚才记错了，昨天没散步'}],
            messages=await evidence_messages(db, 1, 'a'))
    events = (await db.execute(select(schema.tables['pa_activity_events']))).mappings().all()
    assert len(events) == 2
    assert next(e for e in events if e['event_kind'] == 'performed')['status'] == 'superseded'
    public = await public_activities(db, 'a', conversation_id=1)
    assert len(public) == 1 and public[0]['event_kind'] == 'not_performed'
    assert await public_activities(db, 'b', conversation_id=1) == []


@pytest.mark.asyncio
async def test_delete_conversation_cleans_all_new_dependent_tables(deletion_db):
    db, _ = deletion_db
    goal = await create_goal(db, user_id='a', title='站桩', conversation_id=1)
    cycle = await start_cycle(db, user_id='a', goal_id=goal, conversation_id=1)
    await db.execute(insert(schema.tables['module_two_record']), {'id': 'p', 'goal_id': goal, 'version_no': 1, 'timezone': 'Asia/Shanghai'})
    await db.execute(insert(schema.tables['module_four_record']), {'id': 'r', 'cycle_id': cycle})
    await db.execute(insert(schema.tables['pa_goal_details']), {'goal_id': goal, 'goal_kind': 'secondary', 'source_conversation_id': 1, 'source_message_id': 1})
    await db.execute(insert(schema.tables['pa_plan_details']), {'plan_id': 'p', 'schedule_kind': 'recurring'})
    await db.execute(insert(schema.tables['pa_review_details']), {'review_id': 'r', 'action': 'pause', 'source_message_id': 1, 'source_quote': '暂停'})
    await db.execute(insert(schema.tables['pa_activity_events']), {'id': 'e', 'user_id': 'a', 'goal_id': goal,
        'cycle_id': cycle, 'source_conversation_id': 1, 'source_message_id': 1, 'event_index': 1,
        'event_kind': 'performed', 'activity_content': '站桩', 'source_quote': '站桩了'})
    await db.commit()
    await detach_conversation(db, await db.get(Conversation, 1))
    await db.commit()
    for name in TABLE_NAMES:
        assert not (await db.execute(select(schema.tables[name]))).first(), name


@pytest.mark.asyncio
async def test_migration_is_read_only_by_default_and_additive_idempotent(tmp_path):
    path = (tmp_path / 'isolated.db').as_posix()
    url = 'sqlite+aiosqlite:///' + path
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: schema.create_all(c, tables=[t for t in schema.sorted_tables if t.name not in TABLE_NAMES]))
        await connection.execute(insert(schema.tables['user_profile']), {'uuid': 'existing-user'})
    await migrate(url, expected_database=path)
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda c: inspect(c).get_table_names())
        assert not set(TABLE_NAMES) & set(names)
    with pytest.raises(RuntimeError, match='does not match'):
        await migrate(url, expected_database='wrong-db', apply=True)
    await migrate(url, expected_database=path, apply=True)
    await migrate(url, expected_database=path, apply=True)
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda c: inspect(c).get_table_names())
        assert set(TABLE_NAMES) <= set(names)
        assert (await connection.execute(select(schema.tables['user_profile'].c.uuid))).scalars().all() == ['existing-user']
        for name in TABLE_NAMES:
            assert not (await connection.execute(select(schema.tables[name]))).first()
    await engine.dispose()
