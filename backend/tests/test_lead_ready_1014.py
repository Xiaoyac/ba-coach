import asyncio
from types import SimpleNamespace
import json
import httpx
import pytest
from app.provisional_reply import take_ready_lead, with_natural_lead, generate_reply_lead, select_lead_provider
from app.providers.base import Completion
from app.providers.deepseek import DeepSeekProvider
from app.config import Settings

pytestmark=pytest.mark.asyncio
LEAD='你试着做了些调整。'

class Lead:
    model=name='stub'
    def with_thinking(self,enabled):assert enabled is False;return self
    async def complete(self,**kw):return Completion(text=LEAD,model='stub',finish_reason='stop')

async def run(events,ctx,provider,metrics):
    return [e async for e in with_natural_lead(events,provider=provider,user_input='试着散步',generation_id='g',session_id='s',subject_id=None,maker=None,metrics=metrics,context=ctx)]

def visible(events):return ''.join(e.get('text','') for mode,e in events if mode=='custom' and e.get('type')=='delta')

async def test_main_ready_cancels_unfinished_lead_without_waiting_and_no_late_prefix():
    started=asyncio.Event();cancelled=asyncio.Event();metrics={}
    class Slow(Lead):
        async def complete(self,**kw):
            started.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                # Even a transport which returns after cancellation cannot insert it.
                return Completion(text=LEAD,model='stub',finish_reason='stop')
    ctx=SimpleNamespace(settings=SimpleNamespace(reply_lead_timeout_seconds=12))
    async def events():
        yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
        await started.wait()
        assert take_ready_lead(ctx)==''
        assert ctx.reply_lead_task.done()
        yield ('custom',{'type':'delta','text':'主回复'})
    output=await asyncio.wait_for(run(events(),ctx,Slow(),metrics),.5)
    assert cancelled.is_set() and visible(output)=='主回复'
    assert metrics['reason_code']=='main_ready_before_lead' and not metrics['displayed']
    assert not any(e.get('waiting') for _,e in output)

async def test_completed_lead_is_reused_and_body_flushes_slow_display():
    metrics={};ctx=SimpleNamespace(settings=SimpleNamespace(reply_lead_character_seconds=10))
    async def events():
        yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
        await ctx.reply_lead_task
        assert take_ready_lead(ctx)==LEAD
        yield ('custom',{'type':'delta','text':'正文'})
    output=await asyncio.wait_for(run(events(),ctx,Lead(),metrics),.5)
    assert visible(output)==LEAD+'\n\n正文'

async def test_finished_task_before_handoff_delivery_is_not_discarded(monkeypatch):
    from app import provisional_reply as m
    finished=asyncio.Event();ctx=SimpleNamespace(settings=SimpleNamespace());metrics={}
    async def fast(*a,**kw):
        finished.set()
        return {'text':LEAD,'model':'stub','status':'completed'}
    monkeypatch.setattr(m,'generate_reply_lead',fast)
    async def events():
        yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
        await finished.wait()
        # fast is done even if the wrapper has not delivered its result yet.
        assert take_ready_lead(ctx)==LEAD
        yield ('custom',{'type':'delta','text':'正文'})
    assert visible(await run(events(),ctx,Lead(),metrics))==LEAD+'\n\n正文'

async def test_no_late_start_when_main_wins_before_lead_dispatch():
    ctx=SimpleNamespace(settings=SimpleNamespace());metrics={}
    class Forbidden(Lead):
        async def complete(self,**kw):raise AssertionError('late lead started')
    async def events():
        assert take_ready_lead(ctx)==''
        yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
        yield ('custom',{'type':'delta','text':'正文'})
    assert visible(await run(events(),ctx,Forbidden(),metrics))=='正文'
    assert metrics['reason_code']=='main_ready_before_lead'

async def test_flash_lead_wire_disables_thinking_without_changing_main(monkeypatch):
    s=Settings(_env_file=None,deepseek_api_key='test',deepseek_model='kimi-k3',deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3')
    original=DeepSeekProvider(s); main=original.with_deep_reply('low');wire=[]
    selected=select_lead_provider(main,s)
    await selected._client.close()
    from openai import AsyncOpenAI
    def handle(request):
        wire.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'test','object':'chat.completion','created':1,'model':'deepseek-v4-1-flash','choices':[{'index':0,'message':{'role':'assistant','content':LEAD},'finish_reason':'stop'}],'usage':{'prompt_tokens':3,'completion_tokens':3,'total_tokens':6}})
    selected._client=AsyncOpenAI(api_key='test',base_url=s.deepseek_base_url,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('app.provisional_reply.select_lead_provider',lambda *a:selected)
    try:
        result=await generate_reply_lead(main,user_input='刚才去散步',history=[],settings=s)
        assert result['text']==LEAD and result['thinking_enabled'] is False
        assert wire[0]['model']=='deepseek-v4-1-flash-261017'
        assert wire[0]['thinking']=={'type':'disabled'}
        assert 'reasoning_effort' not in wire[0] and 'thinking_budget' not in wire[0]
        assert main.model=='kimi-k3' and main.deep_reply_enabled and main.reply_effort=='low'
        assert main._main_thinking_options([])=={'reasoning_effort':'low'}
    finally:await original._client.close()

async def test_real_graph_starts_main_without_waiting_for_slow_lead(context,provider,monkeypatch):
    provider.risk_result = '{"risk_status":0}'
    import dataclasses
    from app.graph import get_graph
    from app.providers.base import StreamDelta,as_text
    cancelled=asyncio.Event();entered=asyncio.Event()
    async def slow(**kw):
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    async def main(*,system,messages):
        assert '本轮回答的续写' not in as_text(system)
        entered.set()
        yield StreamDelta(kind='content',text='独立完整回答。')
        yield StreamDelta(kind='usage',finish_reason='stop')
    monkeypatch.setattr(provider,'complete',slow);monkeypatch.setattr(provider,'stream',main)
    ctx=dataclasses.replace(context,stream=True)
    state={'user_input':'昨天没散步','forced_module':'module_1','telemetry':{'reply_mode':'ack_deep'}}
    metrics={}
    output=await asyncio.wait_for(run(get_graph().astream(state,context=ctx,stream_mode=['custom','values']),ctx,provider,metrics),2)
    assert entered.is_set() and cancelled.is_set()
    assert visible(output)=='独立完整回答。' and metrics['reason_code']=='main_ready_before_lead'

async def test_http_slow_lead_skipped_and_only_main_reply_saved(client,auth_headers,provider,monkeypatch):
    from app.providers.base import StreamDelta,as_text
    async def slow(**kw):await asyncio.Event().wait()
    async def main(*,system,messages):
        assert '本轮回答的续写' not in as_text(system)
        yield StreamDelta(kind='content',text='主模型独立完整回复。')
        yield StreamDelta(kind='usage',finish_reason='stop')
    monkeypatch.setattr(provider,'complete',slow);monkeypatch.setattr(provider,'stream',main)
    sid=client.post('/api/conversations/current',headers=auth_headers).json()['session_id']
    res=client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'昨天没散步'})
    assert res.status_code==200 and 'event: persisted' in res.text
    frames=[]
    for block in res.text.split('\n\n'):
        if block.startswith('event: delta\n'):frames.append(json.loads(block.split('data: ',1)[1]))
    assert ''.join(f['text'] for f in frames)=='主模型独立完整回复。'
    assert not any(f.get('phase')=='lead' for f in frames)
    saved=client.get('/api/conversations/'+sid,headers=auth_headers).json()
    assert saved['messages'][-1]['content']=='主模型独立完整回复。'
