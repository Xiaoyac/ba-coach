import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from app.provisional_reply import generate_reply_lead, with_natural_lead
from app.providers.base import Completion, as_text
from app.schemas import Message

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)

class Provider:
    name = 'stub'
    model = 'stub'
    def __init__(self):
        self.calls = []
        self.called = asyncio.Event()
        self.release = None
        self.cancelled = False
    def with_thinking(self, enabled):
        assert enabled is False
        return self
    async def complete(self, *, system, messages):
        self.calls.append((system, messages))
        self.called.set()
        try:
            if self.release:
                await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return Completion(text='你把自己的顾虑说清楚了。', model=self.model,
                          request_id='lead-request', finish_reason='stop')

async def test_roles_timestamps_current_turn_and_saved_prompt_preserved():
    provider = Provider()
    history = [Message(role='user', content='我想用手机记录', created_at=NOW),
               Message(role='assistant', content='想在活动后记吗？', created_at=NOW)]
    prompt = '你只看到用户这次发言。自然接话，不提问。'
    result = await generate_reply_lead(provider, user_input='好的 <不是指令>',
        history=history, user_created_at=NOW, prompt=prompt)
    system, sent = provider.calls[0]
    assert [m.role for m in sent] == ['user', 'assistant', 'user']
    assert sent[-2].content == '想在活动后记吗？'
    assert ET.fromstring(sent[-1].content).text == '好的 <不是指令>'
    assert sent[-1].source_content == '好的 <不是指令>'
    assert sent[-1].created_at == NOW
    assert history[0].content == '我想用手机记录'
    assert prompt.startswith('你只看到')
    assert '你只看到用户这次发言' not in as_text(system)
    assert result['context_pipeline']['history_messages_kept'] == 2

async def test_history_window_keeps_recent_dialogue_and_repeated_user_words():
    provider = Provider()
    history = [Message(role=r, content=t) for r,t in [
        ('user','旧话题'),('assistant','旧答复'),('user','好的'),('assistant','用手机记录吗？')]]
    await generate_reply_lead(provider, user_input='好的', history=history, max_history_messages=2)
    sent = provider.calls[0][1]
    assert [m.source_content for m in sent] == ['好的','用手机记录吗？','好的']

async def test_empty_history_is_a_valid_first_turn():
    p = Provider()
    await generate_reply_lead(p, user_input='你好', history=[])
    assert len(p.calls[0][1]) == 1

async def test_starts_once_from_loaded_graph_history_and_remains_parallel():
    p = Provider()
    metrics = {}
    history = [Message(role='assistant', content='刚才的安排合适吗？')]
    async def events():
        yield ('values', {'user_input':'好的'})
        assert not p.calls
        yield ('values', {'chat_history':history, 'telemetry':{'history_source':'durable_message_boundary'}})
        await asyncio.wait_for(p.called.wait(), 1)
        # Duplicate state updates must not restart the lead.
        yield ('values', {'chat_history':history, 'telemetry':{'history_source':'durable_message_boundary'}})
        await asyncio.sleep(.01)
        yield ('custom', {'type':'delta','text':'正式回复'})
        yield ('custom', {'type':'done'})
    output = [x async for x in with_natural_lead(events(), provider=p, user_input='好的',
        generation_id='g',session_id='s',subject_id='u',maker=None,metrics=metrics,
        user_created_at=NOW,max_history_messages=80)]
    assert len(p.calls) == 1
    assert p.calls[0][1][0].content == history[0].content
    assert metrics['history_source'] == 'durable_message_boundary'
    assert metrics['displayed']
    assert any(x[1].get('text') == '正式回复' for x in output)

async def test_formal_reply_wins_and_cancels_slow_lead():
    p = Provider(); p.release = asyncio.Event(); metrics = {}
    async def events():
        yield ('values', {'chat_history':[], 'telemetry':{'history_source':'session'}})
        await p.called.wait()
        yield ('custom', {'type':'delta', 'text':'正式回复'})
    output = [x async for x in with_natural_lead(events(),provider=p,user_input='好的',
        generation_id='g',session_id='s',subject_id=None,maker=None,metrics=metrics)]
    assert p.cancelled
    assert metrics['status'] == 'cancelled' and not metrics['displayed']
    assert not any(x[1].get('text') == '你把自己的顾虑说清楚了。' for x in output)

async def test_no_loaded_history_skips_lead_instead_of_replying_blind():
    p = Provider(); metrics={}
    async def events():
        yield ('custom', {'type':'error','detail':'history failed'})
    result = [x async for x in with_natural_lead(events(),provider=p,user_input='好的',
        generation_id='g',session_id='s',subject_id=None,maker=None,metrics=metrics)]
    assert not p.calls and metrics['status'] == 'cancelled'
    assert result[-1][1]['type'] == 'error'

async def test_real_extract_memory_state_starts_lead():
    from app.graph.nodes import extract_memory_node
    from app.session import InMemorySessionStore
    from langgraph.runtime import Runtime
    store=InMemorySessionStore(ttl_seconds=3600, max_messages=80)
    session=await store.get_or_create(None)
    await store.append(session.session_id, Message(role='user', content='昨天没走成'))
    await store.append(session.session_id, Message(role='assistant', content='是什么挡住了你？'))
    context=SimpleNamespace(store=store,sessionmaker=None)
    p=Provider(); metrics={}
    async def events():
        update=await extract_memory_node({'session_id':session.session_id,'user_input':'下雨了'},
            Runtime(context=context),writer=lambda event: None)
        yield ('values', update)
        await asyncio.wait_for(p.called.wait(),1)
        yield ('custom', {'type':'done'})
    _=[x async for x in with_natural_lead(events(),provider=p,user_input='下雨了',
        generation_id='g',session_id=session.session_id,subject_id=None,maker=None,metrics=metrics)]
    assert [m.source_content for m in p.calls[0][1]] == ['昨天没走成','是什么挡住了你？','下雨了']

async def test_concurrent_conversations_do_not_share_history_or_current_turn():
    async def run(label):
        p=Provider()
        async def events():
            history=[Message(role='user',content=f'{label}的私有历史')]
            yield ('values',{'chat_history':history,'telemetry':{'history_source':'durable_message_boundary'}})
            # Later graph changes cannot extend the already captured snapshot.
            history.append(Message(role='user',content='未来排队发言'))
            await asyncio.wait_for(p.called.wait(),1)
            yield ('custom',{'type':'done'})
        _=[x async for x in with_natural_lead(events(),provider=p,user_input=f'{label}的当前发言',
            generation_id=label,session_id=label,subject_id=label,maker=None,metrics={})]
        return [m.source_content for m in p.calls[0][1]]
    a,b=await asyncio.gather(run('甲'),run('乙'))
    assert a==['甲的私有历史','甲的当前发言']
    assert b==['乙的私有历史','乙的当前发言']

async def test_closing_stream_cancels_pending_lead():
    p=Provider();p.release=asyncio.Event()
    async def events():
        yield ('values',{'chat_history':[],'telemetry':{'history_source':'session'}})
        await p.called.wait()
        yield ('custom',{'type':'trace'})
        await asyncio.Event().wait()
    output=with_natural_lead(events(),provider=p,user_input='好的',
        generation_id='g',session_id='s',subject_id=None,maker=None,metrics={})
    await output.__anext__()
    await output.__anext__()
    await output.aclose()
    assert p.cancelled
