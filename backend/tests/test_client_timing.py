import pytest
from sqlalchemy import insert, select
from app.models import AIExecutionEvent, ConversationMessage
from test_execution_drawer import prepare_api
from test_goal_overview import goal_api

async def test_client_timing_persists_and_is_idempotent(goal_api):
    client, db, _ = await prepare_api(goal_api)
    path='/api/conversations/chat-a/execution/client-timing'
    result=await client.post(path,json={'user_message_id':21,'first_body_ms':2345})
    assert result.status_code==200 and result.json()['first_body_ms']==2345
    assert (await client.post(path,json={'user_message_id':21,'first_body_ms':9999})).json()['first_body_ms']==2345
    events=(await db.scalars(select(AIExecutionEvent).where(AIExecutionEvent.stage=='client_response_display'))).all()
    assert len(events)==1 and events[0].event_metadata['measurement']=='client_click_to_body_paint'
    value=(await client.get('/api/conversations/chat-a/execution')).json()
    assert value['client_first_body_ms']==2345
    assert all(row['stage']!='client_response_display' for row in value['stages'])
    await db.execute(insert(ConversationMessage),{'id':23,'conversation_id':1,'position':3,'role':'user','content':'下一轮'})
    await db.commit()
    assert (await client.get('/api/conversations/chat-a/execution')).json()['client_first_body_ms'] is None
    assert (await client.get('/api/conversations/chat-a/execution?user_message_id=21')).json()['client_first_body_ms']==2345

@pytest.mark.parametrize('role,session,message_id,status',[
    ('user','chat-a',21,403),('admin','chat-b',21,404),('admin','chat-a',999,404)])
async def test_client_timing_is_admin_owned_turn_only(goal_api,role,session,message_id,status):
    client,_,_=await prepare_api(goal_api,role)
    response=await client.post(f'/api/conversations/{session}/execution/client-timing',json={'user_message_id':message_id,'first_body_ms':10})
    assert response.status_code==status

@pytest.mark.parametrize('value',[-1,86400001,True,'50',1.5,None])
async def test_reject_invalid_duration(goal_api,value):
    client,_,_=await prepare_api(goal_api)
    response=await client.post('/api/conversations/chat-a/execution/client-timing',json={'user_message_id':21,'first_body_ms':value})
    assert response.status_code==422

async def test_zero_and_legacy_missing_are_distinct(goal_api):
    client,db,_=await prepare_api(goal_api)
    await db.execute(insert(AIExecutionEvent),{'conversation_id':1,'subject_id':'a','stage':'main_generation',
        'duration_ms':600,'event_metadata':{'user_message_id':21,'time_to_first_body_content_ms':100}})
    await db.commit()
    assert (await client.get('/api/conversations/chat-a/execution')).json()['client_first_body_ms'] is None
    await client.post('/api/conversations/chat-a/execution/client-timing',json={'user_message_id':21,'first_body_ms':0})
    assert (await client.get('/api/conversations/chat-a/execution')).json()['client_first_body_ms']==0
