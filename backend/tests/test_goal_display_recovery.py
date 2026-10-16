"""Reply-timeout recovery against SQLite, without provider or production access."""
import pytest
from sqlalchemy import insert, select, update, func
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.goal_card_workspace import _plan_fields
from app.dialogue_confirmation import fingerprint, render_confirmation_summary
from test_goal_overview import goal_api

async def ready_card(db, kind='primary'):
    source = ConversationMessage(conversation_id=1, position=0, role='user',
        content='我想明天在小区散步十分钟，就一次，难度2分，下雨就在室内走。')
    db.add(source)
    await db.flush()
    plan = {'id':'plan', 'goal_id':'g1', 'version_no':1, 'record_status':'draft',
        'timezone':'Asia/Shanghai', 'confirmation_status':'unconfirmed', 'activity_content':'散步', 'schedule_text':'明天',
        'location':'小区', 'duration_minutes':10, 'frequency_rule':{'schema_version':1,'text':'就一次'},
        'difficulty_rating':2, 'difficulty_evidence':{'rating':{'value':2,'message_id':source.id,'quote':'难度2分'}},
        'potential_barriers':['下雨'], 'barrier_coping_plan':[{'barrier':'下雨','plan':'室内走'}]}
    await db.execute(insert(schema.tables['module_two_record']),plan)
    await db.execute(insert(schema.tables['pa_cycles']),{'id':'cycle','goal_id':'g1','ordinal':1,
        'status':'planning','module_two_record_id':'plan'})
    await db.execute(update(schema.tables['conversation_runtime_states']).where(
        schema.tables['conversation_runtime_states'].c.conversation_id==1).values(
        current_module='module_2', active_goal_id='g1',active_cycle_id='cycle'))
    plan = dict((await db.execute(select(schema.tables['module_two_record']))).mappings().one())
    display = render_confirmation_summary('module_2',plan) if kind=='primary' else '次要活动：散步'
    review = {'revision':3,'is_pa':True,'manageable':True,'schedule_status':'actionable',
        'schedule_text':'明天','schedule_source':{'source_message_id':source.id,'source_quote':'明天'},
        'display_fingerprint':fingerprint(plan)}
    await db.execute(insert(schema.tables['goal_card_workspaces']),{'id':'card','user_id':'a',
        'conversation_id':1,'kind':kind,'phase':'ready','revision':3,'content_revision':2,
        'goal_id':'g1','cycle_id':'cycle','plan_id':'plan','fields':_plan_fields(plan),
        'concerns':[],'review':review,'opened_message_id':source.id,'display_user_message_id':source.id,
        'display_text':display})
    await db.commit()
    return {'card_id':'card','revision':3}

@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    from app.config import get_settings
    settings=get_settings().model_copy(update={'goal_card_ui_enabled':True,'database_schema_version':'v2'})
    monkeypatch.setattr('app.config.get_settings',lambda:settings)

@pytest.mark.parametrize('kind',['primary','secondary'])
async def test_timeout_reload_redisplay_is_idempotent_and_never_confirms(goal_api,kind):
    client,db,_=goal_api
    payload=await ready_card(db,kind)
    for _ in range(2):
        card=(await client.get('/api/program/chat-a/goal-card')).json()['card']
        assert card['confirmation']['recoverable'] and not card['confirmation']['allowed'], card['confirmation']
    first=await client.post('/api/program/chat-a/goal-card/redisplay',json=payload)
    assert first.status_code==200,first.text
    card=first.json()['card']
    assert card['confirmation']['allowed'] and card['revision']==3 and card['phase']=='ready'
    second=await client.post('/api/program/chat-a/goal-card/redisplay',json=payload)
    assert second.json()==first.json()
    assert await db.scalar(select(func.count()).select_from(ConversationMessage))==3
    assert await db.scalar(select(schema.tables['module_two_record'].c.record_status))=='draft'
    assert (await client.get('/api/program/chat-a/goal-card')).json()['card']['confirmation']['allowed']

@pytest.mark.parametrize('case',['stale','wrong_card','foreign','modified','paused','review','plan','legacy','cancelled'])
async def test_recovery_revalidates_every_gate(goal_api,case):
    client,db,_=goal_api
    payload=await ready_card(db)
    table=schema.tables['goal_card_workspaces']
    path='/api/program/chat-a/goal-card/redisplay'
    if case=='stale': payload['revision']=2
    elif case=='wrong_card': payload['card_id']='other'
    elif case=='foreign': path='/api/program/chat-b/goal-card/redisplay'
    elif case in {'modified','cancelled'}:
        db.add(ConversationMessage(conversation_id=1,position=1,role='user',content='时间改成后天' if case=='modified' else '先不确认'))
    elif case=='paused': await db.execute(update(table).values(phase='paused'))
    elif case=='review': await db.execute(update(table).values(review={'revision':1}))
    elif case=='legacy': await db.execute(update(table).values(review={'revision':3,'is_pa':True,'near_term':True,'manageable':True}))
    else: await db.execute(update(schema.tables['module_two_record']).values(schedule_text='后天'))
    await db.commit()
    count=await db.scalar(select(func.count()).select_from(ConversationMessage))
    response=await client.post(path,json=payload)
    assert response.status_code==(404 if case=='foreign' else 409),response.text
    assert await db.scalar(select(func.count()).select_from(ConversationMessage))==count

async def test_ui_status_question_does_not_revoke_plan(goal_api):
    client,db,_=goal_api
    payload=await ready_card(db)
    db.add(ConversationMessage(conversation_id=1,position=1,role='user',content='为什么按钮还是灰的？'))
    await db.commit()
    assert (await client.post('/api/program/chat-a/goal-card/redisplay',json=payload)).status_code==200

async def test_durable_reply_and_anchor_commit_together_even_with_timeout(goal_api):
    from app.conversation_store import finish_turn
    client,db,_=goal_api
    await ready_card(db)
    table=schema.tables['goal_card_workspaces']
    row=(await db.execute(select(table))).mappings().one()
    # The deployed compatibility schema also carries the legacy module projection
    # and analytics table. Add them only to this throwaway SQLite fixture.
    from sqlalchemy import text
    from app.models_business import InteractionStatus
    await db.execute(text('ALTER TABLE conversation_runtime_states ADD COLUMN module VARCHAR(32)'))
    connection=await db.connection()
    await connection.run_sync(lambda conn: InteractionStatus.__table__.create(conn,checkfirst=True))
    await db.commit()
    ident=await finish_turn(db,session_id='chat-a',user_message_id=row['display_user_message_id'],
        reply_text=row['display_text'],telemetry={'error_code':'main_timeout'})
    assert ident
    card=(await client.get('/api/program/chat-a/goal-card')).json()['card']
    assert card['confirmation']['allowed'] and card['confirmation']['display_message_id']==ident
    assistant=await db.get(ConversationMessage,ident)
    assert assistant.error_code=='main_timeout'

async def test_recovery_then_explicit_confirmation_commits_exactly_once(goal_api):
    import json
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.goal_card_interaction import validate_card_action, bind_card_action
    from app.v2_workflow import runtime_for
    from app.pa_card_tools import PACardTools
    client,db,_=goal_api
    payload=await ready_card(db)
    recovered=await client.post('/api/program/chat-a/goal-card/redisplay',json=payload)
    card=recovered.json()['card']
    action={'goal_card_id':'card','goal_card_revision':'3','goal_card_action':'confirm',
        'goal_card_display_message_id':str(card['confirmation']['display_message_id'])}
    text='确认这张核心目标卡，就按这个安排。'
    await validate_card_action(db,session_id='chat-a',user_id='a',metadata=action,text=text)
    user=ConversationMessage(conversation_id=1,position=3,role='user',content=text)
    db.add(user);await db.flush()
    conversation,_=await runtime_for(db,'chat-a')
    await bind_card_action(db,conversation,user.id,action);await db.commit()
    executor=PACardTools(maker=async_sessionmaker(db.bind,expire_on_commit=False),session_id='chat-a',
        user_id='a',user_message_id=user.id,module='module_2',ui_enabled=True)
    _,state=await runtime_for(db,'chat-a')
    call={'id':'confirm','type':'function','function':{'name':'confirm_pa_card',
        'arguments':json.dumps({'state_version':state['row_version']})}}
    result=await executor.execute(call)
    assert result['status']=='confirmed',result
    repeated=await executor.execute(call)
    assert repeated['status']=='confirmed',repeated
    await db.rollback()
    assert await db.scalar(select(schema.tables['module_two_record'].c.record_status))=='confirmed'
    assert await db.scalar(select(func.count()).select_from(schema.tables['module_two_record']))==1

@pytest.mark.parametrize('stream',[False,True])
async def test_committed_display_survives_main_generation_failure(context,provider,stream):
    from types import SimpleNamespace
    from app.graph.nodes import make_module_node, MODULE_CONFIGS
    # The tool phase has already committed this artifact; the free-text provider fails.
    context.stream=stream
    from app.providers.base import ProviderError
    # Provider adapters expose exhausted request timeouts through this public error.
    provider.fail_with=ProviderError('synthetic timeout, no provider call')
    display='已核对的活动计划：明天散步十分钟。'
    events=[]
    result=await make_module_node('module_2',MODULE_CONFIGS['module_2'])(
        {'user_input':'请展示计划','session_id':'isolated-display','memory':{},'chat_history':[],
         'pa_operation_displays':[display]},SimpleNamespace(context=context),writer=events.append)
    assert result.get('error')
    assert result['final_response'].count(display)==1
    if stream:
        assert ''.join(e.get('text','') for e in events if e.get('type')=='delta')==result['final_response']
