"""Native calls retain the existing owned, sourced, versioned business boundary."""
import json
import pytest
from sqlalchemy import insert, select, delete, func
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.pa_card_tools import PACardTools, finalize_tool_display
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.v2_workflow import runtime_for
from app.pa_tool_loop import PAToolReply
from app.providers.base import StreamDelta
from app.schemas import Message
from test_goal_overview import goal_api
from test_turn_confirmation_0924 import setup_turn
from test_cycle_program_0914 import seed_review


def call(name,args=None):
    return {'id':'call-a','type':'function','function':{'name':name,'arguments':json.dumps(args or {},ensure_ascii=False)}}


def executor(db,module='module_2',user_id='a',boundary=21):
    return PACardTools(maker=async_sessionmaker(db.bind,expire_on_commit=False),session_id='chat-a',
                      user_id=user_id,user_message_id=boundary,module=module)


async def version(tool):
    result=await tool.execute(call('get_pa_card'))
    assert result['status']=='ok',result
    return result['state_version']


async def test_confirm_tool_is_idempotent(goal_api):
    _,db,_=goal_api;await setup_turn(db,'确认，就按这个计划试试。')
    tool=executor(db);command=call('confirm_pa_card',{'state_version':await version(tool)})
    result=await tool.execute(command);assert result['status']=='confirmed',result
    assert (await tool.execute(command))['replayed']
    await db.rollback();_,rt=await runtime_for(db,'chat-a');assert rt['current_module']=='module_3'
    logs=schema.tables['ai_decision_logs']
    assert await db.scalar(select(func.count()).select_from(logs).where(logs.c.decision_type=='pa_native_tool'))==1


@pytest.mark.parametrize('text',['先不确认','可以，但是改成明天','为什么要这样？'])
async def test_tool_request_is_not_user_consent(goal_api,text):
    _,db,_=goal_api;await setup_turn(db,text);tool=executor(db)
    result=await tool.execute(call('confirm_pa_card',{'state_version':await version(tool)}))
    assert result['status']=='blocked',result
    await db.rollback();_,rt=await runtime_for(db,'chat-a');assert rt['current_module']=='module_2'


async def test_wrong_owner_newer_turn_stale_version_invalid_fields(goal_api):
    _,db,_=goal_api;await setup_turn(db,'确认')
    assert (await executor(db,user_id='b').execute(call('get_pa_card')))['status']=='blocked'
    tool=executor(db);v=await version(tool)
    assert (await tool.execute(call('confirm_pa_card',{'state_version':v-1})))['reason']=='state_changed_requery_state'
    assert (await tool.execute(call('execute_sql',{'sql':'DROP TABLE pa_goals'})))['status']=='blocked'
    assert (await tool.execute(call('save_pa_card',{'state_version':v,'data':{'record_status':'confirmed'}})))['status']=='blocked'
    await db.execute(insert(ConversationMessage),{'id':22,'conversation_id':1,'position':3,'role':'user','content':'等等，我要修改'});await db.commit()
    assert (await tool.execute(call('confirm_pa_card',{'state_version':v})))['reason']=='current_user_boundary_changed'


async def test_presented_card_binds_real_message_then_confirms(goal_api):
    _,db,_=goal_api;await setup_turn(db,'请再给我看看计划');tool=executor(db)
    result=await tool.execute(call('present_pa_card',{'state_version':await version(tool)}))
    assert result['status']=='ready_to_display',result
    await db.execute(insert(ConversationMessage),{'id':22,'conversation_id':1,'position':3,'role':'assistant','content':result['display_text']});await db.commit()
    await finalize_tool_display(db,session_id='chat-a',user_id='a',assistant_message_id=22);await db.commit()
    _,rt=await runtime_for(db,'chat-a');marker=rt['memory']['dialogue_draft']
    assert marker['assistant_message_id']==22 and marker['summary_verified']
    await db.execute(insert(ConversationMessage),{'id':23,'conversation_id':1,'position':4,'role':'user','content':'就按这个计划'});await db.commit()
    tool=executor(db,boundary=23)
    result=await tool.execute(call('confirm_pa_card',{'state_version':await version(tool)}))
    assert result['status']=='confirmed',result


async def review_for_tool(db):
    await seed_review(db,decision=1)
    row=dict((await db.execute(select(schema.tables['module_four_record']))).mappings().one())
    contract=row['phase_c']['_m4_contract']
    raw={k:v for k,v in row.items() if k in {'phase_a','phase_b','phase_c','ai_abc_chain_summary','ba_reeducation_content','next_coping_strategy'}}
    raw['m4_contract']={k:v['quote'] for k,v in contract['evidence'].items() if k not in {'decision_quote','review_summary_quote'}}
    raw['m4_contract'].update(emotion_improved=True,chain_status='confirmed',core_questions_resolved=True,difficulty_status='none')
    await db.execute(delete(ConversationMessage).where(ConversationMessage.id.in_([18,20])))
    await db.execute(insert(ConversationMessage),{'id':21,'conversation_id':1,'position':11,'role':'user','content':'我理解了，帮我总结这次经历吧。'});await db.commit()
    return raw


async def test_m4_tool_closes_without_fake_message_or_future_decision(goal_api):
    _,db,_=goal_api;raw=await review_for_tool(db);tool=executor(db,module='module_4')
    result=await tool.execute(call('close_pa_card',{'state_version':await version(tool),'data':raw,'summary':'这次散步完成了，行动后感觉轻松，你理解了先行动再观察状态的经验。'}))
    assert result['status']=='closed',result
    await db.rollback();_,rt=await runtime_for(db,'chat-a');assert rt['current_module']=='module_2' and rt['active_cycle_id'] is None
    assert await db.scalar(select(schema.tables['pa_cycles'].c.status))=='completed'
    review=(await db.execute(select(schema.tables['module_four_record']))).mappings().one()
    assert review['review_decision'] is None
    proof=review['phase_c']['_m4_contract']['evidence']['review_summary_quote']
    assert proof['source']=='assistant_tool' and 'message_id' not in proof
    assert await db.scalar(select(func.count()).select_from(ConversationMessage).where(ConversationMessage.content==result['display_text']))==0


async def test_fabricated_understanding_cannot_close_and_rolls_back(goal_api):
    _,db,_=goal_api;raw=await review_for_tool(db)
    await db.execute(delete(ConversationMessage).where(ConversationMessage.id==16));await db.commit()
    raw['m4_contract']['understanding_quote']='这句话不存在';tool=executor(db,module='module_4')
    result=await tool.execute(call('close_pa_card',{'state_version':await version(tool),'data':raw,'summary':'总结完毕'}))
    assert result['status']=='blocked',result
    await db.rollback();assert await db.scalar(select(schema.tables['pa_cycles'].c.status))!='completed'


async def test_loop_preserves_native_roles_streaming_ids_and_total_usage():
    class Provider:
        name='test';model='test-model'
        def __init__(self):self.requests=[]
        async def stream_tools(self,**kwargs):
            self.requests.append(kwargs)
            if len(self.requests)==1:
                yield StreamDelta(kind='reasoning',text='internal')
                yield StreamDelta(kind='tool_calls',tool_calls=[call('get_pa_card')])
            else:
                assert kwargs['messages'][-1]['role']=='tool'
                assert kwargs['messages'][-2]['reasoning_content']=='internal'
                yield StreamDelta(kind='content',text='这是');yield StreamDelta(kind='content',text='当前计划。')
            yield StreamDelta(kind='usage',request_id=f'request-{len(self.requests)}',usage={'input_tokens':5,'output_tokens':2})
    class Executor:
        definitions=[];trace=[];displays=[]
        async def execute(self,call):return {'status':'ok'}
    p=Provider();telemetry={};loop=PAToolReply(p,Executor(),max_rounds=2,telemetry=telemetry)
    parts=[d async for d in loop.stream(system='policy',messages=[Message(role='user',content='看看计划')])]
    assert [d.text for d in parts if d.kind=='content']==['这是','当前计划。']
    assert parts[-1].usage=={'input_tokens':10,'output_tokens':4}
    assert len(telemetry['pa_tools']['requests'])==2
    assert p.requests[1]['messages'][-1]['tool_call_id']=='call-a'

async def test_native_transport_assembles_fragments_and_uses_response_request_id():
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock
    from app.providers.tool_calling import stream_openai_tools
    from app.config import Settings
    def chunk(args, *, first=False, finish=None):
        return NS(usage=None, choices=[NS(finish_reason=finish,delta=NS(content=None,reasoning_content=None,
            tool_calls=[NS(index=0,id='c1' if first else None,function=NS(name='get_pa_card' if first else None,arguments=args))]))])
    class Stream:
        response=NS(headers={'x-request-id':'provider-real-id'})
        close=AsyncMock()
        def __aiter__(self):
            async def gen():
                yield chunk('{',first=True);yield chunk('}',finish='tool_calls')
            return gen()
    stream=Stream();create=AsyncMock(return_value=stream)
    p=NS(name='deepseek',model='test',thinking_override=False,_settings=Settings(_env_file=None),
         _client=NS(chat=NS(completions=NS(create=create))))
    out=[d async for d in stream_openai_tools(p,system='policy',messages=[{'role':'user','content':'hi'}],tools=[])]
    assert out[0].request_id=='provider-real-id'
    assert out[1].tool_calls[0]['function']['arguments']=='{}'
    stream.close.assert_awaited_once()

async def test_tool_display_not_bound_if_newer_turn_arrived(goal_api):
    _,db,_=goal_api;await setup_turn(db,'看看卡片');tool=executor(db)
    result=await tool.execute(call('present_pa_card',{'state_version':await version(tool)}))
    await db.execute(insert(ConversationMessage),[
        {'id':22,'conversation_id':1,'position':3,'role':'assistant','content':result['display_text']},
        {'id':23,'conversation_id':1,'position':4,'role':'user','content':'不对，改一下'}]);await db.commit()
    await finalize_tool_display(db,session_id='chat-a',user_id='a',assistant_message_id=22)
    _,state=await runtime_for(db,'chat-a')
    assert state['memory']['dialogue_draft']['assistant_message_id']!=22

async def test_tool_commit_rollback_on_receipt_failure(goal_api,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.exc import OperationalError
    _,db,_=goal_api;await setup_turn(db,'确认');tool=executor(db);v=await version(tool)
    original=AsyncSession.commit
    async def fail(session):raise OperationalError('test',{},Exception('unavailable'))
    monkeypatch.setattr(AsyncSession,'commit',fail)
    result=await tool.execute(call('confirm_pa_card',{'state_version':v}))
    assert result['status']=='failed',result
    monkeypatch.setattr(AsyncSession,'commit',original)
    await db.rollback();_,state=await runtime_for(db,'chat-a');assert state['current_module']=='module_2'

async def test_write_survives_followup_provider_failure(goal_api):
    from app.providers.base import ProviderError
    _,db,_=goal_api;await setup_turn(db,'确认');tool=executor(db);v=await version(tool)
    class Provider:
        count=0
        async def stream_tools(self,**kwargs):
            self.count+=1
            if self.count==1:yield StreamDelta(kind='tool_calls',tool_calls=[call('confirm_pa_card',{'state_version':v})])
            else:raise ProviderError('interrupted')
    with pytest.raises(ProviderError):
        async for d in PAToolReply(Provider(),tool,max_rounds=2,telemetry={}).stream(system='policy',messages=[Message(role='user',content='确认')]):pass
    await db.rollback();_,state=await runtime_for(db,'chat-a');assert state['current_module']=='module_3'

async def test_loop_budget_prevents_unbounded_queries():
    class Provider:
        choices=[]
        async def stream_tools(self,**kwargs):
            self.choices.append(kwargs['tool_choice'])
            if kwargs['tool_choice']=='auto':yield StreamDelta(kind='tool_calls',tool_calls=[call('get_pa_card')])
            else:yield StreamDelta(kind='content',text='稍后继续。')
    class Executor:
        definitions=[];trace=[];displays=[]
        async def execute(self,call):return {'status':'blocked'}
    p=Provider();t={}
    _=[d async for d in PAToolReply(p,Executor(),max_rounds=2,telemetry=t).stream(system='',messages=[])]
    assert p.choices==['auto','auto','none'] and t['pa_tools']['budget_exhausted']

async def test_bad_score_parameter_rejected_before_any_write(goal_api):
    _,db,_=goal_api;await setup_turn(db,'改成五分钟，难度3分');tool=executor(db)
    result=await tool.execute(call('save_pa_card',{'state_version':await version(tool),'data':{
        'difficulty_rating':3,'difficulty_evidence':{'rating':{'message_id':21,'quote':'难度3分','score_text':'3分'}}}}))
    assert result['status']=='blocked' and 'invalid_difficulty_source' in result['reason']
    await db.rollback();assert await db.scalar(select(schema.tables['module_two_record'].c.difficulty_rating))==4

async def test_required_decisions_hide_planning_then_stream_final():
    class Provider:
        seen=[]
        async def stream_tools(self,**kwargs):
            self.seen.append(kwargs)
            n=len(self.seen)
            if n<3:
                yield StreamDelta(kind='content',text='internal tool planning')
                yield StreamDelta(kind='tool_calls',tool_calls=[call('get_pa_card' if n==1 else 'continue_pa_conversation')])
            else:
                yield StreamDelta(kind='content',text='这次');yield StreamDelta(kind='content',text='想聊什么？')
    class Executor:
        definitions=[{'function':{'name':n}} for n in ('get_pa_card','continue_pa_conversation')]
        trace=[];displays=[]
        async def execute(self,c):return {'status':'ok' if c['function']['name']=='get_pa_card' else 'continue_conversation'}
    p=Provider();out=[d async for d in PAToolReply(p,Executor(),max_rounds=5,telemetry={}).stream(system='',messages=[])]
    assert ''.join(d.text for d in out if d.kind=='content')=='这次想聊什么？'
    assert [r['tool_choice'] for r in p.seen]==[{'type':'function','function':{'name':'get_pa_card'}},'required','none']

async def test_module_node_commits_via_tool_and_uses_m3_prompt(goal_api,context,monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace as NS
    from app.graph.nodes import make_module_node,ModuleConfig,route_next_module_node,_run_background_routing
    from app.providers.base import as_text
    _,db,_=goal_api;await setup_turn(db,'确认');tool=executor(db);v=await version(tool)
    class Provider:
        name='test';model='test';count=0;systems=[]
        async def stream_tools(self,**kw):
            self.count+=1;self.systems.append(as_text(kw['system']))
            if self.count==1:yield StreamDelta(kind='tool_calls',tool_calls=[call('get_pa_card')])
            elif self.count==2:yield StreamDelta(kind='tool_calls',tool_calls=[call('confirm_pa_card',{'state_version':v})])
            else:
                assert 'M3 effective prompt' in as_text(kw['system'])
                assert 'M2 effective prompt' not in as_text(kw['system'])
                yield StreamDelta(kind='content',text='安排已经确认。')
    p=Provider();ctx=replace(context,provider=p,router_provider=None,sessionmaker=tool.maker,
        settings=context.settings.model_copy(update={'database_schema_version':'v2','pa_card_tools_enabled':True,'knowledge_mediator_enabled':False}),
        prompt_snapshot={'global':'policy','module_2':'M2 effective prompt','module_3':'M3 effective prompt'},stream=True)
    state={'session_id':'chat-a','subject_id':'a','user_message_id':21,'user_input':'确认','memory':{},'extracted_intent':'module_2'}
    result=await make_module_node('module_2',ModuleConfig(retrieve=False))(state,NS(context=ctx),writer=lambda e:None)
    assert result.get('error') is None,result
    assert result['pa_tools_used'] and result['next_module']=='module_3'
    routing=await route_next_module_node({**state,**result},NS(context=ctx),writer=lambda e:None)
    assert not routing['routing_pending'] and routing['next_module']=='module_3'
    # The old background extractor is not allowed to rewrite a tool turn.
    await _run_background_routing({**state,**result},ctx,assistant_message_id=999)

async def test_pre_router_cannot_precommit_pa_tool_turn(goal_api,context,provider,monkeypatch):
    from dataclasses import replace
    from app.pre_reply_routing import route_before_reply
    _,db,_=goal_api;await setup_turn(db,'确认')
    ctx=replace(context,sessionmaker=async_sessionmaker(db.bind,expire_on_commit=False),
        settings=context.settings.model_copy(update={'database_schema_version':'v2','pa_card_tools_enabled':True}))
    provider.route_result='3'
    result=await route_before_reply({'session_id':'chat-a','subject_id':'a','user_message_id':21,
        'user_input':'确认','current_module':'module_2'},ctx)
    assert result['extracted_intent']=='module_2'
    await db.rollback();_,state=await runtime_for(db,'chat-a');assert state['current_module']=='module_2'

async def test_new_card_source_validation_and_idempotent_create(goal_api):
    from sqlalchemy import update
    _,db,_=goal_api
    text='我选晚饭后散步十分钟，每天一次，难度4分，下雨就在室内走。'
    await db.execute(update(schema.tables['conversation_runtime_states']).where(schema.tables['conversation_runtime_states'].c.conversation_id==1).values(current_module='module_2'))
    await db.execute(insert(ConversationMessage),{'id':21,'conversation_id':1,'position':1,'role':'user','content':text});await db.commit()
    tool=executor(db);v=await version(tool)
    data={'target_activity_content':'散步十分钟','schedule_text':'晚饭后','target_activity_duration_minutes':10,
          'difficulty_rating':4,'difficulty_evidence':{'rating':{'message_id':21,'quote':'难度4分','score_text':'4'}},
          'potential_barriers':['下雨'],'barrier_coping_plan':[{'barrier':'下雨','plan':'室内走'}],
          'goal_proposal':{'selection_status':'selected','selection_role':'core','goal_kind':'primary',
              'selection_message_id':21,'selection_quote':text,'activity_quote':'散步十分钟'}}
    forged=json.loads(json.dumps(data));forged['goal_proposal']['selection_message_id']=999
    bad=await tool.execute(call('save_pa_card',{'state_version':v,'data':forged}));assert bad['status']=='blocked'
    command=call('save_pa_card',{'state_version':v,'data':data})
    result=await tool.execute(command);assert result['status']=='draft_saved',result
    assert result['saved_draft']['difficulty_rating']==4
    assert (await tool.execute(command))['replayed']
    await db.rollback();assert await db.scalar(select(func.count()).select_from(schema.tables['module_two_record']))==1

@pytest.mark.parametrize('choice',['replace','additional'])
async def test_explicit_core_choice_preserves_old_plan(goal_api,choice):
    from sqlalchemy import update
    _,db,_=goal_api;await setup_turn(db,'确认')
    tool=executor(db);assert (await tool.execute(call('confirm_pa_card',{'state_version':await version(tool)})))['status']=='confirmed'
    await db.execute(insert(schema.tables['pa_goal_details']),{'goal_id':'g1','goal_kind':'primary'});await db.commit()
    text='我要用游泳替换原来的核心散步目标。' if choice=='replace' else '保留原核心散步，额外增加游泳。'
    await db.execute(update(schema.tables['conversation_runtime_states']).where(schema.tables['conversation_runtime_states'].c.conversation_id==1).values(current_module='module_2',flow_status='active'))
    await db.execute(insert(ConversationMessage),{'id':22,'conversation_id':1,'position':3,'role':'user','content':text});await db.commit()
    tool=executor(db,boundary=22)
    data={'target_activity_content':'游泳','core_goal_choice':{'action':choice,'goal_id':'g1','message_id':22,'quote':text},
       'goal_proposal':{'selection_status':'selected','selection_role':'core' if choice=='replace' else 'secondary',
         'goal_kind':'primary' if choice=='replace' else 'secondary','selection_message_id':22,'selection_quote':text,'activity_quote':'游泳'}}
    if choice=='additional':data['m2_activity_context']={'secondary_activities':[{'activity_content':'游泳','message_id':22,'quote':text}]}
    result=await tool.execute(call('save_pa_card',{'state_version':await version(tool),'data':data}))
    assert result['status']==('draft_saved' if choice=='replace' else 'saved_activity_context'),result
    await db.rollback()
    old=(await db.execute(select(schema.tables['module_two_record']).where(schema.tables['module_two_record'].c.id=='m2-draft'))).mappings().one()
    assert old['record_status']=='confirmed' and old['duration_minutes']==10
    _,rt=await runtime_for(db,'chat-a');assert (rt['active_goal_id']=='g1')==(choice=='additional')

async def test_native_request_ids_are_projected_without_tool_payloads(goal_api):
    from app.models import Conversation,AIExecutionEvent
    from app.request_diagnostics import requests_for_owned_messages
    _,db,_=goal_api
    await db.run_sync(lambda s:AIExecutionEvent.__table__.create(s.connection(),checkfirst=True))
    db.add_all([ConversationMessage(id=40,conversation_id=1,position=0,role='user',content='查看卡片'),
                ConversationMessage(id=41,conversation_id=1,position=1,role='assistant',content='卡片内容')]);await db.flush()
    db.add(AIExecutionEvent(conversation_id=1,subject_id='a',session_id='chat-a',assistant_message_id=41,
        stage='main_generation',provider='deepseek',model_name='test',event_metadata={'pa_tools':{'requests':[
            {'request_id':'native-query','duration_ms':11,'input_messages':'PRIVATE'},
            {'request_id':'native-write','duration_ms':22,'input_messages':'PRIVATE'},
            {'request_id':'native-reply','duration_ms':33,'input_messages':'PRIVATE'}]}}));await db.commit()
    conversation=await db.get(Conversation,1);reply=await db.get(ConversationMessage,41)
    rows=(await requests_for_owned_messages(db,owned_conversation=conversation,messages=[reply]))[41]
    assert [r.request_id for r in rows]==['native-query','native-write','native-reply']
    assert [r.stage for r in rows]==['main_generation','pa_tool_continuation','pa_tool_continuation']
    assert 'PRIVATE' not in str(rows)

async def test_total_budget_bounds_tool_model_calls():
    import asyncio
    from app.providers.base import ProviderError
    class Provider:
        async def stream_tools(self,**kw):
            await asyncio.sleep(1)
            yield StreamDelta(kind='content',text='late')
    class Executor:
        definitions=[];trace=[];displays=[]
    telemetry={}
    with pytest.raises(ProviderError,match='timed out'):
        _=[d async for d in PAToolReply(Provider(),Executor(),max_rounds=5,telemetry=telemetry,timeout_seconds=.01).stream(system='',messages=[])]
    assert telemetry['pa_tools']['error']=='total_timeout'

async def test_m4_accepts_queried_evidence_objects_only_with_correct_role(goal_api):
    _,db,_=goal_api;raw=await review_for_tool(db);tool=executor(db,module='module_4')
    snapshot=await tool.execute(call('get_pa_card'))
    source=snapshot['draft']['phase_c']['_m4_contract']['evidence']
    for k in raw['m4_contract']:
        if k.endswith('_quote') and k in source:raw['m4_contract'][k]=source[k]
    forged=json.loads(json.dumps(raw));forged['m4_contract']['confirmation_quote']=source['summary_quote']
    bad=await tool.execute(call('close_pa_card',{'state_version':snapshot['state_version'],'data':forged,'summary':'散步完成了。'}))
    assert bad['status']=='blocked' and 'invalid_review_source' in bad['reason']
    result=await tool.execute(call('close_pa_card',{'state_version':snapshot['state_version'],'data':raw,'summary':'这次散步完成，感觉轻松，也理解了先行动再观察感受。'}))
    assert result['status']=='closed',result
