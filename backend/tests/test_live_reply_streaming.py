"""Visible content is released before generation ends, including V2 users."""
import asyncio
from dataclasses import replace

import pytest
from langgraph.runtime import Runtime

from app.graph import nodes
from app.providers.base import ProviderError, StreamDelta


@pytest.mark.parametrize('validator_enabled', [False, True])
@pytest.mark.parametrize('schema', ['v1', 'v2'])
async def test_first_delta_arrives_before_provider_finishes(
        context, provider, monkeypatch, validator_enabled, schema):
    first_visible = asyncio.Event()
    events = []

    def emit(event):
        events.append(event)
        if event['type'] == 'delta':
            first_visible.set()

    async def stream(**kwargs):
        yield StreamDelta(kind='content', text='先显示这句话。')
        # This cannot complete if the node buffers until provider EOF.
        await asyncio.wait_for(first_visible.wait(), timeout=0.5)
        yield StreamDelta(kind='content', text='然后继续显示。')

    async def authority(*args):
        return {'available': True, 'current_module': 'module_1', 'plan_confirmed': False}

    monkeypatch.setattr(nodes, '_emit', emit)
    monkeypatch.setattr(provider, 'stream', stream)
    monkeypatch.setattr('app.reply_workflow.read_reply_workflow', authority)
    ctx = replace(context, stream=True, sessionmaker=None,
                  settings=context.settings.model_copy(update={
                      'database_schema_version': schema,
                      'answer_validator_enabled': validator_enabled}))
    node = nodes.make_module_node('module_1', nodes.ModuleConfig(retrieve=False))
    result = await node({'user_input': '你好', 'subject_id': 'synthetic',
                         'session_id': 'stream-test'}, Runtime(context=ctx))
    assert result['final_response'] == '先显示这句话。然后继续显示。'
    assert ''.join(e['text'] for e in events if e['type'] == 'delta') == result['final_response']
    assert not result['error']


@pytest.mark.parametrize('failure', ['disconnect', 'late_protocol'])
async def test_partial_stream_is_never_replaced_by_receipt_or_recovery(
        context, provider, monkeypatch, failure):
    events = []
    monkeypatch.setattr(nodes, '_emit', events.append)
    prefix = '已经显示的一部分。'

    async def stream(**kwargs):
        yield StreamDelta(kind='content', text=prefix)
        if failure == 'disconnect':
            raise ProviderError('synthetic connection lost')
        yield StreamDelta(kind='content', text='<tool_call>{"name":"invalid"}</tool_call>')

    monkeypatch.setattr(provider, 'stream', stream)
    node = nodes.make_module_node('module_1', nodes.ModuleConfig(retrieve=False))
    result = await node({'user_input': '你好', 'session_id': 'stream-test',
                         'confirmation_receipt': {'module': 'module_1'}},
                        Runtime(context=replace(context, stream=True)))
    assert result['error']
    assert result['final_response'] == prefix
    assert ''.join(e['text'] for e in events if e['type'] == 'delta') == prefix
    assert 'reply_recovery' not in result['telemetry']
    assert 'confirmation_receipt_recovery' not in result['telemetry']


async def test_real_graph_custom_stream_releases_first_delta_before_eof(context, provider, monkeypatch):
    from app.graph import get_graph
    received = asyncio.Event()

    async def stream(**kwargs):
        yield StreamDelta(kind='content', text='第一段。')
        await asyncio.wait_for(received.wait(), timeout=1)
        yield StreamDelta(kind='content', text='第二段。')

    monkeypatch.setattr(provider, 'stream', stream)
    events, final = [], None
    async for kind, value in get_graph().astream(
            {'user_input': '你好', 'forced_module': 'module_1'},
            context=replace(context, stream=True), stream_mode=['custom', 'values']):
        if kind == 'custom':
            events.append(value)
            if value.get('type') == 'delta':
                received.set()
        else:
            final = value
    assert final['final_response'] == '第一段。第二段。'
    assert ''.join(e['text'] for e in events if e['type'] == 'delta') == final['final_response']
