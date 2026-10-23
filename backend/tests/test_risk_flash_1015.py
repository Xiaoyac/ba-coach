import dataclasses
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from app.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.risk_provider import risk_provider


@pytest.mark.parametrize('flagged', [0, 1])
async def test_risk_flash_wire_and_graph_branch(context, monkeypatch, flagged):
    from app.graph.nodes import risk_gate_node, route_after_risk
    settings = Settings(_env_file=None, deepseek_api_key='test',
        deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3',
        deepseek_model='kimi-k3', deepseek_router_model='kimi-k3')
    router = DeepSeekProvider(settings).with_thinking(True)
    captured, clients = [], []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'id':'risk-test','object':'chat.completion','created':1,
            'model':'deepseek-v4-1-flash', 'choices':[{'index':0,'message':{'role':'assistant',
                'content':json.dumps({'risk_status':flagged,'risk_expression_type':2 if flagged else None})},
                'finish_reason':'stop'}], 'usage':{'prompt_tokens':20,'completion_tokens':12,'total_tokens':32}})
    def build(scoped):
        candidate = DeepSeekProvider.__new__(DeepSeekProvider)
        candidate._settings = scoped
        candidate._client = AsyncOpenAI(api_key='test', base_url=settings.deepseek_base_url,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        clients.append(candidate._client)
        return candidate
    monkeypatch.setattr('app.risk_provider.DeepSeekProvider', build)
    ctx = dataclasses.replace(context, settings=settings, router_provider=router, sessionmaker=None)
    try:
        result = await risk_gate_node({'user_input':'合成测试文本'}, SimpleNamespace(context=ctx))
        assert bool(result['risk']) is bool(flagged)
        assert result['telemetry']['risk_gate_status'] == ('blocked' if flagged else 'passed')
        assert route_after_risk(result) == ('crisis' if flagged else 'module_1')
        assert captured[0]['model'] == 'deepseek-v4-1-flash-261017'
        assert captured[0]['thinking'] == {'type':'disabled'}
        assert captured[0]['max_tokens'] == 256
        assert 'reasoning_effort' not in captured[0]
        assert 'enable_thinking' not in captured[0]
        assert clients[0].is_closed()
        assert router._settings.deepseek_router_model == 'kimi-k3'
        assert router.thinking_override is True
    finally:
        await router._client.close()


async def test_risk_client_closes_on_failure():
    settings = Settings(_env_file=None, deepseek_api_key='test')
    router = DeepSeekProvider(settings)
    try:
        with pytest.raises(RuntimeError):
            async with risk_provider(router, settings) as screener:
                assert screener.thinking_override is False
                raise RuntimeError('cancelled screen')
        assert screener._client.is_closed()
        assert not router._client.is_closed()
    finally:
        await router._client.close()


@pytest.mark.parametrize('raw,text,finish', [({}, '', None), ({}, 'not-json', 'stop'),
    ({'risk_status':0}, '{"risk_status":0}', 'length')])
async def test_unusable_risk_result_does_not_authorize_a_lead(context, monkeypatch, raw, text, finish):
    from app.graph import nodes
    from app.providers.base import Completion
    async def assess(*args, **kwargs):
        return raw, Completion(text=text, model='stub', finish_reason=finish)
    monkeypatch.setattr(nodes, 'assess_risk_detailed', assess)
    ctx = dataclasses.replace(context, settings=context.settings.model_copy(update={'risk_gate_enabled':True}))
    result = await nodes.risk_gate_node({'user_input':'测试'}, SimpleNamespace(context=ctx))
    assert result['telemetry']['risk_gate_status'] == 'failed'


async def test_failed_risk_check_does_not_authorize_a_lead(context, monkeypatch):
    from app.graph import nodes
    async def fail(*args, **kwargs): raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(nodes, 'assess_risk_detailed', fail)
    ctx = dataclasses.replace(context, settings=context.settings.model_copy(update={'risk_gate_enabled':True}))
    result = await nodes.risk_gate_node({'user_input':'测试'}, SimpleNamespace(context=ctx))
    assert result['telemetry']['risk_gate_status'] == 'failed'
