import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import Mock

import pytest

from app.generation_control import GenerationRegistry, GenerationStopped
from app.providers.base import StreamDelta
from test_chat import sse_events


@pytest.mark.asyncio
async def test_cancel_closes_work_before_acknowledgement():
    registry = GenerationRegistry()
    entry = registry.begin("owner", "turn")
    started = asyncio.Event()
    closed = asyncio.Event()
    async def work():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
    async def request():
        try:
            with pytest.raises(GenerationStopped):
                await entry.run(work())
        finally:
            registry.finish(entry)
    task = asyncio.create_task(request())
    await started.wait()
    assert await registry.cancel("owner", "turn") == "cancelled"
    assert closed.is_set()
    await task


@pytest.mark.asyncio
async def test_pre_cancel_is_owner_and_turn_scoped():
    registry = GenerationRegistry()
    assert await registry.cancel("alice", "turn") == "cancelled"
    other = registry.begin("bob", "turn")
    assert not other.stop.is_set()
    next_turn = registry.begin("alice", "next")
    assert not next_turn.stop.is_set()
    stopped = registry.begin("alice", "turn")
    with pytest.raises(GenerationStopped):
        await stopped.run(asyncio.sleep(1))
    registry.finish(stopped)
    assert await registry.cancel("alice", "turn") == "cancelled"


@pytest.mark.asyncio
async def test_stop_cannot_interrupt_finalization_or_completed_turn():
    registry = GenerationRegistry()
    entry = registry.begin("owner", "turn")
    entry.seal()
    assert await registry.cancel("owner", "turn") == "finalizing"
    assert not entry.stop.is_set()
    registry.finish(entry)
    assert await registry.cancel("owner", "turn") == "finished"
    with pytest.raises(ValueError):
        registry.begin("owner", "turn")


def test_cancel_requires_login_and_valid_id(client, auth_headers):
    assert client.post('/api/chat/cancel', json={'generation_id': str(uuid4())}).status_code == 401
    assert client.post('/api/chat/cancel', headers=auth_headers, json={'generation_id': 'bad'}).status_code == 422


def test_stop_before_stream_arrives_does_not_call_model(client, auth_headers, provider):
    generation = str(uuid4())
    assert client.post('/api/chat/cancel', headers=auth_headers, json={'generation_id': generation}).json()['status'] == 'cancelled'
    response = client.post('/api/chat/stream', headers=auth_headers, json={'message': 'hello', 'generation_id': generation})
    assert [name for name, _ in sse_events(response.text)] == ['cancelled']
    assert not provider.seen


@pytest.mark.parametrize('partial', [False, True])
def test_stop_real_graph_keeps_question_not_partial_reply_and_can_send_again(client, auth_headers, provider, monkeypatch, partial):
    conversation = client.post('/api/conversations', headers=auth_headers).json()
    sid = conversation['session_id']
    initial = conversation['messages']
    generation = str(uuid4())
    started, closed = threading.Event(), threading.Event()
    original_stream = provider.stream
    route_after_reply = Mock()
    monkeypatch.setattr('app.routes.chat.schedule_background_routing', route_after_reply)
    async def waiting(**kwargs):
        try:
            if partial:
                yield StreamDelta(kind='reasoning', text='synthetic thinking')
                yield StreamDelta(kind='content', text='unvalidated partial')
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
        if False:
            yield
    monkeypatch.setattr(provider, 'stream', waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/chat/stream', headers=auth_headers,
                             json={'session_id': sid, 'message': '请先听我说', 'generation_id': generation})
        assert started.wait(10)
        stopped = client.post('/api/chat/cancel', headers=auth_headers, json={'generation_id': generation})
        assert stopped.json()['status'] == 'cancelled'
        assert closed.is_set()
        events = sse_events(future.result(timeout=10).text)
    assert events[-1][0] == 'cancelled'
    assert not any(name == 'persisted' for name, _ in events)
    detail = client.get(f'/api/conversations/{sid}', headers=auth_headers).json()
    assert len(detail['messages']) == len(initial) + 1
    assert detail['messages'][-1]['content'] == '请先听我说'
    route_after_reply.assert_not_called()
    monkeypatch.setattr(provider, 'stream', original_stream)
    followup = client.post('/api/chat/stream', headers=auth_headers,
                           json={'session_id': sid, 'message': '现在继续', 'generation_id': str(uuid4())})
    assert sse_events(followup.text)[-1] == ('persisted', {'saved': True})
    assert client.post('/api/chat/cancel', headers=auth_headers, json={'generation_id': generation}).json()['status'] == 'cancelled'
