import asyncio
import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.graph import get_graph
from app.knowledge_mediator import mediate_knowledge
from app.models import AccountSettings, ConversationKnowledgeSettings, UserAccount
from app.providers.base import as_text
from app.retrieval import KnowledgeChunk
from test_admin_sandbox import sandbox_admin_headers


def create(client, headers):
    response = client.post('/api/conversations', headers=headers, json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_owned_admin_preference_persists_isolated_and_deleted(client, sandbox_admin_headers,
        auth_headers, db_sessionmaker, store):
    admin = create(client, sandbox_admin_headers)
    other = create(client, sandbox_admin_headers)
    member = create(client, auth_headers)
    base = f"/api/conversations/{admin['session_id']}"
    path = base + '/knowledge-mediator'
    assert admin['knowledge_mediator_enabled'] is True
    assert client.patch(path, json={'enabled': False}).status_code == 401
    assert client.patch(path, headers=auth_headers, json={'enabled': False}).status_code == 403
    assert client.patch(f"/api/conversations/{member['session_id']}/knowledge-mediator",
                        headers=sandbox_admin_headers, json={'enabled': False}).status_code == 404
    assert client.patch(path, headers=sandbox_admin_headers, json={'enabled': 'false'}).status_code == 422
    changed = client.patch(path, headers=sandbox_admin_headers, json={'enabled': False})
    assert changed.status_code == 200, changed.text
    assert changed.json()['knowledge_mediator_enabled'] is False
    assert changed.json()['next_module'] == admin['next_module']
    store._sessions.clear()
    assert client.get(base, headers=sandbox_admin_headers).json()['knowledge_mediator_enabled'] is False
    assert client.get(f"/api/conversations/{other['session_id']}", headers=sandbox_admin_headers).json()['knowledge_mediator_enabled'] is True
    assert client.patch(path, headers=sandbox_admin_headers, json={'enabled': True}).json()['knowledge_mediator_enabled'] is True
    assert client.delete(base, headers=sandbox_admin_headers).status_code == 204
    async def remaining():
        async with db_sessionmaker() as db:
            return list((await db.scalars(select(ConversationKnowledgeSettings))).all())
    assert asyncio.run(remaining()) == []


@pytest.mark.parametrize('stream', [False, True])
def test_effective_setting_reaches_pipeline_and_ignores_spoofing(client, sandbox_admin_headers,
        auth_headers, monkeypatch, db_sessionmaker, stream):
    from app.graph import nodes
    original = nodes.mediate_knowledge
    seen = []
    async def capture(**kwargs):
        seen.append(kwargs.get('bypass'))
        return await original(**kwargs)
    monkeypatch.setattr(nodes, 'mediate_knowledge', capture)
    convo = create(client, sandbox_admin_headers)
    sid = convo['session_id']
    def send(headers, session_id):
        result = client.post('/api/chat' + ('/stream' if stream else ''), headers=headers,
            json={'session_id': session_id, 'message': '最近学习压力很大',
                  'knowledge_mediator_bypass': True,
                  'metadata': {'knowledge_mediator_bypass': 'true', 'role': 'admin'}})
        assert result.status_code == 200, result.text
    for enabled in [True, False, True]:
        assert client.patch(f'/api/conversations/{sid}/knowledge-mediator', headers=sandbox_admin_headers,
                            json={'enabled': enabled}).status_code == 200
        send(sandbox_admin_headers, sid)
        assert seen[-1] is (not enabled)
    member = create(client, auth_headers)
    send(auth_headers, member['session_id'])
    assert seen[-1] is False
    client.patch(f'/api/conversations/{sid}/knowledge-mediator', headers=sandbox_admin_headers,
                 json={'enabled': False})
    async def demote():
        async with db_sessionmaker() as db:
            row = await db.scalar(select(AccountSettings).join(UserAccount).where(UserAccount.username == 'sandboxadmin'))
            row.role = 'user'
            await db.commit()
    asyncio.run(demote())
    send(sandbox_admin_headers, sid)
    assert seen[-1] is False


async def test_bypass_sends_passages_without_extra_model_or_guidance(context, provider):
    class KB:
        async def search(self, **kwargs):
            return [KnowledgeChunk('direct-source', 'BYPASS_SOURCE_SENTINEL', 'test', .8)]
    context = dataclasses.replace(context, knowledge_base=KB(), knowledge_mediator_bypass=True)
    context.settings.knowledge_intent_gate_enabled = False
    result = await get_graph().ainvoke({'user_input': '行动与情绪有什么关系', 'forced_module': 'module_2'}, context=context)
    snapshot = result['telemetry']['knowledge_references']
    assert snapshot['mediator_status'] == 'bypassed'
    assert snapshot['mediator_duration_ms'] == 0
    assert snapshot['mediator_model'] is None and snapshot['mediator_guidance'] is None
    assert [c['id'] for c in snapshot['provided']] == ['direct-source']
    assert 'BYPASS_SOURCE_SENTINEL' in as_text(provider.systems[-1])
    assert '# 知识使用中介建议' not in as_text(provider.systems[-1])


@pytest.mark.parametrize('knowledge', [[], [KnowledgeChunk('one', '行动可能帮助情绪。', 'test')]])
async def test_bypass_never_calls_mediator_and_preserves_declined_recording(context, knowledge):
    provider = SimpleNamespace(route_detailed=AsyncMock(side_effect=AssertionError('must not call')))
    selected, block, metrics = await mediate_knowledge(state={}, module='module_2', knowledge=knowledge,
        provider=provider, settings=context.settings, bypass=True)
    assert selected == knowledge and block == '' and metrics['status'] == 'bypassed'
    selected, block, metrics = await mediate_knowledge(state={'recording_status': 'declined'},
        module='module_3', knowledge=knowledge, provider=provider, settings=context.settings, bypass=True)
    assert not selected and not block and metrics['reason'] == 'not_needed'
    provider.route_detailed.assert_not_called()


def test_busy_turn_rejects_switch(client, sandbox_admin_headers, store):
    sid = create(client, sandbox_admin_headers)['session_id']
    lock = asyncio.run(store.get_turn_lock(sid))
    asyncio.run(lock.acquire())
    try:
        result = client.patch(f'/api/conversations/{sid}/knowledge-mediator',
            headers=sandbox_admin_headers, json={'enabled': False})
        assert result.status_code == 409
    finally:
        lock.release()
