"""A bounded handoff and paced prefix must precede the same turn's body."""
import asyncio
import dataclasses
import json
from types import SimpleNamespace

import httpx
import pytest
from app.config import Settings
from app.provisional_reply import generate_reply_lead, normalize_lead, parallel_reply_system, with_natural_lead
from app.providers.base import Completion, StreamDelta, as_text
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider

pytestmark = pytest.mark.asyncio
LEAD = '没走成让你有些失落。我们可以先看看当时遇到了什么困难。'

class LeadProvider:
    name = model = 'stub'
    def with_thinking(self, enabled): return self
    async def complete(self, **kwargs):
        return Completion(text=LEAD, model='stub', finish_reason='stop')

def wrapped(events, ctx, metrics=None, provider=None):
    return with_natural_lead(events, provider=provider or LeadProvider(), user_input='没走成',
        generation_id='g', session_id='s', subject_id=None, maker=None,
        metrics=metrics if metrics is not None else {}, context=ctx)

def visible(events):
    return ''.join(e.get('text', '') for mode,e in events if mode=='custom' and e.get('type')=='delta')

async def test_graph_main_reads_exact_opening_and_streams_it_first(context, provider, monkeypatch):
    provider.risk_result = '{"risk_status":0}'
    from app.graph import get_graph
    entered = asyncio.Event()
    async def fast(**kwargs):
        assert not entered.is_set()
        return Completion(text=LEAD, model='stub', finish_reason='stop')
    async def main(*, system, messages):
        assert LEAD in as_text(system) and '只输出后续内容' in as_text(system)
        assert messages[-1].source_content == '昨天太累了，没去散步，有点失落'
        entered.set()
        yield StreamDelta(kind='reasoning', text='思考测试')
        await asyncio.sleep(.05)
        yield StreamDelta(kind='content', text='当时的疲惫主要来自身体，还是心里有些提不起劲？')
        yield StreamDelta(kind='usage', finish_reason='stop')
    monkeypatch.setattr(provider, 'complete', fast)
    monkeypatch.setattr(provider, 'stream', main)
    ctx = dataclasses.replace(context, stream=True,
        settings=context.settings.model_copy(update={'reply_lead_character_seconds': .01}))
    state={'user_input':'昨天太累了，没去散步，有点失落','forced_module':'module_1',
           'telemetry':{'reply_mode':'ack_deep'}}
    metrics={}
    events=[x async for x in with_natural_lead(
        get_graph().astream(state, context=ctx, stream_mode=['custom','values']),
        provider=provider,user_input=state['user_input'],generation_id='g',session_id='s',
        subject_id=None,maker=None,metrics=metrics,context=ctx)]
    assert entered.is_set()
    assert visible(events)==LEAD+'\n\n当时的疲惫主要来自身体，还是心里有些提不起劲？'
    assert len([e for mode,e in events if mode=='custom' and e.get('phase')=='lead'])>=3
    assert metrics['text']==LEAD

async def test_body_arrival_flushes_prefix_without_waiting_for_slow_timer():
    called=asyncio.Event(); release=asyncio.Event()
    ctx=SimpleNamespace(settings=SimpleNamespace(reply_lead_character_seconds=10))
    async def events():
        yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
        assert (await ctx.reply_lead_task)['text']==LEAD
        called.set()
        yield ('custom',{'type':'reasoning_delta','text':'thinking'})
        await release.wait()
        yield ('custom',{'type':'delta','text':'后续正文'})
    seen=[]
    async def collect():
        async for item in wrapped(events(),ctx): seen.append(item)
    task=asyncio.create_task(collect())
    await asyncio.wait_for(called.wait(),1)
    await asyncio.sleep(.02)
    assert visible(seen)==LEAD[:1]
    release.set()
    await asyncio.wait_for(task,1)
    assert visible(seen)==LEAD+'\n\n后续正文'

async def test_timeout_unblocks_deep_without_inventing_an_opening():
    class Slow(LeadProvider):
        async def complete(self, **kwargs): await asyncio.Event().wait()
    ctx=SimpleNamespace(settings=SimpleNamespace(reply_lead_timeout_seconds=.01))
    metrics={}
    async def events():
        yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
        assert (await ctx.reply_lead_task)['text']==''
        yield ('custom',{'type':'delta','text':'独立完整回答'})
    output=[x async for x in wrapped(events(),ctx,metrics,Slow())]
    assert metrics['reason_code']=='lead_timeout' and not metrics['displayed']
    assert visible(output)=='独立完整回答'

async def test_closing_slow_display_cancels_deep_and_keeps_only_released_prefix():
    closed=asyncio.Event()
    ctx=SimpleNamespace(settings=SimpleNamespace(reply_lead_character_seconds=10))
    async def events():
        try:
            yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
            await ctx.reply_lead_task
            await asyncio.Event().wait()
        finally: closed.set()
    metrics={}; stream=wrapped(events(),ctx,metrics)
    while True:
        mode,e=await stream.__anext__()
        if e.get('type')=='delta': break
    await stream.aclose()
    assert closed.is_set()
    assert metrics['text']==LEAD[:1] and metrics['generated_text']==LEAD

async def test_intervention_opening_allowed_and_cache_tail_preserved():
    from app.prompts import SystemPromptSegment
    from app.providers.prompt_cache import ordered_messages
    assert normalize_lead(LEAD)==LEAD
    assert not normalize_lead('已经帮你保存并确认计划。')
    assert not normalize_lead('你愿意试试吗？')
    segments=parallel_reply_system([SystemPromptSegment('stable',True),
        SystemPromptSegment('dynamic',False,after_history=True)],LEAD)
    wire=ordered_messages(segments,[{'role':'user','content':'history'},
        {'role':'assistant','content':'old reply'},{'role':'user','content':'current'}])
    assert wire[0]['content']=='stable' and wire[1]['content']=='history'
    assert LEAD in wire[-2]['content'] and wire[-1]['content']=='current'

@pytest.mark.parametrize('size',[1,4,1000])
@pytest.mark.parametrize('suffix',['。\n\n后续正文','，后续正文','。后续正文'])
async def test_copied_opening_removed_across_chunk_and_punctuation_boundaries(size,suffix):
    from app.provisional_reply import ContinuationPrefix
    prefix=ContinuationPrefix(LEAD)
    raw=LEAD.rstrip('。')+suffix
    text=''.join(prefix.push(raw[i:i+size]) for i in range(0,len(raw),size))+prefix.finish()
    assert text=='后续正文' and prefix.removed

async def test_similar_new_sentence_is_not_deleted():
    from app.provisional_reply import ContinuationPrefix
    prefix=ContinuationPrefix('先走一步。')
    raw='先走一步就可能有新的感受。'
    assert prefix.push(raw)+prefix.finish()==raw and not prefix.removed
