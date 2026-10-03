"""A durable reply authorizes only its own turn, never new semantic evidence."""
import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.dialogue_confirmation import precommit_user_confirmation
from app.goal_card_workspace import open_card, pause_card
from app.models import ConversationMessage
from app.v2_workflow import module_extraction_is_current, persist_record, runtime_for
from test_goal_overview import goal_api
from test_turn_confirmation_0924 import setup_turn


async def durable_reply(db, *, position=3, role='assistant'):
    await db.execute(insert(ConversationMessage), {'id': 22, 'conversation_id': 1,
        'position': position, 'role': role, 'content': '我听到了，我们继续看你的安排。'})
    await db.commit()


async def test_confirmation_after_own_reply_requires_explicit_boundary(goal_api):
    _, db, _ = goal_api
    await setup_turn(db, '确认，就按这个计划试试。')
    await durable_reply(db)
    assert await precommit_user_confirmation(db, session_id='chat-a', user_id='a', user_message_id=21) is None
    result = await precommit_user_confirmation(db, session_id='chat-a', user_id='a',
        user_message_id=21, following_assistant_message_id=22)
    assert result[0] == 'module_3'
    plan = (await db.execute(select(schema.tables['module_two_record']))).mappings().one()
    assert plan['confirmation_message_id'] == 21


@pytest.mark.parametrize('invalid', ['newer_user', 'gap', 'wrong_role', 'wrong_id'])
async def test_confirmation_rejects_unrelated_or_superseded_reply(goal_api, invalid):
    _, db, _ = goal_api
    await setup_turn(db, '确认，就按这个计划试试。')
    await durable_reply(db, position=4 if invalid == 'gap' else 3,
        role='user' if invalid == 'wrong_role' else 'assistant')
    if invalid == 'newer_user':
        await db.execute(insert(ConversationMessage), {'id': 23, 'conversation_id': 1,
            'position': 4, 'role': 'user', 'content': '等等，我改主意了。'})
        await db.commit()
    assert await precommit_user_confirmation(db, session_id='chat-a', user_id='a',
        user_message_id=21, following_assistant_message_id=999 if invalid == 'wrong_id' else 22) is None
    _, state = await runtime_for(db, 'chat-a')
    assert state['current_module'] == 'module_2'


async def test_post_reply_save_retains_user_source_and_assistant_freshness(goal_api):
    _, db, _ = goal_api
    await setup_turn(db, '地点改到公园。')
    await durable_reply(db)
    record = await persist_record(async_sessionmaker(db.bind, expire_on_commit=False),
        module='module_2', user_id='a', cycle_id='m2-cycle', db_session=db,
        data={'target_activity_location': '公园', '_source_session_id': 'chat-a',
              '_source_user_message_id': 21, '_source_assistant_message_id': 22})
    assert record == 'm2-draft'
    _, state = await runtime_for(db, 'chat-a')
    assert state['memory']['module_extraction_freshness']['module_2'] == {
        'assistant_message_id': 22, 'user_message_id': 21, 'cycle_id': 'm2-cycle'}
    assert await module_extraction_is_current(db, conversation_id=1, state=state,
        module='module_2', assistant_message_id=22)
    await db.execute(insert(ConversationMessage), {'id': 23, 'conversation_id': 1,
        'position': 4, 'role': 'user', 'content': '先不改了。'})
    assert await persist_record(async_sessionmaker(db.bind, expire_on_commit=False),
        module='module_2', user_id='a', cycle_id='m2-cycle', db_session=db,
        data={'target_activity_location': '别处', '_source_session_id': 'chat-a',
              '_source_user_message_id': 21, '_source_assistant_message_id': 22}) is None
    plan = (await db.execute(select(schema.tables['module_two_record']))).mappings().one()
    assert plan['location'] == '公园'


async def test_open_and_pause_card_accept_only_explicit_durable_pair(goal_api):
    _, db, _ = goal_api
    await setup_turn(db, '我愿意试试身体活动。')
    await durable_reply(db)
    conversation, state = await runtime_for(db, 'chat-a')
    with pytest.raises(HTTPException):
        await open_card(db, conversation, state, 'primary', 21)
    card = await open_card(db, conversation, state, 'primary', 21, following_assistant_message_id=22)
    assert card['phase'] == 'formulating'
    result = await pause_card(db, conversation, state, 21, following_assistant_message_id=22)
    assert result['phase'] == 'paused'
    await db.execute(insert(ConversationMessage), {'id': 23, 'conversation_id': 1,
        'position': 4, 'role': 'user', 'content': '新的问题。'})
    with pytest.raises(HTTPException):
        await open_card(db, conversation, state, 'primary', 21, following_assistant_message_id=22)


async def test_m4_save_and_close_readiness_use_owned_reply_boundary(goal_api):
    from app.program_confirmation import draft, record_hash, validate_confirmation
    from app.v2_workflow import record_steps
    from test_pa_native_tools import review_for_tool
    from types import SimpleNamespace
    _, db, _ = goal_api
    raw = await review_for_tool(db)
    await durable_reply(db, position=12)
    raw.update(_source_session_id='chat-a', _source_user_message_id=21, _source_assistant_message_id=22)
    record = await persist_record(async_sessionmaker(db.bind, expire_on_commit=False),
        module='module_4', user_id='a', cycle_id='reviewed-cycle', db_session=db, data=raw,
        tool_closing_summary={'call_id': 'close-1', 'text': '这次散步完成，行动后感到轻松。'})
    assert record == 'review-draft'
    await record_steps(db, session_id='chat-a', user_id='a', module='module_4',
        requested_target='module_4', steps=[], assistant_message_id=22, allow_transition=False)
    conversation, state = await runtime_for(db, 'chat-a')
    pending = await draft(db, state, 'a')
    assert pending['phase_c']['_m4_contract']['assistant_message_id'] == 22
    assert pending['phase_c']['_m4_contract']['evidence']['review_summary_quote']['boundary_message_id'] == 22
    ready, _ = await validate_confirmation(db, conversation=conversation, state=state,
        user_id='a', session_id='chat-a',
        payload=SimpleNamespace(record_id=record, record_hash=record_hash(pending), row_version=state['row_version']),
        extraction_assistant_message_id=22)
    assert ready['id'] == record


async def test_m4_foreground_reply_is_not_new_education_evidence(goal_api):
    from sqlalchemy import delete, update
    from test_pa_native_tools import review_for_tool
    _, db, _ = goal_api
    raw = await review_for_tool(db)
    await db.execute(delete(ConversationMessage).where(ConversationMessage.id == 15))
    await durable_reply(db, position=12)
    await db.execute(update(ConversationMessage).where(ConversationMessage.id == 22).values(
        content=raw['m4_contract']['education_quote']))
    await db.commit()
    raw.update(_source_session_id='chat-a', _source_user_message_id=21, _source_assistant_message_id=22)
    record = await persist_record(async_sessionmaker(db.bind, expire_on_commit=False),
        module='module_4', user_id='a', cycle_id='reviewed-cycle', db_session=db, data=raw,
        tool_closing_summary={'call_id': 'close-1', 'text': '本次完成。'})
    assert record == 'review-draft'
    review = (await db.execute(select(schema.tables['module_four_record']))).mappings().one()
    evidence = review['phase_c']['_m4_contract']['evidence']
    assert 'education_quote' not in evidence
    assert 'review_summary_quote' not in evidence


@pytest.mark.parametrize('superseded', [False, True])
@pytest.mark.parametrize('kind', ['primary', 'secondary'])
async def test_semantic_confirmation_has_no_open_session_or_write_lock(goal_api, superseded, kind, monkeypatch):
    import asyncio
    import json
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.pa_card_tools import PACardTools
    from test_pa_native_tools import call
    client, db, _ = goal_api
    wording = '我看完了所有安排，没有要改的地方，愿意照这一版去执行。'
    card = None
    if kind == 'primary':
        await setup_turn(db, wording)
        await durable_reply(db)
        user_id, assistant_id = 21, 22
    else:
        from app.config import get_settings
        settings = get_settings().model_copy(update={'goal_card_ui_enabled': True, 'database_schema_version': 'v2'})
        monkeypatch.setattr('app.config.get_settings', lambda: settings)
        from test_goal_card_interaction import open_primary, submit_form, review, invoke, persist_display, message
        _, original, card = await open_primary(db, text='我还想额外增加一个散步活动。', kind='secondary')
        _, original, card = await submit_form(client, db, card, {'activity_content': '散步'})
        result = await review(original, card, near_term=None, manageable=None)
        card = result['goal_card']
        result = await invoke(original, 'present_secondary_goal_card', card_id=card['id'], card_revision=card['revision'])
        card = result['goal_card']
        await persist_display(db, result['display_text'])
        user_id = (await message(db, wording)).id
        assistant_id = (await message(db, '我听到了。', 'assistant')).id
    _, state = await runtime_for(db, 'chat-a')
    version = state['row_version']
    await db.rollback()
    opened = 0
    started, release = asyncio.Event(), asyncio.Event()

    class TrackedSession(AsyncSession):
        async def __aenter__(self):
            nonlocal opened
            opened += 1
            return await super().__aenter__()

        async def __aexit__(self, *args):
            nonlocal opened
            try:
                return await super().__aexit__(*args)
            finally:
                opened -= 1

    class Provider:
        calls = 0
        async def route(self, **kwargs):
            self.calls += 1
            # The actual executor session has exited before remote inference.
            assert opened == 0
            started.set()
            await release.wait()
            assert opened == 0
            return json.dumps({'intent': 'confirm', 'source_quote': wording,
                'amendment': False, 'unresolved': False}, ensure_ascii=False)

    provider = Provider()
    tool = PACardTools(maker=async_sessionmaker(db.bind, class_=TrackedSession, expire_on_commit=False),
        session_id='chat-a', user_id='a', user_message_id=user_id, assistant_message_id=assistant_id,
        module='module_2', provider=provider, ui_enabled=kind == 'secondary')
    arguments = {'state_version': version}
    if card:
        arguments.update(card_id=card['id'], card_revision=card['revision'])
    task = asyncio.create_task(tool.execute(call('confirm_pa_card' if kind == 'primary' else 'confirm_secondary_goal_card', arguments)))
    await asyncio.wait_for(started.wait(), 1)
    if superseded:
        # A new request can persist its user while the classifier is waiting.
        from app.conversation_store import start_turn
        await asyncio.wait_for(start_turn(db, subject_id='a', session_id='chat-a',
            user_text='等等，我要改一下计划。'), 1)
    release.set()
    result = await asyncio.wait_for(task, 2)
    assert provider.calls == 1
    if superseded:
        assert result['reason'] == 'current_user_boundary_changed'
        _, actual = await runtime_for(db, 'chat-a')
        assert actual['current_module'] == 'module_2'
    else:
        assert result['status'] == ('confirmed' if kind == 'primary' else 'secondary_confirmed'), result
