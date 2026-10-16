"""Behavioral checks for round protection, failure recovery and scoped transport."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app.config import get_settings
from app.context_epochs import load_epoch_history, tail_start, state_entries, state_delta
from app.context_pipeline import prepare_context
from app.history_compressor import HistoryCompressor
from app.models import ConversationContextCheckpoint
from app.prompts import build_system_segments
from app.providers.base import Completion, ProviderError
from app.providers.deepseek import DeepSeekProvider
from test_main_prefix_cache import epoch_db, wire


def test_recent_round_is_never_partly_summarized_even_when_oversized():
    rows = [SimpleNamespace(role=role, content=text) for role, text in [
        ('assistant', 'welcome'), ('user', 'old'), ('assistant', 'old reply'),
        ('user', '关键纠正' * 200), ('assistant', 'ack')]]
    assert tail_start(rows, 50) == 3
    assert tail_start(rows[:1], 1) == 0
    assert tail_start(rows, 50000) == 0
    assert tail_start([], 20) == 0


async def load(db, rows, provider, **kw):
    return await load_epoch_history(db, subject_id='owner', session_id='epoch-owned',
        user_message_id=rows[-1].id, provider=provider, token_budget=1000,
        retain_tokens=300, **kw)


async def test_large_compression_budget_avoids_repeated_summarization(epoch_db):
    db, rows = epoch_db
    p = SimpleNamespace(route_detailed=AsyncMock(return_value=Completion(
        text='事实摘要', model='stub', finish_reason='stop')))
    result = await load(db, rows, p)
    assert p.route_detailed.await_count == 1
    source = json.loads(p.route_detailed.call_args.kwargs['user'])
    assert source['messages'][-1]['role'] == 'assistant'
    assert rows[-1].id not in [m['id'] for m in source['messages']]
    assert result.messages[0].role == 'user'


async def test_latest_oversized_round_and_current_message_stay_exact(epoch_db):
    db, rows = epoch_db
    rows[-3].content = '最新纠正不能丢' * 800
    await db.flush()
    p = SimpleNamespace(route_detailed=AsyncMock(return_value=Completion(
        text='较早事实', model='stub', finish_reason='stop')))
    result = await load(db, rows, p)
    assert [m.content for m in result.messages] == [rows[-3].content, rows[-2].content]
    assert result.metrics['history_over_budget']
    source = json.loads(p.route_detailed.call_args.kwargs['user'])
    assert rows[-3].id not in [m['id'] for m in source['messages']]
    prepared = prepare_context(system=[], history=result.messages,
        user_input=rows[-1].content, max_history_messages=len(result.messages),
        prefix_cache=True, history_summary=result.summary)
    assert prepared.messages[-1].source_content == rows[-1].content


@pytest.mark.parametrize('failure', ['empty', 'length', 'oversized', 'exception', 'timeout'])
async def test_failed_compaction_preserves_every_original_message(epoch_db, failure):
    db, rows = epoch_db
    result = Completion(text='partial', model='stub', finish_reason='stop',
                        usage={'input_tokens': 123}, request_id='failed-request')
    if failure == 'empty': result.text = ''
    if failure == 'length': result.finish_reason = 'length'
    if failure == 'oversized': result.text = '超长' * 500
    p = SimpleNamespace(name='stub', route_detailed=AsyncMock(return_value=result))
    if failure == 'exception': p.route_detailed.side_effect = ProviderError('failure')
    if failure == 'timeout': p.route_detailed.side_effect = TimeoutError()
    epoch = await load(db, rows, p)
    await db.commit()
    assert epoch.summary == ''
    assert [m.content for m in epoch.messages] == [r.content for r in rows[:-1]]
    assert epoch.metrics['compaction_deferred']
    assert epoch.metrics['compaction_requests'][0]['error_code']
    assert await db.get(ConversationContextCheckpoint, rows[0].conversation_id) is None


async def test_failure_without_headroom_does_not_silently_truncate(epoch_db):
    db, rows = epoch_db
    p = SimpleNamespace(route_detailed=AsyncMock(side_effect=ProviderError('failure')))
    with pytest.raises(ProviderError, match='no_headroom'):
        await load(db, rows, p, input_token_budget=1500)
    assert await db.get(ConversationContextCheckpoint, rows[0].conversation_id) is None


async def test_second_batch_failure_rolls_back_first_summary(epoch_db):
    db, rows = epoch_db
    from app.context_epochs import source_digest
    checkpoint = ConversationContextCheckpoint(conversation_id=rows[0].conversation_id,
        through_message_id=rows[1].id, source_digest=source_digest(rows[:2]),
        summary='已验证旧摘要', state_baseline={'old': 'unchanged'})
    db.add(checkpoint); await db.commit()
    # A second-call failure is tested with a deliberately low batch allowance;
    # no-headroom failure must leave the old checkpoint unchanged as well.
    p = SimpleNamespace(route_detailed=AsyncMock(side_effect=[
        Completion(text='未提交的新摘要', model='stub', finish_reason='stop'),
        ProviderError('second batch failed')]))
    with pytest.raises(ProviderError, match='no_headroom'):
        await load(db, rows, p, input_token_budget=2500)
    assert p.route_detailed.await_count == 2
    assert checkpoint.summary == '已验证旧摘要'
    assert checkpoint.through_message_id == rows[1].id
    assert checkpoint.state_baseline == {'old': 'unchanged'}


async def test_cancellation_is_not_swallowed_as_a_fallback(epoch_db):
    db, rows = epoch_db
    p = SimpleNamespace(route_detailed=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError): await load(db, rows, p)
    assert await db.get(ConversationContextCheckpoint, rows[0].conversation_id) is None


def test_retrieval_and_runtime_data_never_enter_frozen_baseline():
    def build(memory, state):
        return build_system_segments('module_1', global_prompt='fixed', module_prompt='module',
            profile_context=['偏好安静'], long_term_memory=[memory], metadata={'phase': state})
    first = build('本轮检索资料A', 'runtimeA')
    baseline = state_entries(first)
    assert '偏好安静' in str(baseline)
    assert '检索资料A' not in str(baseline) and 'runtimeA' not in str(baseline)
    second = state_delta(build('本轮检索资料B', 'runtimeB'), baseline)
    prepared = prepare_context(system=second, history=[], user_input='hi',
        max_history_messages=0, prefix_cache=True)
    payload = wire(prepared)
    assert '资料A' not in str(payload) and 'runtimeA' not in str(payload)
    assert '资料B' not in payload[0]['content']
    assert '资料B' in payload[-2]['content']


async def test_compression_wire_effort_model_budget_are_independent(monkeypatch):
    cfg = get_settings().model_copy(update={'deepseek_api_key': 'offline',
        'deepseek_base_url': 'https://ark.cn-beijing.volces.com/api/coding/v3',
        'deepseek_model': 'ordinary-main', 'deepseek_router_model': 'ordinary-router',
        'main_history_compression_model': 'kimi-k3',
        'main_history_compression_provider': 'deepseek',
        'main_history_compression_effort': 'max',
        'main_history_compression_max_tokens': 8192,
        'main_history_compression_timeout_seconds': 90})
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, headers={'x-request-id': 'compression-id'}, json={
            'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 1, 'model': 'kimi-k3',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '摘要'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 300, 'completion_tokens': 100, 'total_tokens': 400,
                      'completion_tokens_details': {'reasoning_tokens': 70}}})
    p = DeepSeekProvider(cfg).with_thinking(False)
    await p._client.close()
    p._client = openai.AsyncOpenAI(api_key='offline', base_url=cfg.deepseek_base_url,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    def resolve(name):
        assert name == 'deepseek'
        return p
    monkeypatch.setattr('app.providers.get_provider', resolve)
    try:
        compressor = HistoryCompressor(p, cfg)
        result = await compressor.route_detailed(system='summary policy', user='history')
        assert captured[0]['model'] == 'kimi-k3'
        assert captured[0]['reasoning_effort'] == 'max'
        assert captured[0]['max_tokens'] == 8192
        assert result.usage['reasoning_tokens'] == 70
        assert result.request_id == 'compression-id'
        assert p.model == 'ordinary-main' and p.thinking_override is False
        assert p._settings.deepseek_router_model == 'ordinary-router'
        assert compressor._provider._settings.background_model_timeout_seconds == 90
        assert compressor._provider._client.timeout == 90
        assert compressor._provider._client.max_retries == 0
    finally: await p._client.close()


async def test_main_node_continues_and_records_failed_compaction(epoch_db, context, provider):
    from dataclasses import replace
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from langgraph.runtime import Runtime
    from app.graph.nodes import make_module_node, ModuleConfig
    from app.models import AIExecutionEvent
    db, rows = epoch_db
    provider.supports_tail_system = True
    scoped = replace(context, sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        prompt_snapshot={'global': 'global', 'module_1': 'module'},
        settings=context.settings.model_copy(update={'main_history_token_budget': 4000}))
    result = await make_module_node('module_1', ModuleConfig(retrieve=False))(
        {'session_id': 'epoch-owned', 'subject_id': 'owner', 'user_message_id': rows[-1].id,
         'user_input': '当前问题', 'chat_history': [], 'memory': {}, 'routing_mode': 'router_only',
         'current_module': 'module_1', 'extracted_intent': 'module_1'},
        Runtime(context=scoped), writer=lambda _: None)
    assert result['error'] is None
    assert result['telemetry']['context_epoch']['compaction_deferred']
    assert len(provider.seen[-1]) == 101
    event = (await db.execute(select(AIExecutionEvent).where(
        AIExecutionEvent.stage == 'context_compaction'))).scalar_one()
    assert event.error_code == 'ProviderError'
    assert event.event_metadata['deferred'] is True
