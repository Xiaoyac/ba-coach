import json
import httpx
import openai
import pytest
from app.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.module_summary import summarize_module, MODEL
from app.ai_telemetry import request_event_scope
from app.execution_status import snapshot

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_summary_uses_flash_without_mutating_shared_router(monkeypatch, failure):
    clients=[];calls=[];original=openai.AsyncOpenAI
    def handler(request):
        calls.append(json.loads(request.content))
        if failure:
            return httpx.Response(503,json={'error':{'message':'temporary failure','type':'server_error'}})
        return httpx.Response(200,json={'id':'summary-test','object':'chat.completion','created':1,
            'model':MODEL,'choices':[{'index':0,'message':{'role':'assistant','content':'用户计划晚饭后散步五分钟。'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':20,'completion_tokens':12,'total_tokens':32}})
    def make(**kwargs):
        client=original(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        clients.append(client);return client
    monkeypatch.setattr(openai,'AsyncOpenAI',make)
    settings=Settings(deepseek_api_key='test-only',deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3',
        deepseek_model='kimi-k3',deepseek_router_model='kimi-k3',provider_max_retries=0)
    source=DeepSeekProvider(settings).with_thinking(True)
    try:
        async with request_event_scope('summary-test',91):
            result=await summarize_module(source,system='只总结已知事实',user='用户计划明天散步，尚未执行。',max_tokens=512)
            assert snapshot('summary-test',91)['stages']['module_summarizer']['model']==MODEL
        assert result.model==MODEL
        assert bool(result.text) is not failure
        assert len(calls)==1 and calls[0]['model']==MODEL
        assert calls[0]['max_tokens']==512
        assert calls[0]['thinking']=={'type':'disabled'}
        assert calls[0]['messages'][-1]['content']=='用户计划明天散步，尚未执行。'
        assert source.model==source._settings.deepseek_router_model=='kimi-k3'
        assert source.thinking_override is True
        assert not clients[0].is_closed() and clients[1].is_closed()
    finally:
        await clients[0].close()
