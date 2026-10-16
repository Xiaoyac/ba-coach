import pytest
from sqlalchemy import insert
from app.models import AIExecutionEvent, ConversationMessage
from app.routes import conversations
from test_execution_drawer import prepare_api
from test_goal_overview import goal_api


async def test_history_paginates_all_older_turns_and_details(goal_api):
    client, db, _ = await prepare_api(goal_api)
    await db.execute(insert(ConversationMessage), [dict(id=100+i, conversation_id=1,
        position=2+i, role='user', content=f'第{i}轮') for i in range(26)])
    await db.execute(insert(AIExecutionEvent), dict(conversation_id=1, subject_id='a',
        stage='client_response_display', duration_ms=4321,
        event_metadata={'user_message_id':21, 'measurement':'client_click_to_body_paint'}))
    await db.commit()
    first = await client.get('/api/conversations/chat-a/execution/history')
    assert first.headers['cache-control'] == 'private, no-store'
    page = first.json()
    assert page['current_user_message_id'] == 125
    assert len(page['turns']) == 20
    assert 125 not in [t['id'] for t in page['turns']]
    ids = [t['id'] for t in page['turns']]
    page = (await client.get('/api/conversations/chat-a/execution/history', params={'before_id':page['next_before_id']})).json()
    ids += [t['id'] for t in page['turns']]
    assert ids == list(range(124, 99, -1)) + [21]
    assert page['next_before_id'] is None
    detail = await client.get('/api/conversations/chat-a/execution?user_message_id=21')
    assert detail.status_code == 200
    assert detail.json()['client_first_body_ms'] == 4321
    assert (await client.get('/api/conversations/chat-a/execution')).json()['user_message_id'] == 125


async def test_history_empty_and_first_turn_excluded(goal_api):
    client, _, _ = await prepare_api(goal_api)
    page = (await client.get('/api/conversations/chat-a/execution/history')).json()
    assert page['turns'] == [] and page['next_before_id'] is None
    assert page['current_user_message_id'] == 21


@pytest.mark.parametrize('role,session,status', [('user','chat-a',403),('admin','chat-b',404)])
async def test_history_requires_admin_and_owned_chat(goal_api,role,session,status):
    client, _, _ = await prepare_api(goal_api, role)
    assert (await client.get(f'/api/conversations/{session}/execution/history')).status_code == status


async def test_history_requires_login(goal_api):
    client, _, app = goal_api
    app.include_router(conversations.router, prefix='/api')
    assert (await client.get('/api/conversations/chat-a/execution/history')).status_code == 401


@pytest.mark.parametrize('query,status', [('before_id=999',404),('before_id=0',422),('limit=0',422),('limit=101',422)])
async def test_history_rejects_invalid_cursor_and_page_size(goal_api,query,status):
    client, _, _ = await prepare_api(goal_api)
    assert (await client.get('/api/conversations/chat-a/execution/history?'+query)).status_code == status


async def test_old_turn_lookup_never_crosses_chat_or_message_role(goal_api):
    client, db, _ = await prepare_api(goal_api)
    await db.execute(insert(ConversationMessage), [dict(id=22,conversation_id=1,position=2,role='assistant',content='回答'),
        dict(id=100,conversation_id=2,position=1,role='user',content='另一用户')])
    await db.commit()
    for message in [22,100]:
        assert (await client.get(f'/api/conversations/chat-a/execution?user_message_id={message}')).status_code == 404
        assert (await client.get(f'/api/conversations/chat-a/execution/history?before_id={message}')).status_code == 404
