"""Python 3.10 server smoke; synthetic only, no DB sessions or persisted messages."""
import asyncio
import json
import logging
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import Settings,get_settings
from app.knowledge_mediator import mediate_knowledge
from app.providers.base import Completion,ProviderError
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider
from app.providers.deadline import timeout
from app.retrieval import KnowledgeChunk
from app.schemas import Message
from app.graph import nodes


def synthetic_settings():
    return Settings(_env_file=None,database_url='sqlite+aiosqlite:///:memory:')


async def synthetic():
    logging.disable(logging.CRITICAL)
    for cls in (DeepSeekProvider,DoubaoProvider):
        p=object.__new__(cls)
        p.model='synthetic'
        p._settings=synthetic_settings()
        p._settings.provider_request_timeout_seconds=0.02
        p._settings.router_request_timeout_seconds=0.02
        p._settings.background_model_timeout_seconds=0.02
        async def slow(**kw):
            await asyncio.sleep(1)
        p._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=slow)))
        try:
            await p.complete(system='synthetic',messages=[])
            raise AssertionError('missing timeout')
        except ProviderError as exc:
            assert 'timed out' in str(exc)
        assert await p.route(system='synthetic',user='synthetic')==''
        class Stream:
            closed=False
            def __aiter__(self):return self
            async def __anext__(self):
                await asyncio.sleep(1)
                raise StopAsyncIteration
            async def close(self):self.closed=True
        stream=Stream()
        p._client.chat.completions.create=AsyncMock(return_value=stream)
        try:
            async for item in p.stream(system='synthetic',messages=[]):pass
            raise AssertionError('missing stream deadline')
        except ProviderError as exc:
            assert 'timed out' in str(exc)
        assert stream.closed
        p._settings.provider_request_timeout_seconds=1
        stream=Stream()
        p._client.chat.completions.create=AsyncMock(return_value=stream)
        gen=p.stream(system='synthetic',messages=[])
        task=asyncio.create_task(anext(gen))
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
            raise AssertionError('cancel swallowed')
        except asyncio.CancelledError:pass
        await gen.aclose()
        assert stream.closed

    class Selector:
        def __init__(self,text,finish):self.text,self.finish=text,finish
        async def route_detailed(self,**kw):
            assert kw['include_reasoning'] is False and kw['max_tokens']==512
            return Completion(text=self.text,model='synthetic',finish_reason=self.finish)
    kb=[KnowledgeChunk(id='synthetic-1',source='synthetic',text='不保证运动马上改善心情。')]
    for raw,finish in [('', 'length'),('not json','stop'),('{"selected_ids":["unknown"],"guidance":"test"}','stop')]:
        selected,block,metrics=await mediate_knowledge(state={'user_input':'synthetic'},module='module_2',
            knowledge=kb,provider=Selector(raw,finish),settings=synthetic_settings())
        assert not selected and not block and metrics['withheld_on_error']
    ok=Selector('{"selected_ids":["synthetic-1"],"guidance":"不作保证"}','stop')
    selected,block,metrics=await mediate_knowledge(state={'user_input':'synthetic'},module='module_2',
        knowledge=kb,provider=ok,settings=synthetic_settings())
    assert selected and block and metrics['status']=='completed'
    task=asyncio.create_task(asyncio.sleep(0.08))
    nodes._routing_tasks['synthetic-test']=task
    assert not await nodes.wait_for_pending_routing('synthetic-test',timeout_seconds=0.01)
    assert not task.cancelled()
    await nodes.wait_for_pending_routing('synthetic-test')
    nodes._routing_tasks.pop('synthetic-test',None)
    print(json.dumps({'synthetic_checks':'PASS','python':sys.version.split()[0],
        'timeout_implementation':timeout.__module__,'database_operations':0}),flush=True)


async def live():
    cfg=get_settings()
    mediator=DeepSeekProvider(cfg)
    try:
        for module,question in [('module_2','我一想到运动就有点压力，可以从很小的活动开始吗？'),
                                ('module_4','我今天按计划散步了十分钟，但是心情没明显变好，怎么办？')]:
            kb=[KnowledgeChunk(id='synthetic-ba',source='synthetic-smoke',
                text='行为激活鼓励从具体、可行的小活动开始。活动后记录体验，不保证每次活动立刻改善情绪。复盘应肯定实际尝试，并根据用户反馈讨论调整。')]
            selected,block,metrics=await mediate_knowledge(state={'user_input':question},module=module,
                knowledge=kb,provider=mediator,settings=cfg)
            print(json.dumps({'live_mediator':module,'status':metrics['status'],'reason':metrics['reason'],
                'seconds':metrics['duration_ms']/1000,'approved':len(selected),
                'finish_reason':metrics.get('finish_reason'),'usage':metrics.get('usage'),
                'max_tokens':metrics.get('max_tokens'),'include_reasoning':metrics.get('include_reasoning')}),flush=True)
            assert metrics['status']=='completed', 'Live mediator qualification failed'
    finally:
        await mediator._client.close()
    for cls in (DeepSeekProvider,DoubaoProvider):
        provider=cls(cfg)
        started=perf_counter()
        parts=[]
        finish=None
        try:
            async for delta in provider.stream(system='你是行为激活教练。简短、尊重自主，不作诊断，不声称保存任何数据。',
                messages=[Message(role='assistant',content='你愿意先试试五分钟的轻松散步吗？'),Message(role='user',content='好的')]):
                if delta.kind=='content':parts.append(delta.text)
                if delta.kind=='usage':finish=delta.finish_reason
            assert ''.join(parts).strip() and finish=='stop'
            print(json.dumps({'live_short_reply':provider.name,'seconds':round(perf_counter()-started,3),
                'reply_chars':sum(map(len,parts)),'finish_reason':finish}),flush=True)
        finally:
            await provider._client.close()


async def main():
    await synthetic()
    if '--live' in sys.argv:
        await live()


if __name__=='__main__':
    asyncio.run(main())
