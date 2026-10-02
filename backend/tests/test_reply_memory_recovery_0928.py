"""Regressions for the shared conversation: missed input and stale recall."""
import asyncio
from xml.etree import ElementTree
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.conversation_store import load_reply_history, start_turn
from app.models import Conversation, ConversationMessage
from app.prompts import build_system_segments
from app.providers.base import as_text
from app.v2_repository import append_memory, active_memories, reply_memories
from app.v2_workflow import clinical_context
from test_v2_repository import db


@pytest.mark.parametrize('stream', [False, True])
def test_interrupted_user_survives_live_cache_and_repeated_text(client, auth_headers, provider, db_sessionmaker, stream):
    detail = client.post('/api/conversations', headers=auth_headers).json()
    sid = detail['session_id']
    owner = client.get('/api/auth/me', headers=auth_headers).json()['profile_uuid']
    async def save_unfinished():
        async with db_sessionmaker() as session:
            # Simulate a disconnect after durable input but before graph append.
            await start_turn(session, subject_id=owner, session_id=sid, user_text='健身操')
    asyncio.run(save_unfinished())
    endpoint = '/api/chat/stream' if stream else '/api/chat'
    response = client.post(endpoint, headers=auth_headers, json={'session_id':sid, 'message':'健身操'})
    assert response.status_code == 200, response.text
    contents = [m.source_content for m in provider.seen[-1]]
    assert contents[-2:] == ['健身操', '健身操']
    assert contents.count('健身操') == 2
    # Compact transport keeps both identical turns and their source timestamps.
    user_inputs = [ElementTree.fromstring(m.content) for m in provider.seen[-1] if m.role == 'user']
    assert [m.text for m in user_inputs[-2:]] == ['健身操', '健身操']
    assert all(m.tag == 'message' and m.attrib.get('datetime') not in (None, 'unknown')
               for m in user_inputs[-2:])


@pytest.mark.asyncio
async def test_history_boundary_excludes_current_future_and_other_accounts(db):
    await db.execute(insert(ConversationMessage), [
        {'id':4,'conversation_id':1,'position':2,'role':'user','content':'健身操'},
        {'id':5,'conversation_id':1,'position':3,'role':'user','content':'7分吧'},
        {'id':6,'conversation_id':1,'position':4,'role':'user','content':'later queued input'},
    ])
    await db.commit()
    history = await load_reply_history(db,subject_id='a',session_id='chat-a',user_message_id=5,limit=2)
    assert [m.content for m in history] == ['已确认','健身操']
    for args in [('b','chat-a',5),('a','chat-b',5),('a','chat-a',3),('a','chat-a',999)]:
        with pytest.raises(ValueError,match='not_owned'):
            await load_reply_history(db,subject_id=args[0],session_id=args[1],user_message_id=args[2],limit=80)


def test_previous_turn_bookkeeping_never_overrides_current_input():
    text = as_text(build_system_segments('module_2', memory={
        'last_user_message':'stale previous input', 'last_module':'module_4', 'turn_count':'47',
        'conversation_anchor':'早期真实对话', 'pa_card':'已讨论的计划'}))
    assert 'stale previous input' not in text
    assert 'last_user_message' not in text and 'last_module:' not in text and 'turn_count:' not in text
    assert '早期真实对话' in text and '已讨论的计划' in text


@pytest.mark.asyncio
async def test_unconfirmed_summary_is_auditable_but_not_recalled(db):
    inferred = await append_memory(db,user_id='a',memory_type='模块总结',content='invented morning plan',source_kind='ai_inference',source_message_id=1)
    stated = await append_memory(db,user_id='a',memory_type='偏好',content='user stated fact',source_kind='user_statement',source_message_id=1)
    confirmed = await append_memory(db,user_id='a',memory_type='事实',content='confirmed fact',source_kind='user_confirmation',source_message_id=1)
    await append_memory(db,user_id='b',memory_type='事实',content='other account',source_kind='user_confirmation',source_message_id=2)
    await db.commit()
    assert {m['id'] for m in await active_memories(db,user_id='a')} == {inferred,stated,confirmed}
    assert {m['id'] for m in await reply_memories(db,user_id='a')} == {stated,confirmed}
    context = '\n'.join(await clinical_context(async_sessionmaker(db.bind,expire_on_commit=False),'a','chat-a'))
    assert 'invented morning plan' not in context and 'other account' not in context
    assert 'user stated fact' in context and 'confirmed fact' in context


@pytest.mark.asyncio
async def test_router_current_input_is_distinct_from_prior_dialogue(provider):
    from app.router_agent import decide_target_module_with_reasoning
    await decide_target_module_with_reasoning(provider,current_module='module_4',
        user_input='我想换个活动',has_pa_card=False,
        conversation_context='user：我不想继续散步了\nassistant：累了就休息。',
        business_state={'current_module':'module_4'},routing_mode='router_only')
    payload = provider.route_calls[-1]
    assert '此前对话记录（不含本轮输入' in payload
    assert payload.endswith('用户本轮输入：\n我想换个活动')
