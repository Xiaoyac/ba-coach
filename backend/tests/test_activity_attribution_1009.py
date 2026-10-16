"""Execution identity tests use isolated DBs and authenticated message rows."""
import copy
import json
from datetime import datetime

import pytest
from sqlalchemy import insert, select, update, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.goal_contract import capture_activities, evidence_messages
from app.activity_attribution import activity_candidates, occurrence_day, review_execution_binding
from app.v2_workflow import runtime_for, persist_record
from app.pa_card_tools import PACardTools
from test_goal_overview import goal_api


async def seed(db, *, current_name='站桩'):
    goals, plans, cycles, rt = (schema.tables[n] for n in ('pa_goals', 'module_two_record', 'pa_cycles', 'conversation_runtime_states'))
    await db.execute(insert(ConversationMessage), [
        {'id':1,'conversation_id':1,'position':1,'role':'user','content':'确认旧站桩计划', 'created_at':datetime(2026,10,1)},
        {'id':2,'conversation_id':1,'position':2,'role':'user','content':'确认新的'+current_name+'计划', 'created_at':datetime(2026,10,9)}])
    await db.execute(update(goals).where(goals.c.id == 'g1').values(title='站桩', status='replaced', created_at=datetime(2026,10,1)))
    await db.execute(update(goals).where(goals.c.id == 'g2').values(title=current_name, status='active', created_at=datetime(2026,10,9)))
    for gid, pid, stamp, name in [('g1','old-plan',datetime(2026,10,1),'站桩'), ('g2','new-plan',datetime(2026,10,9), current_name)]:
        await db.execute(insert(plans), {'id':pid,'goal_id':gid,'version_no':1,'timezone':'Asia/Shanghai',
            'record_status':'confirmed','confirmation_status':'confirmed','confirmation_message_id':1 if gid=='g1' else 2,
            'activity_content':name,'schedule_text':'每晚一次','created_at':stamp})
        await db.execute(update(goals).where(goals.c.id == gid).values(current_plan_record_id=pid))
    await db.execute(insert(cycles), [
        {'id':'old-cycle','goal_id':'g1','ordinal':1,'module_two_record_id':'old-plan','status':'completed',
         'created_at':datetime(2026,10,1),'completed_at':datetime(2026,10,8,15)},
        {'id':'new-cycle','goal_id':'g2','ordinal':1,'module_two_record_id':'new-plan','status':'waiting_execution',
         'created_at':datetime(2026,10,9),'completed_at':None}])
    await db.execute(insert(schema.tables['pa_cycle_progress']), {'cycle_id':'new-cycle','module_2_steps':[], 'module_3_steps':[], 'module_4_steps':[]})
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(current_module='module_4',active_goal_id='g2',
        active_cycle_id='new-cycle',flow_status='waiting_execution'))
    await db.commit()


async def message(db, text, *, mid=100, day=9, hour=8):
    await db.execute(insert(ConversationMessage), {'id':mid,'conversation_id':1,'position':mid,'role':'user',
        'content':text, 'created_at':datetime(2026,10,day,hour)})
    await db.commit()
    return await db.get(ConversationMessage, mid)


def observation(msg, *, activity='站桩', goal='g2', plan='new-plan', cycle='new-cycle',
                time='今天', kind='performed', status='linked', relation='new', execution=None):
    return {'activity_content':activity,'event_kind':kind,'source_quote':msg.content,'occurred_at_text':time,
        'attribution': {'status':status,'relation':relation,'goal_id':goal,'plan_id':plan,'cycle_id':cycle,
            'execution_id':execution,'evidence':{'message_id':msg.id,'quote':msg.content}}}


async def capture(db, raw):
    conversation,state=await runtime_for(db,'chat-a')
    result=await capture_activities(db,user_id='a',conversation=conversation,state=state,raw=raw,
        messages=await evidence_messages(db,1,'a'))
    await db.commit()
    return result


@pytest.mark.asyncio
async def test_same_name_old_goal_uses_proposed_owned_identity_and_old_date(goal_api):
    _,db,_=goal_api; await seed(db)
    msg=await message(db,'上周的旧站桩目标，昨天又做了一次站桩')
    raw=observation(msg,goal='g1',plan='old-plan',cycle='old-cycle',time='昨天')
    result=await capture(db,[raw])
    assert result[0]['attribution_status']=='linked'
    ex=(await db.execute(select(schema.tables['pa_executions']))).mappings().one()
    assert (ex['goal_id'],ex['cycle_id'],str(ex['occurred_on']))==('g1','old-cycle','2026-10-08')
    _,state=await runtime_for(db,'chat-a')
    binding=await review_execution_binding(db,user_id='a',state=state,data={'m4_contract':{'execution_ref':{'execution_id':ex['id']}}})
    assert binding=={'ready':False,'reason':'feedback_belongs_to_other_execution'}
    assert await db.scalar(select(schema.tables['pa_cycles'].c.status).where(schema.tables['pa_cycles'].c.id=='old-cycle'))=='completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutate,reason',[
    ({'goal_id':'private'},'plan_not_confirmed_for_goal'),
    ({'plan_id':'old-plan'},'plan_not_confirmed_for_goal'),
    ({'cycle_id':'old-cycle'},'cycle_plan_mismatch'),
    ({'status':'uncertain'},'attribution_unclear'),
    ({'evidence':{'message_id':999,'quote':'今天站桩了'}},'attribution_source_invalid'),
])
async def test_invalid_scope_is_saved_unassigned_not_guessed(goal_api,mutate,reason):
    _,db,_=goal_api;await seed(db);msg=await message(db,'今天站桩了');raw=observation(msg)
    raw['attribution'].update(mutate)
    result=(await capture(db,[raw]))[0]
    # A foreign goal is rejected at ownership before its plan is consulted.
    assert result['reason_code']==('goal_not_owned' if mutate.get('goal_id')=='private' else reason)
    event=(await db.execute(select(schema.tables['pa_activity_events']))).mappings().one()
    assert event['goal_id'] is None and event['cycle_id'] is None
    assert result['attribution_status']=='uncertain'


@pytest.mark.asyncio
async def test_ambiguous_date_never_defaults_to_current_cycle(goal_api):
    _,db,_=goal_api;await seed(db);msg=await message(db,'我站桩了');raw=observation(msg,time=None)
    assert (await capture(db,[raw]))[0]['reason_code']=='cycle_occurrence_reference_unclear'
    raw['attribution']['cycle_id']=None
    assert (await capture(db,[raw]))[0]['attribution_status']=='linked'
    ex=(await db.execute(select(schema.tables['pa_executions']))).mappings().one()
    assert ex['cycle_id'] is None and ex['occurred_on'] is None


@pytest.mark.asyncio
async def test_old_date_cannot_bind_new_same_name_cycle(goal_api):
    _,db,_=goal_api;await seed(db);msg=await message(db,'昨天完成站桩');raw=observation(msg,time='昨天')
    assert (await capture(db,[raw]))[0]['reason_code']=='occurrence_precedes_plan'


@pytest.mark.asyncio
async def test_retry_stable_execution_but_again_is_another_execution(goal_api):
    _,db,_=goal_api;await seed(db);one=await message(db,'今天站桩了');raw=observation(one)
    first=(await capture(db,[raw]))[0]['execution_id']
    assert (await capture(db,[raw]))[0]['execution_id']==first
    two=await message(db,'今天又站桩了一次',mid=101)
    second=(await capture(db,[observation(two)]))[0]['execution_id']
    assert second!=first
    three=await message(db,'补充刚才那一次站桩，觉得轻松',mid=102)
    third=(await capture(db,[observation(three,time=None,relation='existing',execution=second)]))[0]['execution_id']
    assert third==second
    assert await db.scalar(select(func.count()).select_from(schema.tables['pa_executions']))==2


@pytest.mark.asyncio
async def test_temporal_anchor_is_message_local_day_not_extraction_day(goal_api):
    _,db,_=goal_api;msg=await message(db,'今天站桩了',day=8,hour=17)
    assert str(occurrence_day('今天',msg))=='2026-10-09'
    assert occurrence_day('昨天',msg) is None
    assert occurrence_day('前几天',msg) is None


@pytest.mark.asyncio
async def test_main_not_performed_and_unplanned_done_remain_independent(goal_api):
    _,db,_=goal_api;await seed(db,current_name='跑步');msg=await message(db,'今天没跑步，但跳绳十分钟了')
    results=await capture(db,[observation(msg,activity='跑步',kind='not_performed'),
        observation(msg,activity='跳绳',goal=None,plan=None,cycle=None,status='unplanned')])
    rows=(await db.execute(select(schema.tables['pa_executions']))).mappings().all()
    assert {r['goal_id'] for r in rows}=={'g2',None}
    _,state=await runtime_for(db,'chat-a')
    blocked=await review_execution_binding(db,user_id='a',state=state,data={'phase_b':{'overt':{'action_taken':True}},
        'm4_contract':{'execution_ref':{'execution_id':results[0]['execution_id']}}})
    assert blocked['reason']=='review_execution_result_conflict'
    blocked=await review_execution_binding(db,user_id='a',state=state,data={'m4_contract':{'execution_ref':{'execution_id':results[1]['execution_id']}}})
    assert not blocked['ready']


@pytest.mark.asyncio
async def test_old_backfill_requires_authentic_id_and_does_not_erase_other_observations(goal_api):
    _,db,_=goal_api;await seed(db);msg=await message(db,'今天站桩了，也散步了')
    raw=observation(msg);await capture(db,[raw,observation(msg,activity='散步',goal=None,plan=None,cycle=None,status='unplanned')])
    await message(db,'嗯，站桩那次很放松',mid=101)
    raw['source_message_id']=100
    await capture(db,[raw])
    assert await db.scalar(select(func.count()).select_from(schema.tables['pa_activity_events']))==2
    raw['source_message_id']=999
    await capture(db,[raw])
    assert await db.scalar(select(func.count()).select_from(schema.tables['pa_activity_events']))==2


@pytest.mark.asyncio
@pytest.mark.parametrize('use_tool',[False,True])
async def test_real_persistence_and_native_tool_do_not_pollute_current_review(goal_api,use_tool):
    _,db,_=goal_api;await seed(db);msg=await message(db,'昨天完成了旧目标站桩')
    raw=observation(msg,goal='g1',plan='old-plan',cycle='old-cycle',time='昨天')
    data={'activity_observations':[raw], 'phase_b':{'overt':{'activity':'站桩','action_taken':True,'completion_status':'complete'}},
        'm4_contract':{'phase_b_quote':msg.content,'execution_ref':{'source_message_id':msg.id,'source_quote':msg.content,'activity_content':'站桩'}}}
    maker=async_sessionmaker(db.bind,expire_on_commit=False)
    if use_tool:
        tool=PACardTools(maker=maker,session_id='chat-a',user_id='a',user_message_id=100,module='module_4')
        def call(name,args={}):return {'id':'call1','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}
        snapshot=await tool.execute(call('get_pa_card'))
        assert {g['id'] for g in snapshot['activity_candidates']['goals']}=={'g1','g2'}
        command=call('save_pa_review',{'state_version':snapshot['state_version'],'data':data})
        result=await tool.execute(command)
        assert result['status']=='activity_facts_saved' and result['review_saved'] is False,result
        assert (await tool.execute(command))['replayed']
    else:
        data.update(_source_session_id='chat-a',_source_user_message_id=100)
        assert await persist_record(maker,module='module_4',user_id='a',data=data,cycle_id='new-cycle') is None
    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(schema.tables['module_four_record']))==0
    assert await db.scalar(select(func.count()).select_from(schema.tables['pa_executions']))==1
    assert await db.scalar(select(schema.tables['pa_cycles'].c.status).where(schema.tables['pa_cycles'].c.id=='new-cycle'))=='waiting_execution'


@pytest.mark.asyncio
async def test_additive_migration_does_not_infer_legacy_links(tmp_path):
    from scripts.migrate_activity_attribution_1009 import migrate,TABLE_NAMES
    from sqlalchemy import inspect
    path=str(tmp_path/'activity.db');url='sqlite+aiosqlite:///'+path
    engine=create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c:schema.create_all(c,tables=[t for t in schema.sorted_tables if t.name not in TABLE_NAMES]))
    await migrate(url,path)
    async with engine.connect() as conn:
        assert not set(TABLE_NAMES)&set(await conn.run_sync(lambda c:inspect(c).get_table_names()))
    await migrate(url,path,apply=True);await migrate(url,path,apply=True)
    async with engine.connect() as conn:
        for name in TABLE_NAMES:
            assert await conn.scalar(select(func.count()).select_from(schema.tables[name]))==0
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('phrase',['今天早上','刚才','刚刚'])
async def test_explicit_single_day_wording_does_not_require_reasking_date(goal_api,phrase):
    _,db,_=goal_api;await seed(db);msg=await message(db,phrase+'站桩做完了')
    assert (await capture(db,[observation(msg,time=phrase)]))[0]['attribution_status']=='linked'


@pytest.mark.asyncio
@pytest.mark.parametrize('phrase',['按我们刚定的安排做完了','做完了'])
async def test_unknown_day_can_use_authentic_unique_current_plan_reference(goal_api,phrase):
    _,db,_=goal_api;await seed(db,current_name='跑步')
    anchor=await db.get(ConversationMessage,2)
    msg=await message(db,phrase)
    raw=observation(msg,activity='跑步',time=None)
    raw['attribution']['cycle_reference']={'message_id':anchor.id,'quote':anchor.content}
    assert (await capture(db,[raw]))[0]['attribution_status']=='linked'
    row=(await db.execute(select(schema.tables['pa_executions']))).mappings().one()
    assert row['occurred_on'] is None and row['cycle_id']=='new-cycle'


@pytest.mark.asyncio
async def test_same_named_historical_plans_require_more_than_naked_current_reference(goal_api):
    _,db,_=goal_api;await seed(db)
    anchor=await db.get(ConversationMessage,2)
    msg=await message(db,'做完了');raw=observation(msg,time=None)
    raw['attribution']['cycle_reference']={'message_id':anchor.id,'quote':anchor.content}
    assert (await capture(db,[raw]))[0]['attribution_status']=='uncertain'


@pytest.mark.asyncio
async def test_admin_isolated_candidates_and_writes_cannot_cross_same_account_chat(goal_api):
    from app.models import UserAccount, AccountSettings
    _,db,_=goal_api;await seed(db)
    await db.execute(insert(UserAccount),{'id':1,'username':'isolated','password_hash':'unused','profile_uuid':'a'})
    await db.execute(insert(AccountSettings),{'account_id':1,'role':'admin'})
    await db.execute(update(schema.tables['pa_goals']).where(schema.tables['pa_goals'].c.id=='g1').values(created_from_conversation_id=2))
    await db.execute(update(schema.tables['pa_goals']).where(schema.tables['pa_goals'].c.id=='g2').values(created_from_conversation_id=1))
    await db.commit()
    candidates=await activity_candidates(db,'a',conversation_id=1)
    assert [g['id'] for g in candidates['goals']]==['g2']
    assert (await activity_candidates(db,'a',conversation_id=1,goal_id='g1'))['goals']==[]
    msg=await message(db,'昨天站桩了')
    assert (await capture(db,[observation(msg,goal='g1',plan='old-plan',cycle='old-cycle',time='昨天')]))[0]['reason_code']=='goal_not_owned'


@pytest.mark.asyncio
async def test_candidate_pages_mark_omission_and_history_remains_queryable(goal_api):
    _,db,_=goal_api
    for n in range(25):
        await db.execute(insert(schema.tables['pa_goals']),{'id':f'history-{n}','user_id':'a','title':f'历史活动{n}'})
    await db.commit()
    first=await activity_candidates(db,'a',conversation_id=1)
    assert len(first['goals'])==20 and first['page']['history_may_be_omitted']
    next_page=await activity_candidates(db,'a',conversation_id=1,offset=20)
    assert len(next_page['goals'])==7
    specific=await activity_candidates(db,'a',conversation_id=1,activity_query='历史活动24')
    assert [g['id'] for g in specific['goals']]==['history-24']


@pytest.mark.asyncio
async def test_same_name_explicit_current_has_two_authentic_anchors(goal_api):
    _,db,_=goal_api; await seed(db)
    msg=await message(db,'不是原来的那次，是按我们刚定的新站桩安排做完了')
    raw=observation(msg,time=None)
    raw['attribution']['cycle_reference']={'message_id':2,'quote':'确认新的站桩计划'}
    assert (await capture(db,[raw]))[0]['attribution_status']=='uncertain'
    raw['attribution']['disambiguation_ref']={'message_id':msg.id,'quote':msg.content}
    assert (await capture(db,[raw]))[0]['attribution_status']=='linked'


@pytest.mark.asyncio
async def test_retry_cannot_move_established_execution_scope(goal_api):
    _,db,_=goal_api; await seed(db)
    msg=await message(db,'今天做了站桩，完成当前安排')
    raw=observation(msg)
    first=(await capture(db,[raw]))[0]['execution_id']
    changed=copy.deepcopy(raw)
    changed['attribution'].update(goal_id='g1',plan_id='old-plan',cycle_id=None)
    result=(await capture(db,[changed]))[0]
    assert result['attribution_status']=='uncertain' and result['reason_code']=='origin_identity_conflict'
    execution=(await db.execute(select(schema.tables['pa_executions']).where(schema.tables['pa_executions'].c.id==first))).mappings().one()
    assert execution['goal_id']=='g2' and execution['cycle_id']=='new-cycle'


@pytest.mark.asyncio
async def test_candidate_pagination_always_supplies_current_bound_bundle(goal_api):
    _,db,_=goal_api; await seed(db)
    goals=schema.tables['pa_goals']
    await db.execute(insert(goals),[{'id':f'later-{i}','user_id':'a','title':f'其他活动{i}','status':'active','created_at':datetime(2026,10,10)} for i in range(25)])
    result=await activity_candidates(db,'a',conversation_id=1,source_text='做完了',history_offset=80)
    assert any(g['id']=='g2' for g in result['goals'])
    assert any(p['id']=='new-plan' for p in result['plans'])
    assert any(c['id']=='new-cycle' for c in result['cycles'])
    assert result['page']['history_may_be_omitted'] is True


@pytest.mark.asyncio
async def test_deleted_origin_clears_cross_chat_event_projection(goal_api):
    from app.activity_attribution import delete_activity_dependencies, attribution_context
    _, db, _ = goal_api
    await seed(db)
    msg = await message(db, '今天站桩做完了')
    first = (await capture(db, [observation(msg)]))[0]
    await db.execute(insert(ConversationMessage), {
        'id': 200, 'conversation_id': 2, 'position': 1, 'role': 'user',
        'content': '补充刚才那次站桩，感觉轻松', 'created_at': datetime(2026, 10, 9, 9)})
    await db.commit()
    source = await db.get(ConversationMessage, 200)
    conversation, state = await runtime_for(db, 'new-chat-a')
    raw = observation(source, time=None, relation='existing', execution=first['execution_id'])
    captured = await capture_activities(db, user_id='a', conversation=conversation, state=state,
        raw=[raw], messages=await evidence_messages(db, 2, 'a'))
    assert captured[0]['execution_id'] == first['execution_id']
    await delete_activity_dependencies(db, user_id='a', conversation_id=1)
    retained = (await db.execute(select(schema.tables['pa_activity_events']).where(
        schema.tables['pa_activity_events'].c.source_conversation_id == 2))).mappings().one()
    context = await attribution_context(db, user_id='a', conversation_id=2)
    assert retained['goal_id'] is None and retained['cycle_id'] is None, context
