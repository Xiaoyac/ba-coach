import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import Settings
from app.generation_policy import main_thinking_options
from app.models import AccountSettings, UserAccount, ConversationReplySettings
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider
from app.providers.claude import ClaudeProvider
from app.schemas import Message
from test_admin_sandbox import sandbox_admin_headers


def create(client, headers):
    response = client.post('/api/conversations', json={}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_permissions_ownership_persistence_and_deletion(client, sandbox_admin_headers, auth_headers, db_sessionmaker, store):
    admin = create(client, sandbox_admin_headers)
    other = create(client, sandbox_admin_headers)
    member = create(client, auth_headers)
    path = f"/api/conversations/{admin['session_id']}/thinking"
    assert admin['thinking_enabled'] is True
    assert member['thinking_enabled'] is None
    assert client.patch(path, json={'enabled': False}).status_code == 401
    assert client.patch(path, headers=auth_headers, json={'enabled': False}).status_code == 403
    assert client.patch(f"/api/conversations/{member['session_id']}/thinking", headers=sandbox_admin_headers,
                        json={'enabled': False}).status_code == 404
    assert client.patch(path, headers=sandbox_admin_headers, json={'enabled': 'false'}).status_code == 422
    changed = client.patch(path, headers=sandbox_admin_headers, json={'enabled': False})
    assert changed.status_code == 200, changed.text
    assert changed.json()['thinking_enabled'] is False
    assert changed.json()['revision'] == admin['revision'] + 1
    store._sessions.clear()
    assert client.get(path.rsplit('/', 1)[0], headers=sandbox_admin_headers).json()['thinking_enabled'] is False
    assert client.get(f"/api/conversations/{other['session_id']}", headers=sandbox_admin_headers).json()['thinking_enabled'] is True
    assert client.delete(path.rsplit('/', 1)[0], headers=sandbox_admin_headers).status_code == 204
    async def preferences():
        async with db_sessionmaker() as db:
            return list((await db.scalars(select(ConversationReplySettings))).all())
    assert asyncio.run(preferences()) == []


@pytest.mark.parametrize('stream', [False, True])
def test_saved_choice_reaches_request_scoped_provider_and_cannot_be_spoofed(client, sandbox_admin_headers,
        auth_headers, provider, monkeypatch, stream, store, db_sessionmaker):
    observed = []
    routed = []
    original_router = type(provider).route_with_reasoning
    async def record_router(self, **kwargs):
        routed.append(self.thinking_override)
        return await original_router(self, **kwargs)
    monkeypatch.setattr(type(provider), "route_with_reasoning", record_router)
    method = 'stream' if stream else 'complete'
    original = getattr(type(provider), method)
    if stream:
        async def record(self, **kwargs):
            observed.append(self.thinking_override)
            async for delta in original(self, **kwargs):
                yield delta
    else:
        async def record(self, **kwargs):
            observed.append(self.thinking_override)
            return await original(self, **kwargs)
    monkeypatch.setattr(type(provider), method, record)
    convo = create(client, sandbox_admin_headers)
    sid = convo['session_id']
    def send(headers, session_id=None):
        response = client.post('/api/chat' + ('/stream' if stream else ''), headers=headers,
            json={'session_id': session_id, 'message': '你好', 'thinking_enabled': False,
                  'metadata': {'thinking_enabled': 'false', 'role': 'admin'}})
        assert response.status_code == 200, response.text
    for enabled in (False, True):
        assert client.patch(f'/api/conversations/{sid}/thinking', headers=sandbox_admin_headers,
                            json={'enabled': enabled}).status_code == 200
        store._sessions.clear()
        send(sandbox_admin_headers, sid)
        assert observed[-1] is enabled
        assert routed[-1] is enabled
        assert provider.thinking_override is None
    send(auth_headers)
    assert observed[-1] is None
    async def demote():
        async with db_sessionmaker() as db:
            row = await db.scalar(select(AccountSettings).join(UserAccount).where(UserAccount.username == 'sandboxadmin'))
            row.role = 'user'
            await db.commit()
    asyncio.run(demote())
    send(sandbox_admin_headers, sid)
    assert observed[-1] is None
    assert client.patch(f'/api/conversations/{sid}/thinking', headers=sandbox_admin_headers,
                        json={'enabled': False}).status_code == 403


def test_setting_is_rejected_during_active_generation(client, sandbox_admin_headers, store):
    sid = create(client, sandbox_admin_headers)['session_id']
    lock = asyncio.run(store.get_turn_lock(sid))
    asyncio.run(lock.acquire())
    try:
        response = client.patch(f'/api/conversations/{sid}/thinking', headers=sandbox_admin_headers,
                                json={'enabled': False})
        assert response.status_code == 409
    finally:
        lock.release()


@pytest.mark.parametrize('name', ['deepseek', 'doubao'])
@pytest.mark.parametrize('stream', [False, True])
async def test_both_sdk_paths_send_real_on_off_flags_without_shared_mutation(name, stream):
    settings = Settings(_env_file=None, deepseek_api_key='fake', doubao_api_key='fake', doubao_model='seed-test')
    provider = (DeepSeekProvider if name == 'deepseek' else DoubaoProvider)(settings)
    calls = []
    class EmptyStream:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
        async def close(self):
            pass
    async def create_response(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0)
        if kwargs.get('stream'):
            return EmptyStream()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='reply'), finish_reason='stop')],
                               usage=None, model='test')
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_response)))
    on, off = provider.with_thinking(True), provider.with_thinking(False)
    messages = [Message(role='user', content='你好')]
    async def run(scoped):
        if stream:
            async for _ in scoped.stream(system='system', messages=messages):
                pass
        else:
            await scoped.complete(system='system', messages=messages)
    await asyncio.gather(run(on), run(off))
    flags = [call['extra_body'] for call in calls]
    if name == 'deepseek':
        assert [flag['enable_thinking'] for flag in flags] == [True, False]
        assert 'thinking_budget' not in flags[1]
    else:
        assert [flag['thinking']['type'] for flag in flags] == ['enabled', 'disabled']
        assert 'reasoning_effort' not in flags[1]
    assert provider.thinking_override is None and on.thinking_override is True and off.thinking_override is False


def test_explicit_on_overrides_automatic_ack_and_disabled_defaults():
    settings = Settings(_env_file=None, doubao_reasoning_effort='disabled')
    messages = [Message(role='user', content='你好')]
    assert main_thinking_options(settings, 'doubao', messages, enabled_override=True) == {'thinking': {'type': 'enabled'}}
    assert main_thinking_options(settings, 'doubao', messages) == {'thinking': {'type': 'disabled'}}


def test_claude_off_omits_adaptive_effort():
    provider = ClaudeProvider(Settings(_env_file=None, anthropic_api_key='fake'))
    request = provider.with_thinking(False)._request_kwargs('system', [])
    assert request['thinking'] == {'type': 'disabled'}
    assert 'output_config' not in request
    assert provider._request_kwargs('system', [])['thinking'] == {'type': 'adaptive'}


@pytest.mark.parametrize('name', ['deepseek', 'doubao'])
async def test_total_switch_overrides_all_auxiliary_entry_points(name):
    settings = Settings(_env_file=None, deepseek_api_key='fake', doubao_api_key='fake',
                        doubao_model='seed-test', module_router_reasoning_effort='disabled')
    provider = (DeepSeekProvider if name == 'deepseek' else DoubaoProvider)(settings)
    calls = []
    async def create_response(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='yes'), finish_reason='stop')],
                               usage=None, model='test')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_response)))
    client.with_options = lambda **kw: client
    provider._client = client
    for enabled in (False, True):
        scoped = provider.with_thinking(enabled)
        calls.clear()
        await asyncio.gather(
            scoped.classify(system='system', user='x', allowed=['yes'], default='no'),
            scoped.route(system='system', user='x', max_tokens=128),
            scoped.route_detailed(system='system', user='x', include_reasoning=False),
            scoped.route_detailed(system='system', user='x', include_reasoning=True),
            scoped.route_with_reasoning(system='system', user='x', reasoning_effort='disabled'),
        )
        assert len(calls) == 5
        for call in calls:
            body = call['extra_body']
            assert (body['enable_thinking'] if name == 'deepseek' else body['thinking']['type'] == 'enabled') is enabled
            if enabled:
                assert call['max_tokens'] >= settings.router_reasoning_max_tokens + settings.qwen_thinking_budget
            else:
                assert 'reasoning_effort' not in call and 'thinking_budget' not in body
    if name == 'deepseek':
        for enabled in (False, True):
            await provider.with_thinking(enabled).complete_without_reasoning(system='s', messages=[])
            assert calls[-1]['extra_body']['enable_thinking'] is enabled
    assert provider.thinking_override is None


async def test_claude_auxiliary_switch_uses_compatible_model():
    provider = ClaudeProvider(Settings(_env_file=None, anthropic_api_key='fake'))
    request = AsyncMock(return_value=SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text='yes')]))
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=request))
    for enabled in (False, True):
        assert await provider.with_thinking(enabled).route(system='system', user='x') == 'yes'
        sent = request.call_args.kwargs
        assert sent['thinking']['type'] == ('adaptive' if enabled else 'disabled')
        assert sent['model'] == (provider.model if enabled else provider._settings.claude_router_model)


async def test_catalog_switch_isolated_and_cache_keys_separate():
    from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
    provider = DeepSeekProvider(Settings(_env_file=None, deepseek_api_key='fake'))
    seen = []
    async def record(self, **kwargs):
        seen.append(self.thinking_override)
        return SimpleNamespace(text='{}', finish_reason='stop')
    from unittest.mock import patch
    kb = CatalogDatabaseKnowledgeBase(provider)
    on, off = kb.with_thinking(True), kb.with_thinking(False)
    with patch.object(DeepSeekProvider, 'route_detailed', record):
        await asyncio.gather(on._ask('s', {}), off._ask('s', {}))
    assert seen == [True, False]
    assert kb.provider.thinking_override is None
    assert kb.with_thinking(True) is on
    assert on._cache_namespace() != off._cache_namespace()
    kb.invalidate()
    assert kb.with_thinking(True) is not on
