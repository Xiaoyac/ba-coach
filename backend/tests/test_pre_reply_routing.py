"""Current input -> one router -> verified commit -> selected reply, offline."""
import dataclasses
import pytest
import pytest_asyncio
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.pre_reply_routing import load_routing_snapshot, route_before_reply
from app.router_agent import decide_target_module_with_reasoning

@pytest.mark.parametrize('current,proposed,expected', [('module_1','3','module_1'),
    ('module_2','1','module_2'),('module_3','2','module_2'),('module_4','3','module_3')])
async def test_structural_transitions_without_microstep_json(provider,current,proposed,expected):
    provider.route_result=proposed
    decision=await decide_target_module_with_reasoning(provider,current_module=current,
        user_input='这是当前输入',ai_output='未生成的AI回复',has_pa_card=True,
        conversation_context='user：以前的输入',business_state={'flow_status':'active'})
    assert decision.target_module==expected
    assert '这是当前输入' in provider.route_calls[-1]
    assert '以前的输入' in provider.route_calls[-1]
    assert '未生成的AI回复' not in provider.route_calls[-1]

async def test_commit_veto_keeps_actual_module_and_fresh_context(context,provider):
    provider.route_result='2'
    calls=[]
    async def commit(state,ctx,decision):
        calls.append((state['user_input'],decision.target_module))
        return {'current_module':'module_1','clinical_context':['已刷新事实'],
                'diagnostics':{'block_reasons':['用户确认不足']}}
    result=await route_before_reply({'session_id':'s','user_input':'进入下一步',
        'current_module':'module_1','chat_history':[]},context,apply_decision=commit)
    assert calls==[('进入下一步','module_2')]
    assert result['extracted_intent']==result['next_module']=='module_1'
    assert result['clinical_context']==['已刷新事实']
    assert result['telemetry']['router_pre_reply']['proposed_module']=='module_2'
    assert '用户确认不足' in result['routing_reasoning_content']
    assert result['telemetry']['router_duration_ms']>=0

async def test_same_module_still_settles_current_user_confirmation(context,provider):
    provider.route_result='3'
    calls=[]
    async def commit(state,ctx,decision):
        calls.append(decision.target_module)
        return {'current_module':'module_3','memory':{'recording_status':'declined'}}
    result=await route_before_reply({'session_id':'s','user_input':'暂时不记录',
        'current_module':'module_3','memory':{'pa_card':'verified'}},context,apply_decision=commit)
    assert calls==['module_3']
    assert result['memory']['recording_status']=='declined'

@pytest.mark.parametrize('extras',[{'risk':{'suicidal':True}},{'forced_module':'module_2'},
                                  {'memory':{'sandbox_mode':'true'}}])
async def test_risk_pin_sandbox_skip_router_and_commit(context,provider,extras):
    async def forbidden(*args):
        pytest.fail('must not submit state')
    await route_before_reply({'session_id':'s','user_input':'hi','current_module':'module_1',**extras},
        context,apply_decision=forbidden)
    assert provider.route_calls==[]

async def test_failed_commit_never_publishes_proposal(context,provider):
    provider.route_result='2'
    async def fail(*args):
        raise RuntimeError('transaction rolled back')
    result=await route_before_reply({'session_id':'s','user_input':'继续','current_module':'module_1','clinical_context':['obsolete old-cycle facts']},
        context,apply_decision=fail)
    assert result['extracted_intent']=='module_1'
    assert result['telemetry']['execution_timeline'][-1]['status']=='failed'
    assert result['clinical_context']==[]

@pytest_asyncio.fixture
async def routing_database(context):
    from app.database_v2_schema import metadata as schema
    from app.models import Conversation,ConversationMessage,AIExecutionEvent
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    maker=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(schema.create_all)
        for model in (Conversation,ConversationMessage,AIExecutionEvent):
            await conn.run_sync(model.__table__.create)
    async with maker() as db:
        await db.execute(insert(schema.tables['user_profile']),[{'uuid':'a'},{'uuid':'b'}])
        await db.execute(insert(Conversation),{'id':1,'session_id':'s','subject_id':'a'})
        await db.execute(insert(ConversationMessage),[{'id':n,'conversation_id':1,'position':n-1,
            'role':'user' if n%2 else 'assistant','content':'我确认这版计划' if n==1 else f'历史消息{n}'} for n in range(1,14)])
        goals,plans,cycles,rt=[schema.tables[n] for n in ('pa_goals','module_two_record','pa_cycles','conversation_runtime_states')]
        await db.execute(insert(goals),{'id':'g','user_id':'a','title':'散步','status':'active','current_plan_record_id':'p1'})
        await db.execute(insert(plans),{'id':'p1','goal_id':'g','version_no':1,'timezone':'Asia/Shanghai',
            'record_status':'confirmed','confirmation_status':'confirmed','confirmation_message_id':1,
            'activity_content':'散步','schedule_text':'晚饭后'})
        await db.execute(insert(cycles),{'id':'c1','goal_id':'g','ordinal':1,'status':'waiting_execution','module_two_record_id':'p1'})
        await db.execute(insert(rt),{'conversation_id':1,'current_module':'module_3','active_goal_id':'g','active_cycle_id':'c1','memory':{}})
        await db.commit()
    ctx=dataclasses.replace(context,sessionmaker=maker,settings=context.settings.model_copy(update={'database_schema_version':'v2'}))
    try:
        yield ctx,maker,schema
    finally:
        await engine.dispose()

async def test_confirmed_card_older_than_three_turns_is_valid(routing_database):
    context,_,_=routing_database
    snapshot=await load_routing_snapshot({'session_id':'s','subject_id':'a','user_message_id':13},context)
    assert snapshot['current_module']=='module_3'
    assert snapshot['routing_state']['has_pa_card'] is True
    assert len(snapshot['routing_history'])==12
    assert snapshot['routing_history'][-1].content=='历史消息12'

@pytest.mark.parametrize('invalid',['old_version','other_cycle','unconfirmed','other_user','wrong_boundary'])
async def test_invalid_card_or_ownership_not_used(routing_database,invalid):
    context,maker,schema=routing_database
    state={'session_id':'s','subject_id':'a','user_message_id':13}
    async with maker() as db:
        plans,cycles,rt,goals=[schema.tables[n] for n in ('module_two_record','pa_cycles','conversation_runtime_states','pa_goals')]
        if invalid=='old_version':
            await db.execute(insert(plans),{'id':'p2','goal_id':'g','version_no':2,'timezone':'Asia/Shanghai'})
            await db.execute(update(goals).where(goals.c.id=='g').values(current_plan_record_id='p2'))
        elif invalid=='other_cycle':
            await db.execute(insert(cycles),{'id':'c2','goal_id':'g','ordinal':2,'status':'planning'})
            await db.execute(update(rt).where(rt.c.conversation_id==1).values(active_cycle_id='c2'))
        elif invalid=='unconfirmed':
            await db.execute(update(plans).where(plans.c.id=='p1').values(record_status='draft',confirmation_status='unconfirmed'))
        elif invalid=='other_user':
            state['subject_id']='b'
        else:
            state['user_message_id']=12
        await db.commit()
    if invalid in {'other_user','wrong_boundary'}:
        with pytest.raises(ValueError):
            await load_routing_snapshot(state,context)
    else:
        assert (await load_routing_snapshot(state,context))['routing_state']['has_pa_card'] is False

@pytest.mark.parametrize('raw,expected', [
    ('{"target_module":"3","knowledge_task":"m3_recording_concern"}', 'm3_recording_concern'),
    ('{"target_module":"3","knowledge_task":"m2_values"}', 'general'),
    ('{"target_module":"3","knowledge_task":"arbitrary_private_fields"}', 'general'),
    ('3', 'general'),
])
async def test_knowledge_task_is_optional_module_scoped_allowlist(provider,raw,expected):
    provider.route_result=raw
    result=await decide_target_module_with_reasoning(provider,current_module='module_3',
        user_input='记录是不是又给我增加负担',has_pa_card=True)
    assert result.knowledge_task==expected

async def test_task_reverts_to_general_if_commit_vetoes_its_module(context,provider):
    provider.route_result='{"target_module":"2","knowledge_task":"m2_values"}'
    async def commit(*args):
        return {'current_module':'module_1'}
    result=await route_before_reply({'session_id':'s','current_module':'module_1','user_input':'下一步'},
        context,apply_decision=commit)
    assert result['knowledge_task']=='general'

async def test_committed_module_survives_context_refresh_failure(routing_database,provider):
    context,maker,schema=routing_database
    provider.route_result='2'
    async def commit_then_fail(*args):
        async with maker() as db:
            rt=schema.tables['conversation_runtime_states']
            await db.execute(update(rt).where(rt.c.conversation_id==1).values(current_module='module_2'))
            await db.commit()
        raise RuntimeError('commit succeeded, enrichment failed')
    result=await route_before_reply({'session_id':'s','subject_id':'a','user_message_id':13,
        'user_input':'修改当前计划','current_module':'module_3','clinical_context':['obsolete old cycle']},
        context,apply_decision=commit_then_fail)
    assert result['extracted_intent']=='module_2'
    assert result['clinical_context']==[]
    assert result['telemetry']['router_pre_reply']['database_module']=='module_2'

async def test_m4_empty_extraction_keeps_progress_without_missing_column(routing_database):
    from app.v2_workflow import record_steps
    from app.models import ConversationMessage
    context,maker,schema=routing_database
    async with maker() as db:
        rt=schema.tables['conversation_runtime_states']
        await db.execute(update(rt).where(rt.c.conversation_id==1).values(current_module='module_4'))
        await db.execute(insert(schema.tables['pa_cycle_progress']),{'cycle_id':'c1',
            'module_2_steps':[],'module_3_steps':[],'module_4_steps':[]})
        await db.execute(insert(ConversationMessage),{'id':14,'conversation_id':1,'position':13,
            'role':'assistant','content':'我们聊聊实际发生了什么。'})
        target,cycle=await record_steps(db,session_id='s',user_id='a',module='module_4',
            requested_target='module_4',steps=[],assistant_message_id=14,allow_transition=False)
        await db.commit()
    assert target=='module_4' and cycle=='c1'
