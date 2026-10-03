"""Real DB confirmations bypass model selection only for bound UI button events."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.goal_card_interaction import bind_card_action, confirmation_button_text
from app.goal_card_workspace import read_card_row, sync_core_card
from app.models import Conversation, ConversationMessage
from app.pre_reply_routing import route_before_reply
from app.structured_goal_confirmation import confirm_goal_button
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api
from test_goal_card_interaction import (enable_form, invoke, message, open_primary,
    persist_display, review, submit_form, tools)
from test_turn_confirmation_0924 import setup_turn


async def ready_button(goal_api, context, kind='primary', *, bind=True, text=None):
    client, db, _ = goal_api
    if kind == 'primary':
        await setup_turn(db, '我想继续细化散步目标。')
        executor = tools(db, 21)
        await invoke(executor, 'open_goal_card', kind=kind,
            source_message_id=21, source_quote='我想继续细化散步目标。')
        conversation, state = await runtime_for(db, 'chat-a')
        plan = (await db.execute(select(schema.tables['module_two_record']))).mappings().one()
        card = await sync_core_card(db, conversation, state, 'discussing', plan)
        await db.commit()
        presented = await review(executor, card)
    else:
        _, _, card = await open_primary(db, '原核心目标不变，我额外想骑车。', kind=kind)
        _, executor, card = await submit_form(client, db, card, {'activity_content': '骑车'})
        checked = await review(executor, card, near_term=None, manageable=None)
        presented = await invoke(executor, 'present_secondary_goal_card', card_id=card['id'],
            card_revision=checked['goal_card']['revision'])
    assert presented['status'] == 'ready_to_display', presented
    assistant = await persist_display(db, presented['display_text'])
    card = presented['goal_card']
    user = await message(db, text or confirmation_button_text(kind, card['revision']))
    conversation, _ = await runtime_for(db, 'chat-a')
    if bind:
        await bind_card_action(db, conversation, user.id, {
            'goal_card_action': 'confirm', 'goal_card_id': card['id'],
            'goal_card_revision': str(card['revision'])})
    await db.commit()
    state = {'subject_id': 'a', 'session_id': 'chat-a', 'user_message_id': user.id,
        'user_input': user.content, 'current_module': 'module_2', 'memory': {}, 'telemetry': {}}
    ctx = replace(context, sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        settings=context.settings.model_copy(update={'database_schema_version': 'v2',
            'pa_card_tools_enabled': True, 'goal_card_ui_enabled': True}))
    return db, state, ctx, card, assistant


@pytest.mark.parametrize('kind', ['primary', 'secondary'])
async def test_button_commits_before_reply_without_router_tools_or_semantic_model(goal_api, context, monkeypatch, kind):
    db, state, ctx, card, _ = await ready_button(goal_api, context, kind)
    forbidden = AsyncMock(side_effect=AssertionError('button must not ask a model'))
    monkeypatch.setattr('app.pre_reply_routing.decide_target_module_with_reasoning', forbidden)
    monkeypatch.setattr('app.confirmation_intent.semantic_confirmation', forbidden)
    events = []
    monkeypatch.setattr('app.pre_reply_routing._emit', events.append)
    revision = await db.scalar(select(Conversation.revision).where(Conversation.id == 1))
    await db.rollback()
    result = await route_before_reply(state, ctx)
    expected = 'module_3' if kind == 'primary' else 'module_2'
    assert result['extracted_intent'] == expected
    assert result['confirmation_receipt']['status'] == ('confirmed' if kind == 'primary' else 'secondary_confirmed')
    assert result['structured_goal_action_handled'] and not result['pa_background_pending']
    assert events[0]['reply_module'] == expected
    forbidden.assert_not_called()
    _, actual = await runtime_for(db, 'chat-a')
    assert actual['current_module'] == expected
    durable = await read_card_row(db, 1, 'a')
    assert durable['phase'] == 'confirmed' and durable['confirmation_message_id'] == state['user_message_id']
    assert await db.scalar(select(Conversation.revision).where(Conversation.id == 1)) > revision
    from app.pa_background import schedule_pa_background
    assert schedule_pa_background({**result, 'pa_background_pending': True}, ctx, assistant_message_id=999) is None


@pytest.mark.parametrize('tamper', ['revision', 'review', 'display_id', 'display_text', 'assistant_text',
    'next_user', 'foreign_user', 'plan_changed', 'difficulty_source', 'paused'])
async def test_button_keeps_existing_ownership_version_display_and_clinical_gates(goal_api, context, tamper):
    db, state, ctx, card, assistant = await ready_button(goal_api, context)
    table = schema.tables['goal_card_workspaces']
    row = await read_card_row(db, 1, 'a')
    if tamper == 'revision':
        await db.execute(update(table).where(table.c.id == card['id']).values(revision=row['revision']+1))
    elif tamper == 'review':
        await db.execute(update(table).where(table.c.id == card['id']).values(review={}))
    elif tamper == 'display_id':
        await db.execute(update(table).where(table.c.id == card['id']).values(display_assistant_message_id=None))
    elif tamper == 'display_text':
        await db.execute(update(table).where(table.c.id == card['id']).values(display_text='not actually shown'))
    elif tamper == 'assistant_text':
        await db.execute(update(ConversationMessage).where(ConversationMessage.id == assistant.id).values(content='改过的展示'))
    elif tamper == 'next_user':
        await message(db, '先等等，我想改成五分钟。')
    elif tamper == 'foreign_user':
        state['subject_id'] = 'b'
    elif tamper in {'plan_changed', 'difficulty_source'}:
        plan = schema.tables['module_two_record']
        values = {'duration_minutes': 50} if tamper == 'plan_changed' else {'difficulty_evidence': {}}
        await db.execute(update(plan).values(**values))
    elif tamper == 'paused':
        rt = schema.tables['conversation_runtime_states']
        await db.execute(update(rt).where(rt.c.conversation_id == 1).values(flow_status='paused'))
    await db.commit()
    result = await confirm_goal_button(state, ctx)
    assert result is None or result['status'] == 'blocked', result
    await db.rollback()
    _, actual = await runtime_for(db, 'chat-a')
    assert actual['current_module'] == 'module_2'
    assert (await read_card_row(db, 1, 'a'))['phase'] != 'confirmed'


@pytest.mark.parametrize('bind,text', [(False, None), (True, '好的'),
    (True, '先不要保存，改成五分钟。')])
async def test_free_chat_and_unbound_button_text_do_not_take_fast_path(goal_api, context, bind, text):
    db, state, ctx, _, _ = await ready_button(goal_api, context, bind=bind, text=text)
    assert await confirm_goal_button(state, ctx) is None
    _, actual = await runtime_for(db, 'chat-a')
    assert actual['current_module'] == 'module_2'


async def test_storage_failure_emits_no_receipt_and_keeps_ready_card(goal_api, context, monkeypatch):
    db, state, ctx, _, _ = await ready_button(goal_api, context)
    fail = AsyncMock(side_effect=SQLAlchemyError('simulated commit failure'))
    monkeypatch.setattr('app.dialogue_confirmation.commit_confirmation', fail)
    result = await route_before_reply(state, ctx)
    assert result['confirmation_receipt'] == {}
    assert result['extracted_intent'] == 'module_2'
    assert result['telemetry']['goal_button_confirmation']['status'] == 'failed'
    assert (await read_card_row(db, 1, 'a'))['phase'] == 'ready'


async def test_cancelled_confirmation_does_not_create_a_receipt(goal_api, context, monkeypatch):
    db, state, ctx, _, _ = await ready_button(goal_api, context)
    monkeypatch.setattr('app.dialogue_confirmation.commit_confirmation',
        AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await confirm_goal_button(state, ctx)
    assert (await read_card_row(db, 1, 'a'))['phase'] == 'ready'


async def test_risk_gate_precedes_structured_confirmation(goal_api, context):
    db, state, ctx, _, _ = await ready_button(goal_api, context)
    result = await route_before_reply({**state, 'risk': True}, ctx)
    assert result['routed_by'] == 'risk_hold'
    assert (await read_card_row(db, 1, 'a'))['phase'] == 'ready'


async def test_background_button_uses_structured_evidence_and_replays_receipt(goal_api, context, monkeypatch):
    db, state, ctx, _, _ = await ready_button(goal_api, context)
    assistant = await message(db, '我们继续看看接下来的安排。', 'assistant')
    from app.pa_card_tools import PACardTools
    executor = PACardTools(maker=ctx.sessionmaker, session_id='chat-a', user_id='a',
        user_message_id=state['user_message_id'], module='module_2', ui_enabled=True,
        assistant_message_id=assistant.id)
    forbidden = AsyncMock(side_effect=AssertionError('structured UI does not require language classification'))
    monkeypatch.setattr('app.confirmation_intent.semantic_confirmation', forbidden)
    snapshot = await invoke(executor, 'get_pa_card')
    result = await invoke(executor, 'confirm_pa_card', state_version=snapshot['state_version'])
    assert result['status'] == 'confirmed'
    replay = await invoke(executor, 'confirm_pa_card', state_version=snapshot['state_version'])
    assert replay['replayed'] and replay['status'] == 'confirmed'
    forbidden.assert_not_called()
