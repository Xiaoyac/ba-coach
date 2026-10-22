"""Role defaults, independent persistence, provider bypass and experiment labels."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from app.graph import get_graph
from app.models import AIExecutionEvent, ConversationMediatorNodeSettings
from app.providers.base import as_text
from app.retrieval import KnowledgeChunk
from test_admin_sandbox import sandbox_admin_headers
from test_mediator_bypass import create


def test_node_ownership_persistence_independence_and_delete(client, sandbox_admin_headers,
        auth_headers, register, db_sessionmaker, store):
    a, b = [create(client, sandbox_admin_headers) for _ in range(2)]
    member = create(client, auth_headers)
    other = register(username='mediatorother')
    base = f"/api/conversations/{a['session_id']}"
    path = base + '/mediator-node'
    assert a['knowledge_mediator_node_enabled'] is False
    assert member['knowledge_mode'] == 'astrbot_hybrid' and member['knowledge_mediator_node_enabled']
    assert client.patch(path, json={'enabled': True}).status_code == 401
    assert client.patch(path, headers=auth_headers, json={'enabled': False}).status_code == 403
    assert client.patch(path, headers=other, json={'enabled': True}).status_code == 403
    assert client.patch(f"/api/conversations/{member['session_id']}/mediator-node",
                        headers=sandbox_admin_headers, json={'enabled': True}).status_code == 404
    for payload in [{'enabled': 'false'}, {'enabled': 1}, {'enabled': True, 'role': 'admin'}]:
        assert client.patch(path, headers=sandbox_admin_headers, json=payload).status_code == 422
    enabled = client.patch(path, headers=sandbox_admin_headers, json={'enabled': True}).json()
    assert enabled['knowledge_mediator_node_enabled'] and enabled['revision'] > a['revision']
    assert enabled['next_module'] == a['next_module']
    assert enabled['messages'] == a['messages']
    assert enabled['knowledge_mode'] == 'astrbot_hybrid'
    store._sessions.clear()
    assert client.get(base, headers=sandbox_admin_headers).json()['knowledge_mediator_node_enabled']
    assert not client.get(f"/api/conversations/{b['session_id']}", headers=sandbox_admin_headers).json()['knowledge_mediator_node_enabled']
    lock = asyncio.run(store.get_turn_lock(a['session_id']))
    asyncio.run(lock.acquire())
    try:
        assert client.patch(path, headers=sandbox_admin_headers, json={'enabled': False}).status_code == 409
    finally:
        lock.release()
    assert client.delete(base, headers=sandbox_admin_headers).status_code == 204
    async def remaining():
        async with db_sessionmaker() as db:
            return (await db.scalars(select(ConversationMediatorNodeSettings))).all()
    assert asyncio.run(remaining()) == []


@pytest.mark.parametrize('hybrid', [False, True])
async def test_disabled_node_forwards_retrieved_material_without_mediator(context, provider, monkeypatch, hybrid):
    from app import hybrid_mediator
    forbidden = AsyncMock(side_effect=AssertionError('disabled node called a model'))
    monkeypatch.setattr(hybrid_mediator, 'mediate_hybrid_knowledge', forbidden)
    class KB:
        async def search(self, **kwargs):
            return [KnowledgeChunk('one', '合成知识：散步活动可能帮助改善心情。', 'BA')]
    context = replace(context, knowledge_base=KB(), knowledge_mediator_bypass=hybrid,
                      knowledge_mediator_node_enabled=False)
    context.settings.knowledge_intent_gate_enabled = False
    result = await get_graph().ainvoke({'user_input': '为什么散步可能帮助改善心情？',
                                       'forced_module': 'module_2'}, context=context)
    forbidden.assert_not_awaited()
    assert '合成知识：散步活动可能帮助改善心情。' in as_text(provider.systems[-1])
    metrics = result['telemetry']['knowledge_mediator']
    assert metrics['node_enabled'] is False and metrics['status'] == 'bypassed'
    assert metrics['duration_ms'] == 0
    assert metrics['knowledge_mode'] == ('astrbot_hybrid' if hybrid else 'legacy_mediator')
    assert result['telemetry']['knowledge_references']['provided'][0]['id'] == 'one'


@pytest.mark.parametrize('stream', [False, True])
def test_saved_main_metrics_identify_disabled_experiment(client, sandbox_admin_headers, db_sessionmaker, stream):
    sid = create(client, sandbox_admin_headers)['session_id']
    response = client.post('/api/chat' + ('/stream' if stream else ''), headers=sandbox_admin_headers,
                           json={'session_id': sid, 'message': '最近学习压力很大'})
    assert response.status_code == 200
    async def event():
        async with db_sessionmaker() as db:
            return (await db.scalars(select(AIExecutionEvent).where(
                AIExecutionEvent.session_id == sid, AIExecutionEvent.stage == 'main_generation'))).all()
    saved = asyncio.run(event())[-1].event_metadata
    assert saved['knowledge_mode'] == 'astrbot_hybrid'
    assert saved['knowledge_mediator_node_enabled'] is False
    assert saved['knowledge_mediator']['status'] == 'bypassed'
