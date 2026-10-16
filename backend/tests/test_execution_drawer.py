import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import insert
from app.ai_telemetry import request_event_scope
from app.execution_status import _turns, snapshot, track_call, update
from app.identity import require_caller, CallerIdentity
from app.models import AIExecutionEvent, ConversationMessage
from app.routes import conversations
from test_goal_overview import goal_api

@pytest.fixture(autouse=True)
def clean_progress():
    _turns.clear()
    yield
    _turns.clear()

async def test_parallel_status_survives_foreground_completion():
    entered, release = asyncio.Event(), asyncio.Event()
    async def work():
        async with request_event_scope('s', 21):
            update('pa_background_tools', 'running', model='background')
            entered.set()
            await release.wait()
            update('pa_background_tools', 'completed')
    async with request_event_scope('s', 21):
        task = asyncio.create_task(work())
        await entered.wait()
        update('main_generation', 'completed', model='main')
    status = snapshot('s', 21)
    assert status['request_finished']
    assert status['stages']['pa_background_tools']['status'] == 'running'
    release.set();await task
    assert snapshot('s', 21)['stages']['pa_background_tools']['status'] == 'completed'

async def test_failed_and_cancelled_calls_are_not_left_running():
    async def fail(): raise RuntimeError('private provider response')
    async with request_event_scope('s', 21):
        with pytest.raises(RuntimeError): await track_call('risk_gate', fail(), model='risk')
        task = asyncio.create_task(track_call('reply_lead', asyncio.sleep(30), model='lead'))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    rows = snapshot('s', 21)['stages']
    assert rows['risk_gate']['status'] == 'failed'
    assert rows['reply_lead']['status'] == 'cancelled'
    assert 'private provider' not in str(rows)

async def prepare_api(goal_api, role='admin', subject='a'):
    client, db, app = goal_api
    app.include_router(conversations.router, prefix='/api')
    caller = CallerIdentity(account=SimpleNamespace(profile_uuid=subject, settings=SimpleNamespace(role=role)), session=None)
    app.dependency_overrides[require_caller] = lambda: caller
    await db.execute(insert(ConversationMessage), {'id':21, 'conversation_id':1, 'position':1, 'role':'user', 'content':'计划怎么安排？'})
    await db.commit()
    return client, db, app

@pytest.mark.parametrize('role,session,status', [('user','chat-a',403),('admin','chat-b',404),('admin','chat-a',200)])
async def test_server_enforces_admin_and_owned_conversation(goal_api, role, session, status):
    client, _, _ = await prepare_api(goal_api, role)
    response = await client.get('/api/conversations/'+session+'/execution')
    assert response.status_code == status
    if status == 200: assert response.headers['cache-control'] == 'private, no-store'

async def test_no_login_is_rejected(goal_api):
    client, _, app = goal_api
    app.include_router(conversations.router, prefix='/api')
    response = await client.get('/api/conversations/chat-a/execution')
    assert response.status_code == 401

async def test_live_and_history_status_exclude_prompt_and_reasoning(goal_api):
    client, db, _ = await prepare_api(goal_api)
    await db.execute(insert(AIExecutionEvent), {'conversation_id':1,'session_id':'chat-a','subject_id':'a',
        'stage':'risk_gate','model_name':'risk-model','duration_ms':250,
        'event_metadata':{'user_message_id':21,'main_input':{'system':'PRIVATE PROMPT'},'reasoning_content':'PRIVATE REASONING'}})
    await db.commit()
    async with request_event_scope('chat-a',21):
        update('main_generation','running',model='main-model')
        response = await client.get('/api/conversations/chat-a/execution')
        assert response.status_code == 200
        data=response.json()
        assert data['state']=='running'
        assert next(s for s in data['stages'] if s['stage']=='main_generation')['status']=='running'
        assert next(s for s in data['stages'] if s['stage']=='risk_gate')['status']=='completed'
        assert 'PRIVATE' not in response.text
        update('main_generation','completed')
    # Simulate a restart: history remains inspectable without stale running rows.
    _turns.clear()
    data=(await client.get('/api/conversations/chat-a/execution')).json()
    assert not data['live']
    assert all(s['status']!='running' for s in data['stages'])
    assert (await client.get('/api/conversations/chat-a/execution?user_message_id=999')).status_code == 404

async def test_registry_is_bounded_and_snapshots_are_isolated():
    from app.execution_status import begin
    for i in range(600):begin('s',i)
    assert len(_turns)==512
    original=snapshot('s',599);original['stages']['fake']={}
    assert 'fake' not in snapshot('s',599)['stages']

@pytest.mark.parametrize('state,expected', [('bypassed','skipped'), ('fallback','failed'), ('completed','completed')])
async def test_event_status_does_not_claim_fallback_or_bypass_completed(state, expected):
    from app.execution_status import record_event
    from app.execution_status_view import event_row
    async with request_event_scope('s', 21):
        record_event('knowledge_mediator', session_id='s', metadata={'user_message_id':21,'status':state})
        assert snapshot('s',21)['stages']['knowledge_mediator']['status'] == expected
    row=event_row(SimpleNamespace(stage='knowledge_mediator',event_metadata={'status':state},error_code=None,model_name=None,duration_ms=0))
    assert row['status']==expected

@pytest.mark.parametrize('status,code,expected', [
    (429, 'RateLimitError', '限流'), (401, 'AuthenticationError', '身份验证'),
    (503, 'APIStatusError', '上游服务'), (None, 'APITimeoutError', '等待时限'),
    (None, 'APIConnectionError', '连接'), (None, 'invalid_router_json', '格式'),
])
def test_failure_explanations_are_structured_not_raw(status, code, expected):
    from app.execution_errors import failure_info, safe_failure
    result = failure_info(code, status=status, request_id='request-123')
    assert expected in result['reason']
    assert result['request_id'] == 'request-123'
    assert 'SECRET' not in str(safe_failure({'code': 'invalid code SECRET', 'reason': 'SECRET',
        'request_id': 'Bearer SECRET', 'http_status': 'SECRET'}))

async def test_wrapped_provider_failure_survives_event_and_reload(goal_api):
    from app.ai_telemetry import add_ai_event
    from app.providers.base import ProviderError
    client, db, _ = await prepare_api(goal_api)
    class RateLimitError(Exception):
        status_code = 429
        request_id = 'req-test-429'
    async def fail():
        try:
            raise RateLimitError('SECRET BODY AND API KEY')
        except RateLimitError as exc:
            raise ProviderError('SECRET WRAPPED RESPONSE') from exc
    async with request_event_scope('chat-a', 21):
        with pytest.raises(ProviderError):
            await track_call('main_generation', fail(), model='kimi-k3')
        await add_ai_event(db, stage='main_generation', session_id='chat-a', subject_id='a',
            provider='deepseek', model_name='kimi-k3', error_code='provider_error')
        await db.commit()
        live = (await client.get('/api/conversations/chat-a/execution')).json()
        assert live['stages'][0]['failure']['http_status'] == 429
    _turns.clear()
    history = (await client.get('/api/conversations/chat-a/execution')).json()
    assert history['stages'][0]['failure']['request_id'] == 'req-test-429'
    assert history['stages'][0]['failure'] == live['stages'][0]['failure']
    assert 'SECRET' not in str(history)

async def test_recovered_stage_clears_failure_and_old_errors_are_honest():
    from app.execution_status_view import event_row
    async with request_event_scope('s', 21):
        update('main_generation', 'failed', failure={'code': 'TimeoutError'})
        update('main_generation', 'running')
        update('main_generation', 'completed')
        assert 'failure' not in snapshot('s',21)['stages']['main_generation']
    row = event_row(SimpleNamespace(stage='main_generation', event_metadata={},
        error_code='provider_error', model_name='kimi-k3', duration_ms=50))
    assert '未提供更具体' in row['failure']['reason']
