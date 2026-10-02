from datetime import datetime, timezone
import pytest
from app.context_pipeline import prepare_context
from app.conversation_time import request_time_context, temporal_context
from app.prompts import SystemPromptSegment
from app.schemas import Message


def test_cross_midnight_separates_now_from_message_and_relative_dates():
    sent=datetime(2026,9,29,15,59,59,tzinfo=timezone.utc)
    now=datetime(2026,9,29,16,0,2,tzinfo=timezone.utc)
    prepared=prepare_context(system=[SystemPromptSegment('固定策略',cacheable=True)],
        history=[Message(role='user',content='昨天散步了',created_at=datetime(2026,9,28,1,tzinfo=timezone.utc))],
        user_input='今天还没做',max_history_messages=80,user_created_at=sent,current_time=now)
    clock=prepared.system[-1].text
    assert '<current_datetime>2026-09-30T00:00:02+08:00</current_datetime>' in clock
    assert '<datetime>2026-09-29T23:59:59+08:00</datetime>' in clock
    assert '昨天=2026-09-28；今天=2026-09-29；明天=2026-09-30' in clock
    assert prepared.messages[-1].content == '<message datetime="260929-23:59">今天还没做</message>'
    assert prepared.messages[-1].created_at == sent
    assert prepared.messages[0].content == '<message datetime="260928-09:00">昨天散步了</message>'
    assert prepared.system[0].text=='固定策略' and prepared.system[0].cacheable


def test_unknown_send_time_does_not_borrow_old_user_time():
    old=Message(role='user',content='昨天',created_at=datetime(2000,1,1,tzinfo=timezone.utc))
    now=datetime(2026,9,29,3,tzinfo=timezone.utc)
    context=temporal_context([old],current_time=now)
    assert '<datetime>unknown</datetime>' in context
    assert '2000-01-01' not in context
    assert '不能解析' in request_time_context(current_time=now)


@pytest.mark.parametrize('endpoint',['/api/chat','/api/chat/stream'])
def test_receipt_time_survives_wait_across_midnight(client,provider,monkeypatch,endpoint):
    from app.routes import chat as route
    sent=datetime(2026,9,29,15,59,59,tzinfo=timezone.utc)
    now=[sent]
    monkeypatch.setattr(route,'utc_now',lambda:now[0])
    async def wait(*args,**kwargs):
        now[0]=datetime(2026,9,29,16,0,2,tzinfo=timezone.utc)
    monkeypatch.setattr(route,'wait_for_pending_routing',wait)
    response=client.post(endpoint,json={'message':'今天散步了','metadata':{'datetime':'1900-01-01'}})
    assert response.status_code==200,response.text
    current=provider.seen[-1][-1]
    assert current.created_at==sent
    assert current.content == '<message datetime="260929-23:59">今天散步了</message>'


@pytest.mark.asyncio
async def test_extraction_ignores_model_iso_and_keeps_vague_original(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.graph import nodes
    from app.providers.base import Completion
    evidence=[SimpleNamespace(id=1,role='user',content='我明天下午散步',
        created_at=datetime(2026,9,29,16,0,tzinfo=timezone.utc))]
    provider=SimpleNamespace(name='stub',model='stub',route_detailed=AsyncMock(return_value=Completion(
        text='{"schedule_text":"明天下午","target_activity_time":"2035-12-01 15:30:00"}',model='stub')))
    monkeypatch.setattr(nodes,'save_ai_event',AsyncMock())
    result=await nodes._extract_module_data(provider,None,subject_id='test',module='module_2',
        transcript='',max_tokens=1000,evidence_messages=evidence)
    assert result['schedule_text']=='明天下午'
    assert result['target_activity_time'] is None
