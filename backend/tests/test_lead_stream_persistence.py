"""The HTTP transcript and durable message must contain the same ordered reply."""
import asyncio
import json
import pytest
from app.providers.base import Completion, StreamDelta, as_text

LEAD='没走成让你有些失落。我们可以看看当时的困难。'
BODY='当时是什么让你没能出门？'

@pytest.mark.parametrize('repeated', [False, True])
def test_member_sse_handoff_and_durable_history(client, auth_headers, provider, monkeypatch, repeated):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(),'reply_lead_character_seconds',.01)
    async def fast(**kwargs):
        return Completion(text=LEAD,model='stub',finish_reason='stop')
    async def main(*,system,messages):
        assert LEAD in as_text(system)
        assert '用户证据' in as_text(system)
        yield StreamDelta(kind='reasoning',text='test reasoning')
        await asyncio.sleep(.04)
        if repeated:
            for char in LEAD+'\n\n':
                yield StreamDelta(kind='content',text=char)
        yield StreamDelta(kind='content',text=BODY)
        yield StreamDelta(kind='usage',finish_reason='stop')
    monkeypatch.setattr(provider,'complete',fast)
    monkeypatch.setattr(provider,'stream',main)
    conversation=client.post('/api/conversations/current',headers=auth_headers).json()
    sid=conversation['session_id']
    reply=client.post('/api/chat/stream',headers=auth_headers,
        json={'session_id':sid,'message':'昨天没去散步，有点失落'})
    assert reply.status_code==200
    frames=[]
    for frame in reply.text.split('\n\n'):
        rows=frame.splitlines()
        if len(rows)>=2 and rows[0].startswith('event:') and rows[1].startswith('data:'):
            frames.append((rows[0][6:].strip(),json.loads(rows[1][5:])))
    assert ''.join(v['text'] for k,v in frames if k=='delta')==LEAD+'\n\n'+BODY
    assert [v['waiting'] for k,v in frames if k=='reply_wait']==[True,False]
    assert any(k=='persisted' and v['saved'] for k,v in frames)
    saved=client.get('/api/conversations/'+sid,headers=auth_headers).json()
    assert saved['messages'][-1]['content']==LEAD+'\n\n'+BODY
    assert saved['messages'][-2]['content']=='昨天没去散步，有点失落'
