import asyncio
import json
import httpx
import openai
import pytest
from app.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.router_agent import decide_target_module, decide_target_module_with_reasoning
from app.module_router import MODEL

@pytest.mark.asyncio
@pytest.mark.parametrize('legacy,recovery,enabled', [(False,False,False),(True,False,True),(False,True,False)])
async def test_all_router_entries_pin_flash_low_and_record_wire(monkeypatch,legacy,recovery,enabled):
    clients=[];requests=[];original=openai.AsyncOpenAI
    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        truncated=recovery and len(requests)==1
        return httpx.Response(200,headers={'x-request-id':f'req-{len(requests)}'},json={
            'id':'completion-id','object':'chat.completion','created':1,'model':'deepseek-v4-1-flash',
            'choices':[{'index':0,'message':{'role':'assistant','content':'' if truncated else '{"target_module":"2","knowledge_task":"general"}',
             'reasoning_content':'基于用户原话判断。' if len(requests)==1 else ''},'finish_reason':'length' if truncated else 'stop'}],
            'usage':{'prompt_tokens':20,'completion_tokens':15,'total_tokens':35}})
    def make(**kwargs):
        c=original(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)));clients.append(c);return c
    monkeypatch.setattr(openai,'AsyncOpenAI',make)
    source=DeepSeekProvider(Settings(deepseek_api_key='SECRET_KEY',deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3',
        deepseek_model='kimi-k3',deepseek_router_model='kimi-k3',provider_max_retries=0,module_router_reasoning_effort='disabled')).with_thinking(enabled)
    try:
        fn=decide_target_module if legacy else decide_target_module_with_reasoning
        result=await fn(source,current_module='module_2',user_input='我还没开始做，想调整计划',has_pa_card=False,**({} if legacy else {'max_tokens':1024}))
        assert requests[0]['model']==MODEL
        assert requests[0]['reasoning_effort']=='low' and requests[0]['thinking']=={'type':'enabled'}
        assert requests[0]['max_tokens']>=2048
        assert source.model==source._settings.deepseek_router_model=='kimi-k3'
        assert source.thinking_override is enabled
        if not legacy:
            assert result.request_calls[0]['request']=={k:v for k,v in requests[0].items() if k in {'model','messages','max_tokens','thinking','reasoning_effort','temperature'}}
            assert result.request_calls[0]['response']['reasoning_content']=='基于用户原话判断。'
            assert result.request_calls[0]['request_id']=='req-1'
            assert 'SECRET_KEY' not in str(result.request_calls)
            assert result.model=='deepseek-v4-1-flash'
        if recovery:
            assert len(requests)==2 and requests[1]['model']==MODEL
            assert requests[1]['thinking']=={'type':'disabled'} and 'reasoning_effort' not in requests[1]
            assert result.json_recovery['status']=='recovered'
            assert result.request_calls[1]['response']['content'].startswith('{')
        assert clients[1].is_closed() and not clients[0].is_closed()
    finally:await clients[0].close()

@pytest.mark.asyncio
async def test_cancel_keeps_sent_request_and_closes_router(monkeypatch):
    original=openai.AsyncOpenAI;clients=[];entered=asyncio.Event()
    async def handler(request):
        entered.set();await asyncio.sleep(60)
    def make(**kwargs):
        c=original(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)));clients.append(c);return c
    monkeypatch.setattr(openai,'AsyncOpenAI',make)
    source=DeepSeekProvider(Settings(deepseek_api_key='test',deepseek_model='kimi-k3',deepseek_router_model='kimi-k3'))
    calls=[]
    try:
        task=asyncio.create_task(decide_target_module_with_reasoning(source,current_module='module_2',user_input='继续',has_pa_card=False,_request_calls=calls))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert calls[0]['request']['model']==MODEL
        assert calls[0]['response']['error_code']=='router_call_interrupted'
        assert clients[1].is_closed()
    finally:await clients[0].close()
