"""User evidence commits independently; a Router label never substitutes for it."""
from dataclasses import replace
from sqlalchemy import insert, select, update, delete
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy.exc import OperationalError
import pytest
from app.database_v2_schema import metadata as schema
from app.models import UserAccount, AccountSettings, ConversationMessage
from app.pre_reply_routing import route_before_reply, load_routing_snapshot
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api
from test_turn_confirmation_0924 import setup_turn


async def member(db):
    await db.execute(insert(UserAccount), {'id': 1, 'username': 'member', 'password_hash': 'unused', 'profile_uuid': 'a'})
    await db.execute(insert(AccountSettings), {'account_id': 1, 'role': 'user'})
    await db.commit()


@pytest.mark.parametrize('target', ['module_2', 'module_3', 'module_4'])
async def test_non_tool_member_accepts_exact_card_before_router_selects_independent_stage(goal_api, context, provider, target):
    client, db, _ = goal_api
    state, _ = await setup_turn(db, '确认，就按这个计划试试。')
    await member(db)
    provider.route_result = target
    ctx = replace(context, sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        settings=context.settings.model_copy(update={'database_schema_version': 'v2',
            'pa_card_tools_enabled': False, 'goal_card_ui_enabled': False}))
    await ctx.store.adopt('chat-a', [], 'module_2', {})
    result = await route_before_reply({**state, 'user_input': '确认，就按这个计划试试。', 'current_module': 'module_2'}, ctx)
    assert result['current_module'] == target
    assert result['confirmation_receipt']['source'] == 'verified_router_only_card'
    await db.rollback()
    plan = (await db.execute(select(schema.tables['module_two_record']))).mappings().one()
    assert plan['record_status'] == 'confirmed' and plan['confirmation_message_id'] == 21
    overview = (await client.get('/api/program/goals/overview')).json()
    assert any(card['status'] != 'draft' for card in overview['pa_cards'] if card['id'] == 'g1')
    assert provider.route_calls and '确认' in provider.route_calls[-1]


@pytest.mark.parametrize('case', ['correction', 'missing_field', 'changed_version', 'storage_failure'])
async def test_unverified_or_failed_acceptance_has_no_receipt_even_if_router_advances(goal_api, context, provider, monkeypatch, case):
    _, db, _ = goal_api
    text = '改成五分钟，还不要确认。' if case == 'correction' else '确认'
    state, _ = await setup_turn(db, text)
    await member(db)
    plans = schema.tables['module_two_record']
    if case in {'missing_field', 'changed_version'}:
        await db.execute(update(plans).values(duration_minutes=None if case == 'missing_field' else 5))
        await db.commit()
    if case == 'storage_failure':
        async def failed_commit(*args, **kwargs):
            raise OperationalError('test', {}, Exception('storage unavailable'))
        monkeypatch.setattr('app.dialogue_confirmation.commit_confirmation', failed_commit)
    provider.route_result = 'module_3'
    ctx = replace(context, sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        settings=context.settings.model_copy(update={'database_schema_version': 'v2',
            'pa_card_tools_enabled': False, 'goal_card_ui_enabled': False}))
    await ctx.store.adopt('chat-a', [], 'module_2', {})
    result = await route_before_reply({**state, 'user_input': text, 'current_module': 'module_2'}, ctx)
    assert result['current_module'] == 'module_3'  # No code routing gate was reintroduced.
    assert not result.get('confirmation_receipt')
    await db.rollback()
    assert await db.scalar(select(plans.c.record_status)) == 'draft'


async def test_confirmation_hook_rejects_stale_state_and_foreign_owner(goal_api, context):
    from app.router_business_confirmation import settle_confirmation
    _, db, _ = goal_api
    state, _ = await setup_turn(db, '确认')
    await member(db)
    ctx = replace(context, sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        settings=context.settings.model_copy(update={'database_schema_version': 'v2'}))
    snapshot = {**state, **await load_routing_snapshot(state, ctx)}
    stale = {**snapshot, 'routing_state': {**snapshot['routing_state'], 'row_version': -1}}
    assert await settle_confirmation(stale, ctx) is None
    assert await settle_confirmation({**snapshot, 'subject_id': 'b'}, ctx) is None
    await db.rollback()
    assert await db.scalar(select(schema.tables['module_two_record'].c.record_status)) == 'draft'


async def test_non_tool_from_zero_goals_to_displayed_confirmed_archive(goal_api, context, provider, monkeypatch):
    from app.graph import nodes
    from app.dialogue_confirmation import render_confirmation_summary
    from app.v2_workflow import record_values
    client, db, _ = goal_api
    await member(db)
    await db.execute(delete(schema.tables['pa_goals']).where(schema.tables['pa_goals'].c.user_id == 'a'))
    runtime = schema.tables['conversation_runtime_states']
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module='module_2'))
    text = '我选饭后散步十分钟作为核心目标，只做今天一次，在小区走，难度2分，下雨就在室内走。'
    data = {'target_activity_content': '饭后散步十分钟', 'schedule_text': '今天晚饭后',
        'target_activity_location': '小区', 'target_activity_duration_minutes': 10,
        'frequency_rule': {'schema_version': 1, 'text': '只做今天一次'},
        'difficulty_rating': 2, 'difficulty_evidence': {'rating': {'message_id': 10, 'quote': '难度2分', 'score_text': '2'}},
        'potential_barriers': ['下雨'], 'barrier_coping_plan': [{'barrier': '下雨', 'plan': '室内走'}],
        'goal_proposal': {'selection_status': 'selected', 'selection_role': 'core', 'goal_kind': 'primary',
            'selection_message_id': 10, 'selection_quote': text, 'activity_quote': '饭后散步十分钟'}}
    from app.goal_contract import difficulty_values
    from types import SimpleNamespace
    values = {**record_values('module_2', data), **difficulty_values(data, [SimpleNamespace(id=10, role='user', content=text, position=0)])}
    rendered = render_confirmation_summary('module_2', values)
    assert rendered
    await db.execute(insert(ConversationMessage), [
        {'id': 10, 'conversation_id': 1, 'position': 0, 'role': 'user', 'content': text},
        {'id': 11, 'conversation_id': 1, 'position': 1, 'role': 'assistant', 'content': rendered}])
    await db.commit()
    async def extract(*args, **kwargs): return dict(data)
    monkeypatch.setattr(nodes, '_extract_module_data', extract)
    ctx = replace(context, sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        settings=context.settings.model_copy(update={'database_schema_version': 'v2',
            'pa_card_tools_enabled': False, 'goal_card_ui_enabled': False}))
    await ctx.store.adopt('chat-a', [], 'module_2', {})
    state = {'session_id': 'chat-a', 'subject_id': 'a', 'user_message_id': 10, 'user_input': text,
        'current_module': 'module_2', 'extracted_intent': 'module_2', 'routing_mode': 'router_only',
        'active_cycle_id': None, 'routing_pending': True, 'final_response': rendered, 'memory': {}}
    nodes.schedule_background_routing(state, ctx, assistant_message_id=11)
    await nodes.wait_for_pending_routing('chat-a')
    await db.rollback()
    _, durable = await runtime_for(db, 'chat-a')
    assert durable['current_module'] == 'module_2'
    assert durable['active_goal_id'] and durable['active_cycle_id']
    assert durable['memory']['dialogue_draft']['summary_verified']
    assert durable['last_transition_reason'] == 'awaiting_record_confirmation'
    await db.execute(insert(ConversationMessage), {'id': 12, 'conversation_id': 1, 'position': 2,
        'role': 'user', 'content': '确认，就按这个计划试试。'})
    await db.commit()
    provider.route_result = 'module_3'
    result = await route_before_reply({**state, 'user_message_id': 12, 'user_input': '确认，就按这个计划试试。'}, ctx)
    assert result['current_module'] == 'module_3' and result['confirmation_receipt']
    await db.rollback()
    plans = schema.tables['module_two_record']
    plan = (await db.execute(select(plans))).mappings().one()
    assert plan['record_status'] == 'confirmed' and plan['confirmation_message_id'] == 12
    assert plan['duration_minutes'] == 10
    assert plan['frequency_rule']['text'] == '只做今天一次'
    for _ in range(2):  # Independent reads match a refresh, with one durable card.
        archive = (await client.get('/api/program/goals/overview')).json()['pa_cards']
        assert len(archive) == 1 and archive[0]['id'] == durable['active_goal_id']
        assert archive[0]['status'] != 'draft'
