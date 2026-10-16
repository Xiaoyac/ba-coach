import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import openai
import pytest
from test_chat_reliability_1012 import api_error, provider_with_create
from app.providers.base import ProviderError, StreamDelta

@pytest.mark.asyncio
@pytest.mark.parametrize('error', [api_error(), api_error(503), openai.APITimeoutError(request=httpx.Request('POST','https://example.invalid')), openai.APIConnectionError(request=httpx.Request('POST','https://example.invalid'))])
async def test_fifth_retry_succeeds_and_status_is_bounded(monkeypatch, error):
    monkeypatch.setattr('app.providers.overload_retry.asyncio.sleep',AsyncMock())
    create=AsyncMock(side_effect=[error]*5+['ok']);p=provider_with_create(create)
    events=[];p.on_main_retry=events.append
    assert await p._create_main_completion()=='ok'
    assert create.await_count==6
    assert [e['attempt'] for e in events if e['retrying']]==[1,2,3,4,5]
    assert events[-1]=={'retrying':False,'attempt':5,'max_retries':5}
    assert p._main_timeout_seconds()==180 and p._main_total_timeout_seconds()==1105
    assert p.with_thinking(False).on_main_retry is None
    assert p.with_main_reply().on_main_retry is None

@pytest.mark.asyncio
async def test_per_attempt_wall_deadline_retries_five_times(monkeypatch):
    monkeypatch.setattr('app.providers.overload_retry.MAIN_ATTEMPT_SECONDS',.001)
    async def stall(): await asyncio.Event().wait()
    monkeypatch.setattr('app.providers.overload_retry.asyncio.sleep',AsyncMock())
    p=provider_with_create(AsyncMock(side_effect=stall));events=[];p.on_main_retry=events.append
    with pytest.raises(asyncio.TimeoutError): await p._create_main_completion()
    assert p._client.chat.completions.create.await_count==6
    assert len(p.overload_retries)==5 and events[-1]['retrying'] is False

@pytest.mark.asyncio
async def test_cancel_during_backoff_stops_and_clears_status(monkeypatch):
    started=asyncio.Event()
    async def sleep(_):started.set();await asyncio.Event().wait()
    monkeypatch.setattr('app.providers.overload_retry.asyncio.sleep',sleep)
    p=provider_with_create(AsyncMock(side_effect=api_error()));events=[];p.on_main_retry=events.append
    task=asyncio.create_task(p._create_main_completion());await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert p._client.chat.completions.create.await_count==1
    assert events[-1]['retrying'] is False

@pytest.mark.asyncio
async def test_headers_and_stream_share_one_attempt_budget(monkeypatch):
    class Slow:
        closed=False
        def __aiter__(self):return self
        async def __anext__(self):await asyncio.Event().wait()
        async def close(self):self.closed=True
    stream=Slow();p=provider_with_create(AsyncMock(return_value=stream))
    monkeypatch.setattr(p,'_main_stream_remaining_seconds',lambda:.001)
    with pytest.raises(ProviderError,match='timed out'):
        async for _ in p.stream(system='rules',messages=[]):pass
    assert stream.closed and p._client.chat.completions.create.await_count==1

@pytest.mark.parametrize('succeed',[True,False])
def test_retry_status_traverses_graph_and_http_stream(client,auth_headers,provider,monkeypatch,succeed):
    from conftest import StubProvider
    async def stream(self,**kwargs):
        for attempt in range(1,6):
            self.on_main_retry({'retrying':True,'attempt':attempt,'max_retries':5})
        self.on_main_retry({'retrying':False,'attempt':5,'max_retries':5})
        if not succeed:raise ProviderError('ServerOverloaded')
        yield StreamDelta(kind='content',text='测试回复。')
    monkeypatch.setattr(StubProvider,'stream',stream)
    sid=client.post('/api/conversations',headers=auth_headers).json()['session_id']
    generation='00000000-0000-4000-8000-000000000005'
    r=client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'test','generation_id':generation})
    assert r.status_code==200
    events=[]
    for block in r.text.split('\n\n'):
        if block.startswith('event: retry\n'):events.append(json.loads(block.split('data: ',1)[1]))
    assert len(events)==6,r.text
    assert all(e['generation_id']==generation for e in events)
    assert [e['attempt'] for e in events[:-1]]==[1,2,3,4,5]
    assert events[-1]['retrying'] is False
    assert ('event: error' in r.text) is not succeed
    assert 'event: persisted' in r.text

@pytest.mark.asyncio
@pytest.mark.parametrize('error',[api_error(401,'invalid key'),api_error(400,'bad input'),api_error(429,'quota exceeded')])
async def test_permanent_errors_never_retry(error):
    p=provider_with_create(AsyncMock(side_effect=error));events=[];p.on_main_retry=events.append
    with pytest.raises(openai.APIStatusError):await p._create_main_completion()
    assert p._client.chat.completions.create.await_count==1 and events==[]
