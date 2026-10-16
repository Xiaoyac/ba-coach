import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import openai
import pytest
from app.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.providers.base import ProviderError, Completion, StreamDelta
from app.providers.overload_retry import retryable_overload
from app.generation_control import GenerationRegistry
from app.provisional_reply import usable_lead, generate_reply_lead


def api_error(status=429, message='ServerOverloaded'):
    return openai.APIStatusError(message, response=httpx.Response(status,
        request=httpx.Request('POST','https://example.invalid')), body={'error':message})


def provider_with_create(create):
    p=object.__new__(DeepSeekProvider); p.model='synthetic';p._settings=Settings(_env_file=None)
    p._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p._client.with_options=lambda **kw:p._client
    return p.with_main_reply()


def test_foreground_budget_identical_and_auxiliary_scoped():
    p=provider_with_create(AsyncMock())
    assert p._main_timeout_seconds()==180
    p.deep_reply_enabled=True
    assert p._main_timeout_seconds()==180
    auxiliary=p.with_thinking(False)
    auxiliary._settings=p._settings.model_copy(update={'provider_request_timeout_seconds':8})
    assert auxiliary._main_timeout_seconds()==8
    assert p._main_timeout_seconds()==180


@pytest.mark.asyncio
async def test_explicit_overload_retries_once(monkeypatch):
    sleep=AsyncMock(); monkeypatch.setattr('app.providers.overload_retry.asyncio.sleep',sleep)
    create=AsyncMock(side_effect=[api_error(), 'ok']);p=provider_with_create(create)
    assert await p._create_main_completion(model='x')=='ok'
    assert create.await_count==2 and len(p.overload_retries)==1
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_five_retries_exhaust_and_auxiliary_does_not_retry(monkeypatch):
    monkeypatch.setattr('app.providers.overload_retry.asyncio.sleep',AsyncMock())
    create=AsyncMock(side_effect=api_error());p=provider_with_create(create)
    with pytest.raises(openai.APIStatusError):await p._create_main_completion()
    assert create.await_count==6 and len(p.overload_retries)==5
    create.reset_mock(); p=p.with_thinking(False)
    with pytest.raises(openai.APIStatusError):await p._create_main_completion()
    assert create.await_count==1


@pytest.mark.parametrize('status,body,expected',[(429,'ServerOverloaded',True),(503,'busy',True),(429,'quota exceeded',False),(401,'bad key',False),(400,'bad input',False)])
def test_retry_classification(status,body,expected):assert retryable_overload(api_error(status,body)) is expected


@pytest.mark.asyncio
async def test_retry_wait_uses_outer_budget_and_cancellation():
    p=provider_with_create(AsyncMock(side_effect=api_error()))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(p._create_main_completion(),timeout=.01)
    assert p._client.chat.completions.create.await_count==1


@pytest.mark.asyncio
async def test_never_replay_after_stream_started():
    class Broken:
        closed=False
        def __aiter__(self):return self
        async def __anext__(self):raise api_error()
        async def close(self):self.closed=True
    stream=Broken();create=AsyncMock(return_value=stream);p=provider_with_create(create)
    with pytest.raises(ProviderError):
        async for _ in p.stream(system='rules',messages=[]):pass
    assert create.await_count==1 and stream.closed


def test_session_reservation_blocks_double_click_and_releases():
    r=GenerationRegistry();first=r.begin('a','1','s')
    with pytest.raises(ValueError,match='already running'):r.begin('a','2','s')
    second=r.begin('b','2','s')
    r.finish(first);assert r.begin('a','3','s').claimed
    r.finish(second)


@pytest.mark.parametrize('raw,expected',[
    ('听起来最近的安排很满。你想从哪里开始？','听起来最近的安排很满。'),
    ('你愿意吗？我理解了。',''),
    ('我已经替你保存了。接下来怎么安排？',''),
    ('你在认真考虑这件事。'+ '甲'*160+'。','你在认真考虑这件事。'),
    ('甲'*151+'。',''),
    ('一。二。三。','一。二。'),
])
def test_lead_keeps_complete_valid_prefix(raw,expected):assert usable_lead(raw)[0]==expected


def test_retry_rejects_completed_or_stale_and_foreign_turn(client,auth_headers,provider,monkeypatch):
    sid=client.post('/api/conversations',headers=auth_headers).json()['session_id']
    client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'test'})
    detail=client.get('/api/conversations/'+sid,headers=auth_headers).json()
    uid=next(m['id'] for m in detail['messages'] if m['role']=='user')
    for target in [uid,uid+9999]:
        response=client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'test','retry_of_message_id':target})
        assert response.status_code==409
    response=client.post('/api/chat/stream',headers=auth_headers,json={'session_id':'not-owned','message':'test','retry_of_message_id':uid})
    assert response.status_code==404
    assert len(client.get('/api/conversations/'+sid,headers=auth_headers).json()['messages'])==len(detail['messages'])


def test_retry_failed_turn_succeeds_once_with_explicit_new_user_row(client,auth_headers,provider,monkeypatch):
    async def fail(**kw):
        raise ProviderError('ServerOverloaded')
        yield
    original=provider.stream
    monkeypatch.setattr(provider,'stream',fail)
    sid=client.post('/api/conversations',headers=auth_headers).json()['session_id']
    client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'test'})
    detail=client.get('/api/conversations/'+sid,headers=auth_headers).json()
    uid=next(m['id'] for m in detail['messages'] if m['role']=='user')
    monkeypatch.setattr(provider,'stream',original)
    response=client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'test','retry_of_message_id':uid})
    assert response.status_code==200 and 'event: persisted' in response.text
    saved=client.get('/api/conversations/'+sid,headers=auth_headers).json()
    assert len([m for m in saved['messages'] if m['role']=='user'])==2
    assert saved['messages'][-1]['role']=='assistant'
    assert client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'test','retry_of_message_id':uid}).status_code==409
