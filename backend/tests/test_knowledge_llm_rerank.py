from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.config import Settings
from app.knowledge_llm_rerank import ListwiseReranker
from app.providers.base import Completion
from app.retrieval import KnowledgeChunk

HITS = [KnowledgeChunk('kb:1', '原文', 'source')]


def create(text='{"selected_ids":["kb:1"]}'):
    settings = Settings(_env_file=None, knowledge_rerank_backend='llm', knowledge_rerank_model='deepseek-v4.1-flash')
    provider = SimpleNamespace(route_detailed=AsyncMock(return_value=Completion(
        text=text, model='deepseek-v4-1-flash', finish_reason='stop', usage={'input_tokens':100})))
    return ListwiseReranker(settings, provider), provider


async def test_flash_preserves_sources_and_actual_usage_without_extra_thinking():
    ranker, provider = create()
    trace = {}
    hits = await ranker.rank('不想跳绳，只想散步', HITS, trace=trace)
    assert hits[0].text == HITS[0].text
    kwargs = provider.route_detailed.call_args.kwargs
    assert kwargs['include_reasoning'] is False and kwargs['reasoning_effort'] is None
    assert json.loads(kwargs['user'])['query'] == '不想跳绳，只想散步'
    assert trace['rerank_backend'] == 'llm'
    assert trace['rerank_model'] == 'deepseek-v4-1-flash'
    assert trace['rerank_usage']['input_tokens'] == 100


@pytest.mark.parametrize('text', ['{"selected_ids":["unknown"]}', '{"selected_ids":["kb:1","kb:1"]}', '{"selected_ids":[],"advice":"invented"}', 'not JSON'])
async def test_flash_rejects_invalid_evidence(text):
    ranker, _ = create(text)
    with pytest.raises(ValueError):
        await ranker.rank('query', HITS, trace={})


@pytest.mark.parametrize('change', [{'model':'kimi-k3'}, {'model':'deepseek-v4-1-flash-unverified'}, {'finish_reason':'length'}])
async def test_flash_rejects_model_substitution_and_truncation(change):
    ranker, provider = create()
    provider.route_detailed.return_value = replace(provider.route_detailed.return_value, **change)
    with pytest.raises(ValueError):
        await ranker.rank('query', HITS, trace={})


async def test_flash_can_reject_all():
    ranker, _ = create('{"selected_ids":[]}')
    assert await ranker.rank('量子计算机价格', HITS, trace={}) == []


async def test_flash_wire_model_is_isolated_from_k3_main_and_router():
    settings = Settings(_env_file=None, deepseek_api_key='synthetic',
        deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3',
        deepseek_model='kimi-k3', deepseek_router_model='kimi-k3',
        knowledge_rerank_backend='llm', knowledge_rerank_model='deepseek-v4.1-flash')
    ranker = ListwiseReranker(settings)
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':0,
            'model':'deepseek-v4-1-flash','choices':[{'index':0,'finish_reason':'stop',
            'message':{'role':'assistant','content':'{"selected_ids":["kb:1"]}'}}],
            'usage':{'prompt_tokens':30,'completion_tokens':10,'total_tokens':40}})
    import openai
    await ranker.provider._client.close()
    ranker.provider._client = openai.AsyncOpenAI(api_key='synthetic', base_url=settings.deepseek_base_url,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)), max_retries=0)
    try:
        await ranker.rank('query', HITS, trace={})
    finally:
        await ranker.provider._client.close()
    assert captured[0]['model'] == 'deepseek-v4.1-flash'
    assert captured[0]['thinking'] == {'type':'disabled'}
    assert 'reasoning_effort' not in captured[0]
    assert settings.deepseek_model == settings.deepseek_router_model == 'kimi-k3'
