"""Synthetic clocks and DBs only; no providers, live users or notifications."""
import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, insert, select, update

from app.chat_reminder_schema import metadata, reminders
from app.conversation_store import load_reply_history, start_turn
from app.database_v2_schema import metadata as schema
from app.models import AuthSession, Conversation, ConversationMessage
from app.pa_chat_reminders import deliver, tick
from app.pa_push import candidates
from app.push_schema import devices
from test_pa_push import AT, ANCHOR, setup as push_setup


@pytest_asyncio.fixture
async def setup(push_setup):
    async with push_setup.kw['bind'].begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with push_setup() as db:
        await db.execute(insert(ConversationMessage), [
            dict(id=3, conversation_id=1, position=2, role='user', content='好的，按这个计划', created_at=ANCHOR+timedelta(minutes=1)),
            dict(id=4, conversation_id=1, position=3, role='assistant', content='好的，活动后可以记录。', created_at=ANCHOR+timedelta(minutes=1)),
        ])
        await db.execute(update(schema.tables['module_two_record']).values(confirmation_message_id=3))
        await db.execute(insert(schema.tables['conversation_runtime_states']), dict(
            conversation_id=1, current_module='module_3', flow_status='waiting_execution',
            active_goal_id='g', active_cycle_id='c', memory={}))
        # No notification permission, no device and no active login are needed.
        await db.execute(delete(devices))
        await db.execute(delete(AuthSession))
        await db.commit()
    return push_setup


async def reminder_rows(factory):
    async with factory() as db:
        return (await db.execute(select(ConversationMessage).where(
            ConversationMessage.finish_reason == 'scheduled_reminder'))).scalars().all()


@pytest.mark.asyncio
async def test_due_once_offline_and_durable_history(setup):
    async with setup() as db:
        before = await db.get(Conversation, 1)
        revision, recency = before.revision, before.updated_at
    assert (await tick(setup, at=AT-timedelta(seconds=1)))['sent'] == 0
    assert (await tick(setup, at=AT))['sent'] == 1
    assert (await tick(setup, at=AT+timedelta(seconds=30)))['duplicate'] == 1
    messages = await reminder_rows(setup)
    assert len(messages) == 1 and messages[0].role == 'assistant'
    assert '09月18日 16:15' in messages[0].content and '散步' in messages[0].content
    assert messages[0].position == 5 and messages[0].model_name is None
    async with setup() as db:
        conversation = await db.get(Conversation, 1)
        assert conversation.revision == revision + 1 and conversation.updated_at == recency
        runtime = (await db.execute(select(schema.tables['conversation_runtime_states']))).mappings().one()
        assert runtime['current_module'] == 'module_3' and runtime['row_version'] == 0
        # The next model really sees the reminder through the durable history.
        user_id = await start_turn(db, subject_id='u', session_id='synthetic', user_text='我现在记录')
        history = await load_reply_history(db, subject_id='u', session_id='synthetic', user_message_id=user_id, limit=80)
        assert history[-1].content == messages[0].content
        user = await db.get(ConversationMessage, user_id)
        assert user.position == 6


@pytest.mark.asyncio
async def test_overlapping_workers_insert_one_message(setup):
    async with setup() as db:
        item, = await candidates(db, 'u', AT)
    outcomes = await asyncio.gather(*(deliver(setup, item, AT) for _ in range(4)))
    assert outcomes.count('sent') == 1 and outcomes.count('duplicate') == 3
    assert len(await reminder_rows(setup)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['no_time', 'no_duration', 'paused_goal', 'replaced_plan',
    'deleted_source', 'assistant_confirmation', 'paused_chat', 'reviewing_chat', 'awaiting_confirmation', 'sandbox', 'different_cycle', 'different_owner'])
async def test_no_unintended_reminder(setup, change):
    async with setup() as db:
        plans, goals, rt = [schema.tables[n] for n in ('module_two_record', 'pa_goals', 'conversation_runtime_states')]
        if change == 'no_time': await db.execute(update(plans).values(schedule_text='午饭后'))
        if change == 'no_duration': await db.execute(update(plans).values(duration_minutes=None))
        if change == 'paused_goal': await db.execute(update(goals).values(status='paused'))
        if change == 'replaced_plan': await db.execute(update(goals).values(current_plan_record_id=None))
        if change == 'deleted_source': await db.execute(delete(ConversationMessage).where(ConversationMessage.id == 1))
        if change == 'assistant_confirmation': await db.execute(update(plans).values(confirmation_message_id=2))
        if change == 'paused_chat': await db.execute(update(rt).values(flow_status='paused'))
        if change == 'reviewing_chat': await db.execute(update(rt).values(flow_status='active', current_module='module_4'))
        if change == 'awaiting_confirmation': await db.execute(update(rt).values(last_transition_reason='awaiting_record_confirmation'))
        if change == 'sandbox': await db.execute(update(rt).values(memory={'sandbox_mode': 'true'}))
        if change == 'different_cycle': await db.execute(update(rt).values(active_cycle_id=None))
        if change == 'different_owner': await db.execute(update(Conversation).values(subject_id='other'))
        await db.commit()
    assert (await tick(setup, at=AT))['sent'] == 0
    assert not await reminder_rows(setup)


@pytest.mark.asyncio
async def test_recheck_plan_changed_since_scan(setup):
    async with setup() as db:
        item, = await candidates(db, 'u', AT)
        await db.execute(update(schema.tables['module_two_record']).values(duration_minutes=30))
        await db.commit()
    assert await deliver(setup, item, AT) == 'skipped'
    assert not await reminder_rows(setup)


@pytest.mark.asyncio
async def test_completed_activity_is_suppressed(setup):
    async with setup() as db:
        await db.execute(insert(ConversationMessage), dict(conversation_id=1, position=4,
            role='user', content='我已经做完了', created_at=AT-timedelta(minutes=3)))
        await db.commit()
    assert (await tick(setup, at=AT))['suppressed'] == 1
    assert (await tick(setup, at=AT+timedelta(minutes=1)))['duplicate'] == 1
    assert not await reminder_rows(setup)


@pytest.mark.asyncio
@pytest.mark.parametrize('preference', [dict(reminder_frequency='暂时不需要提醒'),
    dict(reminder_frequency='仅在我主动找你时提醒'), dict(reminder_time_slot='早晨7-9')])
async def test_preferences(setup, preference):
    async with setup() as db:
        await db.execute(insert(schema.tables['user_preferences']), dict(user_id='u', **preference))
        await db.commit()
    assert (await tick(setup, at=AT))['sent'] == 0


@pytest.mark.asyncio
async def test_busy_turn_deferred_then_delivered_without_slot_collision(setup):
    async with setup() as db:
        # A long-running turn remains protected even after the two-minute guard.
        await db.execute(insert(ConversationMessage), dict(id=5, conversation_id=1, position=4,
            role='user', content='我有一个问题', created_at=AT-timedelta(minutes=3)))
        await db.commit()
    assert (await tick(setup, at=AT))['deferred'] == 1
    async with setup() as db:
        await db.execute(insert(ConversationMessage), dict(conversation_id=1, position=5,
            role='assistant', content='请说。', created_at=AT))
        await db.commit()
    assert (await tick(setup, at=AT+timedelta(seconds=30)))['deferred'] == 1
    assert (await tick(setup, at=AT+timedelta(minutes=2)))['sent'] == 1
    assert (await reminder_rows(setup))[0].position == 7


@pytest.mark.asyncio
async def test_expired_occurrence_not_replayed(setup):
    assert (await tick(setup, at=AT+timedelta(minutes=30)))['sent'] == 0
    assert not await reminder_rows(setup)


@pytest.mark.asyncio
async def test_daily_occurrences_and_frequency_limit(setup):
    async with setup() as db:
        await db.execute(update(ConversationMessage).where(ConversationMessage.id == 1)
                         .values(content='每天16:00散步15分钟'))
        await db.execute(update(schema.tables['module_two_record']).values(schedule_text='每天16:00散步15分钟'))
        await db.execute(insert(schema.tables['user_preferences']), dict(user_id='u', reminder_frequency='隔天一次'))
        await db.commit()
    assert (await tick(setup, at=AT))['sent'] == 1
    assert (await tick(setup, at=AT+timedelta(days=1)))['sent'] == 0
    assert (await tick(setup, at=AT+timedelta(days=2)))['sent'] == 1
    assert len(await reminder_rows(setup)) == 2


@pytest.mark.asyncio
async def test_deleted_conversation_not_recreated_and_receipt_cleaned(setup):
    assert (await tick(setup, at=AT))['sent'] == 1
    async with setup() as db:
        await db.execute(delete(Conversation).where(Conversation.id == 1))
        await db.commit()
    assert (await tick(setup, at=AT+timedelta(seconds=30)))['sent'] == 0
    async with setup() as db:
        assert await db.scalar(select(reminders.c.id)) is None
        assert await db.get(Conversation, 1) is None


@pytest.mark.asyncio
async def test_transaction_failure_rolls_back_receipt_and_message(setup, monkeypatch):
    from app import pa_chat_reminders
    def fail(_):
        raise RuntimeError('synthetic failure after receipt insertion')
    original = pa_chat_reminders.reminder_text
    monkeypatch.setattr(pa_chat_reminders, 'reminder_text', fail)
    assert (await tick(setup, at=AT))['failed'] == 1
    async with setup() as db:
        assert await db.scalar(select(reminders.c.id)) is None
    assert not await reminder_rows(setup)
    monkeypatch.setattr(pa_chat_reminders, 'reminder_text', original)
    assert (await tick(setup, at=AT+timedelta(seconds=30)))['sent'] == 1


@pytest.mark.asyncio
async def test_migration_dry_run_and_idempotent_apply(setup, monkeypatch):
    from sqlalchemy import inspect
    from scripts import migrate_pa_push
    engine = setup.kw['bind']
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    monkeypatch.setattr(migrate_pa_push, 'metadata', metadata)
    await migrate_pa_push.migrate(str(engine.url), engine.url.database)
    async with engine.connect() as conn:
        assert not await conn.run_sync(lambda c: inspect(c).has_table('pa_chat_reminders'))
    await migrate_pa_push.migrate(str(engine.url), engine.url.database, apply=True)
    await migrate_pa_push.migrate(str(engine.url), engine.url.database, apply=True)
    assert (await tick(setup, at=AT))['sent'] == 1


def test_worker_available_without_push_configuration(monkeypatch):
    from types import SimpleNamespace
    from app import pa_chat_reminders
    settings = SimpleNamespace(pa_chat_reminders_enabled=True, database_schema_version='v2')
    monkeypatch.setattr(pa_chat_reminders, 'get_settings', lambda: settings)
    assert pa_chat_reminders.available()
    settings.pa_chat_reminders_enabled = False
    assert not pa_chat_reminders.available()
