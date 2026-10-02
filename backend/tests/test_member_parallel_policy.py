from types import SimpleNamespace

from sqlalchemy import delete, insert, select, update

from app.models import Conversation, ConversationReplyEffort, ConversationResponseMode, ConversationRuntimeState
from app.conversation_reply_mode import effective_reply_mode, effective_reply_effort
from app.routing_modes import effective_routing_mode
from test_admin_accounts import admin_headers
from test_router_only_routing import router_only_db


def test_new_and_old_members_use_parallel_router_low_without_reset(client, auth_headers, db_sessionmaker):
    first = client.post('/api/conversations/current', headers=auth_headers)
    assert first.status_code == 200, first.text
    data = first.json()
    assert (data['reply_mode'], data['routing_mode'], data['reply_effort']) == ('ack_deep', 'router_only', 'low')
    sid = data['session_id']
    async def old_settings():
        async with db_sessionmaker() as db:
            c = await db.scalar(select(Conversation).where(Conversation.session_id == sid))
            await db.execute(delete(ConversationResponseMode).where(ConversationResponseMode.conversation_id == c.id))
            await db.execute(update(ConversationReplyEffort).where(ConversationReplyEffort.conversation_id == c.id).values(effort='max'))
            state = await db.get(ConversationRuntimeState, c.id)
            state.module = 'module_3'
            state.memory = {'routing_mode':'router_code', 'existing_progress':'keep'}
            await db.commit()
    client.portal.call(old_settings)
    restored = client.post('/api/conversations/current', headers=auth_headers).json()
    assert restored['session_id'] == sid and restored['next_module'] == 'module_3'
    assert restored['messages'] == data['messages']
    assert (restored['reply_mode'], restored['routing_mode'], restored['reply_effort']) == ('ack_deep', 'router_only', 'low')
    async def unchanged():
        async with db_sessionmaker() as db:
            c = await db.scalar(select(Conversation).where(Conversation.session_id == sid))
            state = await db.get(ConversationRuntimeState, c.id)
            assert state.memory == {'routing_mode':'router_code', 'existing_progress':'keep'}
    client.portal.call(unchanged)
    assert client.patch(f'/api/conversations/{sid}/reply-effort', headers=auth_headers, json={'effort':'max'}).status_code == 403


def test_ownership_and_admin_choices_are_preserved(client, auth_headers, admin_headers, db_sessionmaker):
    a = client.post('/api/conversations', headers=admin_headers).json()
    assert (a['reply_mode'],a['routing_mode']) == ('standard','router_code')
    b = client.post('/api/conversations/current', headers=auth_headers).json()
    subject = client.get('/api/auth/me', headers=auth_headers).json()['profile_uuid']
    assert client.get('/api/conversations/'+a['session_id'], headers=auth_headers).status_code == 404
    async def check():
        async with db_sessionmaker() as db:
            assert await effective_reply_mode(db, session_id=a['session_id'], subject_id=subject) == 'standard'
            assert await effective_reply_effort(db, session_id=a['session_id'], subject_id=subject) is None
            c = await db.scalar(select(Conversation).where(Conversation.session_id == a['session_id']))
            assert await effective_routing_mode(db, conversation=c, state={'memory':{}}, user_id=subject) == 'router_code'
            c = await db.scalar(select(Conversation).where(Conversation.session_id == b['session_id']))
            for marker in (True, 'true'):
                assert await effective_routing_mode(db, conversation=c, state={'memory':{'sandbox_mode':marker}}, user_id=subject) == 'router_code'
    client.portal.call(check)


def test_member_stream_uses_parallel_path(client, auth_headers, monkeypatch):
    from app import provisional_reply as chat
    calls = []
    original = chat.with_natural_lead
    async def wrapped(events, **kwargs):
        calls.append(kwargs['session_id'])
        async for event in original(events, **kwargs):
            yield event
    monkeypatch.setattr(chat, 'with_natural_lead', wrapped)
    c = client.post('/api/conversations/current', headers=auth_headers).json()
    response = client.post('/api/chat/stream', headers=auth_headers,
        json={'session_id':c['session_id'],'message':'我想开始散步'})
    assert response.status_code == 200, response.text
    assert calls == [c['session_id']]
    assert 'event: done' in response.text


def test_member_low_reaches_kimi_wire_policy(client, auth_headers, db_sessionmaker):
    from app.config import get_settings
    from app.providers.deepseek import DeepSeekProvider
    from app.routes.chat import _apply_conversation_thinking
    c = client.post('/api/conversations/current', headers=auth_headers).json()
    subject = client.get('/api/auth/me', headers=auth_headers).json()['profile_uuid']
    settings = get_settings().model_copy(update={'deepseek_api_key':'test-only', 'deepseek_model':'kimi-k3',
        'deepseek_base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1'})
    provider = DeepSeekProvider(settings)
    ctx = SimpleNamespace(provider=provider,router_provider=provider,knowledge_base=None)
    state = {}
    async def check():
        assert await _apply_conversation_thinking(ctx, session_id=c['session_id'], subject_id=subject,state=state) == 'ack_deep'
        assert ctx.provider.reply_effort == 'low'
        assert ctx.provider.deep_reply_policy()['thinking_options'] == {'enable_thinking':True,'reasoning_effort':'low'}
        assert ctx.router_provider.thinking_override is False
        assert state['telemetry']['deep_reply_policy']['applied_reply_effort'] == 'low'
        assert provider.reply_effort is None
        await provider._client.close()
    client.portal.call(check)


async def test_member_router_decision_commits_without_old_code_gate(router_only_db, provider):
    from app.database_v2_schema import metadata
    from app.models import ConversationMessage
    from app.pre_reply_routing import route_before_reply
    ctx, maker = router_only_db
    rt = metadata.tables['conversation_runtime_states']
    async with maker() as db:
        await db.execute(update(rt).where(rt.c.conversation_id == 2).values(memory={'routing_mode':'router_code'}))
        await db.execute(insert(ConversationMessage), {'id':201,'conversation_id':2,'position':0,'role':'user','content':'继续'})
        await db.commit()
    await ctx.store.adopt(session_id='other',messages=[],module='module_1',memory={'routing_mode':'router_code'})
    provider.route_result='module_4'
    result=await route_before_reply({'session_id':'other','subject_id':'b','user_message_id':201,
        'user_input':'继续','current_module':'module_1','forced_module':'module_2'},ctx)
    assert result['current_module']=='module_4'
    assert result['telemetry']['router_pre_reply']['routing_mode']=='router_only'
    assert result['forced_module'] is None
    async with maker() as db:
        row=(await db.execute(select(rt).where(rt.c.conversation_id==2))).mappings().one()
        assert row['current_module']=='module_4' and row['active_goal_id'] is None and row['active_cycle_id'] is None
