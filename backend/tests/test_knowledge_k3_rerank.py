import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.knowledge_k3_rerank import K3Reranker
from app.providers.base import Completion
from app.retrieval import KnowledgeChunk

HITS = [KnowledgeChunk('kb:1', '原理全文', 'first'), KnowledgeChunk('kb:2', '活动全文', 'second')]


def create(text='{"selected_ids":["kb:2","kb:1"]}', **completion_args):
    settings = Settings(_env_file=None, knowledge_rerank_model='kimi-k3', knowledge_rerank_backend='k3')
    provider = SimpleNamespace(route_detailed=AsyncMock(return_value=Completion(text=text, model='kimi-k3',
        finish_reason='stop', usage={'input_tokens': 100, 'output_tokens': 20}, **completion_args)))
    return K3Reranker(settings, provider), provider


async def test_k3_selection_keeps_exact_source_and_requests_low_effort():
    reranker, provider = create()
    trace = {}
    hits = await reranker.rank('不想跳绳，只想散步', HITS, trace=trace)
    assert [h.id for h in hits] == ['kb:2','kb:1']
    assert hits[0].text == HITS[1].text and hits[0].score_type == 'llm_rank'
    kwargs = provider.route_detailed.call_args.kwargs
    assert kwargs['reasoning_effort'] == 'low' and kwargs['include_reasoning'] is True
    body = json.loads(kwargs['user'])
    assert body['query'] == '不想跳绳，只想散步'
    assert body['documents'][0]['text'] == '原理全文'
    assert trace['rerank_usage'] == {'input_tokens': 100, 'output_tokens': 20}
    assert 'reasoning_content' not in trace


async def test_k3_can_reject_all_candidates():
    reranker, _ = create('{"selected_ids":[]}')
    assert await reranker.rank('量子计算机价格', HITS, trace={}) == []


@pytest.mark.parametrize('text', [
    '{"selected_ids":["kb:999"]}', '{"selected_ids":["kb:1","kb:1"]}',
    '{"selected_ids":[1]}', '{"selected_ids":[],"advice":"new advice"}',
    'not json', '```json\n{"selected_ids":[]}\n```',
])
async def test_malformed_ids_and_extra_fields_are_rejected(text):
    reranker, _ = create(text)
    with pytest.raises(ValueError):
        await reranker.rank('query', HITS, trace={})


async def test_no_truncation_and_no_over_limit_selection():
    reranker, provider = create()
    reranker.settings.knowledge_k3_rerank_max_chars = 10
    with pytest.raises(ValueError, match='no silent truncation'):
        await reranker.rank('query', HITS, trace={})
    provider.route_detailed.assert_not_called()
    reranker.settings.knowledge_k3_rerank_max_chars = 140000
    reranker.settings.knowledge_hybrid_final_results = 1
    with pytest.raises(ValueError, match='excess'):
        await reranker.rank('query', HITS, trace={})


@pytest.mark.parametrize('change', [{'finish_reason':'length'}, {'model':'another-model'}])
async def test_truncated_output_and_model_substitution_fail(change):
    reranker, provider = create()
    provider.route_detailed.return_value = replace(provider.route_detailed.return_value, **change)
    with pytest.raises(ValueError):
        await reranker.rank('query', HITS, trace={})


async def test_timeout_is_bounded():
    reranker, provider = create()
    async def slow(**kwargs):
        await asyncio.sleep(1)
    provider.route_detailed.side_effect = slow
    reranker.settings.knowledge_rerank_timeout_seconds = .01
    with pytest.raises(asyncio.TimeoutError):
        await reranker.rank('query', HITS, trace={})


def test_k3_scores_are_not_cross_encoder_probabilities():
    reranker, provider = create()
    reranker.settings.knowledge_rerank_min_score = .5
    with pytest.raises(ValueError, match='ordinal'):
        K3Reranker(reranker.settings, provider)
