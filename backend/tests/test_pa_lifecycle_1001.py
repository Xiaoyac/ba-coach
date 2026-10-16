"""M4 closes attempts; only a later sourced M2 choice changes goals."""
import pytest
from sqlalchemy import select, insert, update, delete, func
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.m4_contract import normalize, STEPS, missing_fields
from app.v2_workflow import create_goal_from_agent_dialogue, record_steps
from test_goal_overview import goal_api
from test_cycle_program_0914 import seed_review, confirmation
from test_m4_contract_0917 import _evidence, _message
from scripts.migrate_m4_attempt_closure import migration_sql


def test_executed_review_ends_without_future_goal_decision():
    data, messages = _evidence()
    data.pop('review_decision')
    data['m4_contract'].pop('decision_quote')
    messages = [m for m in messages if m.id != 25]
    result = normalize(data, messages, session_id='s', cycle_id='c', assistant_message_id=26)
    assert result['review_decision'] is None
    assert not missing_fields(result, session_id='s', cycle_id='c')
    assert result['phase_c']['_m4_contract']['completed_steps'] == list(STEPS)


def test_reluctance_not_terminal_but_final_refusal_is():
    data, messages = _evidence(scenario='B')
    data.pop('review_decision')
    data['m4_contract'].pop('decision_quote')
    first = normalize(data, messages, session_id='s', cycle_id='c', assistant_message_id=26)
    assert 'review_decision_made' not in first['phase_c']['_m4_contract']['completed_steps']
    messages.insert(-1, _message(30, 25, 'user', '这一次我最终决定不执行了。'))
    data['m4_contract'].update(final_non_execution=True, final_non_execution_quote='这一次我最终决定不执行了。')
    final = normalize(data, messages, session_id='s', cycle_id='c', assistant_message_id=26)
    assert not missing_fields(final, session_id='s', cycle_id='c')
    assert final['execution_result'] == 3
    data['m4_contract']['final_non_execution_quote'] = 'a fabricated user quote'
    assert missing_fields(normalize(data, messages, session_id='s', cycle_id='c', assistant_message_id=26))


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [1, 2, 3, 4])
async def test_closing_card_does_not_choose_next_goal(goal_api, result):
    client, db, _ = goal_api
    await seed_review(db, decision=1)
    reviews = schema.tables['module_four_record']
    await db.execute(update(reviews).where(reviews.c.id == 'review-draft').values(review_decision=None, execution_result=result))
    await db.execute(delete(schema.tables['pa_review_details']))
    await db.commit()
    response = await client.post('/api/program/chat-a/confirm', json=await confirmation(client))
    assert response.status_code == 200, response.text
    state = response.json()['runtime']
    assert (state['current_module'], state['active_goal_id'], state['active_cycle_id']) == ('module_2', None, None)
    assert state['memory']['last_reviewed_cycle']['goal_id'] == 'g1'
    goals, cycles = schema.tables['pa_goals'], schema.tables['pa_cycles']
    assert await db.scalar(select(goals.c.status).where(goals.c.id=='g1')) == 'active'
    assert await db.scalar(select(func.count()).select_from(cycles)) == 1
    cards = (await client.get('/api/program/goals/overview')).json()['pa_cards']
    assert not any(c['id']=='private' for c in cards)
    old = next(c for c in cards if c['card_id']=='reviewed-cycle')
    assert old['status'] == 'completed'
    assert old['execution_outcome'] == {1:'completed',2:'not_started',3:'not_started',4:'partial'}[result]


async def close_and_choose(db, action='keep', quote='下一轮我保持原目标。', goal_id='g1'):
    await seed_review(db, decision=1)
    await record_steps(db, session_id='chat-a', user_id='a', module='module_4', requested_target='module_2', steps=[], assistant_message_id=20)
    db.add_all([ConversationMessage(id=31,conversation_id=1,position=11,role='user',content=quote),
        ConversationMessage(id=32,conversation_id=1,position=12,role='assistant',content='我们来讨论下一轮。')])
    await db.flush()
    data={'activity_content':'游泳','core_goal_choice':{'action':action,'goal_id':goal_id,'message_id':31,'quote':quote},
        'goal_proposal':{'selection_status':'selected','selection_role':'core','goal_kind':'primary',
            'selection_message_id':31,'selection_quote':quote,'activity_quote':'游泳'}}
    return await create_goal_from_agent_dialogue(db,session_id='chat-a',user_id='a',data=data,
        completed_steps=[],assistant_message_id=32),data


@pytest.mark.asyncio
async def test_keep_starts_separate_draft_old_card_stays_closed(goal_api):
    client,db,_=goal_api
    created,data=await close_and_choose(db)
    assert created['goal_id']=='g1'
    plans=schema.tables['module_two_record']
    plan=(await db.execute(select(plans).where(plans.c.id==created['plan_id']))).mappings().one()
    assert plan['record_status']=='draft' and plan['version_no']==2
    assert plan['activity_content']=='晚饭后散步十分钟' and plan['confirmation_message_id'] is None
    assert await create_goal_from_agent_dialogue(db,session_id='chat-a',user_id='a',data=data,completed_steps=[],assistant_message_id=32) is None
    cards=(await client.get('/api/program/goals/overview')).json()['pa_cards']
    assert next(c for c in cards if c['card_id']=='reviewed-cycle')['status']=='completed'
    assert next(c for c in cards if c['card_id']==created['cycle_id'])['status']=='draft'


@pytest.mark.asyncio
async def test_replace_archives_only_chosen_core(goal_api):
    _,db,_=goal_api
    created,_=await close_and_choose(db,'replace','我要用游泳替换原来的核心目标。')
    assert created and created['goal_id']!='g1'
    goals=schema.tables['pa_goals']
    old=(await db.execute(select(goals).where(goals.c.id=='g1'))).mappings().one()
    assert old['status']=='replaced' and old['replaced_by_goal_id']==created['goal_id']
    assert await db.scalar(select(goals.c.status).where(goals.c.id=='private'))=='active'
    cycles=schema.tables['pa_cycles']
    assert await db.scalar(select(cycles.c.status).where(cycles.c.id=='reviewed-cycle'))=='completed'


@pytest.mark.asyncio
async def test_foreign_goal_choice_rejected(goal_api):
    _,db,_=goal_api
    created,_=await close_and_choose(db,'replace','我要用游泳替换原来的核心目标。',goal_id='private')
    assert created is None
    goals=schema.tables['pa_goals']
    assert await db.scalar(select(goals.c.status).where(goals.c.id=='g1'))=='active'


@pytest.mark.asyncio
async def test_unfinished_core_additional_preserves_original(goal_api):
    _,db,_=goal_api
    await seed_review(db, decision=1)
    await db.execute(insert(schema.tables['pa_goal_details']),{'goal_id':'g1','goal_kind':'primary'})
    rt=schema.tables['conversation_runtime_states']
    await db.execute(update(rt).where(rt.c.conversation_id==1).values(current_module='module_2'))
    db.add_all([ConversationMessage(id=31,conversation_id=1,position=11,role='user',content='原目标保留，游泳只是额外活动。'),
        ConversationMessage(id=32,conversation_id=1,position=12,role='assistant',content='好的。')])
    await db.flush()
    data={'activity_content':'游泳','core_goal_choice':{'action':'additional','goal_id':'g1','message_id':31,'quote':'原目标保留，游泳只是额外活动。'}}
    result=await create_goal_from_agent_dialogue(db,session_id='chat-a',user_id='a',data=data,completed_steps=[],assistant_message_id=32)
    assert result is None
    assert await db.scalar(select(rt.c.active_goal_id).where(rt.c.conversation_id==1))=='g1'
    cycles=schema.tables['pa_cycles']
    assert await db.scalar(select(cycles.c.status).where(cycles.c.id=='reviewed-cycle'))=='waiting_execution'


def test_migration_is_narrow_and_idempotent():
    old="record_status != 'confirmed' OR (execution_result IS NOT NULL AND review_decision IS NOT NULL AND confirmation_message_id IS NOT NULL)"
    assert 'DROP CHECK `old_check`' in migration_sql([{'name':'old_check','sqltext':old}])
    assert migration_sql([{'name':'new_check','sqltext':old.replace(' AND review_decision IS NOT NULL','')}]) is None
    with pytest.raises(ValueError): migration_sql([])


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['router_code', 'router_only'])
async def test_post_reply_closes_sourced_attempt_and_preserves_router_authority(goal_api, monkeypatch, mode):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.graph import nodes
    from app.session import InMemorySessionStore
    from test_m4_program_0917 import _rebuild_seeded_m4_payload
    _,db,_=goal_api
    await seed_review(db, decision=1)
    reviews=schema.tables['module_four_record']
    row=(await db.execute(select(reviews).where(reviews.c.id=='review-draft'))).mappings().one()
    raw=_rebuild_seeded_m4_payload(row)
    raw['review_decision']=None
    raw['m4_contract'].pop('decision_quote')
    raw.pop('review_followup')
    await db.commit()
    monkeypatch.setattr('app.v2_profile.enabled',lambda:True)
    monkeypatch.setattr(nodes,'_extract_module_data',AsyncMock(return_value=raw))
    monkeypatch.setattr('app.routing_modes.effective_routing_mode',AsyncMock(return_value=mode))
    audit=AsyncMock()
    monkeypatch.setattr('app.ai_telemetry.add_ai_event',audit)
    store=InMemorySessionStore(ttl_seconds=60,max_messages=40)
    context=SimpleNamespace(sessionmaker=async_sessionmaker(db.bind,expire_on_commit=False),
        provider=object(),store=store,settings=SimpleNamespace(database_schema_version='v2',extraction_max_tokens=4800))
    monkeypatch.setitem(nodes._routing_tasks,'chat-a',asyncio.current_task())
    await nodes._run_background_routing({'session_id':'chat-a','subject_id':'a',
        'extracted_intent':'module_4','active_cycle_id':'reviewed-cycle'},context,assistant_message_id=20)
    await db.rollback()
    cycles,rt=schema.tables['pa_cycles'],schema.tables['conversation_runtime_states']
    assert await db.scalar(select(cycles.c.status).where(cycles.c.id=='reviewed-cycle'))=='completed'
    runtime=(await db.execute(select(rt).where(rt.c.conversation_id==1))).mappings().one()
    assert runtime['active_cycle_id'] is None
    assert runtime['current_module']==('module_4' if mode=='router_only' else 'module_2')
    assert await db.scalar(select(reviews.c.record_status).where(reviews.c.id=='review-draft'))=='confirmed'
    assert audit.await_count==1


@pytest.mark.asyncio
async def test_selecting_ended_goal_opens_m2_draft(goal_api):
    client,db,_=goal_api
    await seed_review(db, decision=1)
    await record_steps(db,session_id='chat-a',user_id='a',module='module_4',
        requested_target='module_2',steps=[],assistant_message_id=20)
    goals=schema.tables["pa_goals"]
    await db.execute(update(goals).where(goals.c.id=="g1").values(current_plan_record_id="cycle-plan"))
    await db.commit()
    current=(await client.get('/api/program/chat-a')).json()
    response=await client.post('/api/program/chat-a/goal',json={
        'goal_id':'g1','row_version':current['runtime']['row_version']})
    assert response.status_code==200,response.text
    state=response.json()['runtime']
    assert state['current_module']=='module_2' and state['active_cycle_id']!='reviewed-cycle'
    assert response.json()['draft']['record_status']=='draft'
