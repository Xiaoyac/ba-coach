import pytest
from sqlalchemy import insert, select, update
from app.database_v2_schema import metadata as schema
from app.identity import require_subject_id
from test_goal_overview import goal_api


@pytest.mark.asyncio
async def test_history_keeps_versions_cycles_and_corrected_events(goal_api):
    client, db, _ = goal_api
    plans, cycles, events = (schema.tables[n] for n in ('module_two_record','pa_cycles','pa_activity_events'))
    for i, status in enumerate(('superseded','confirmed','draft'), 1):
        await db.execute(insert(plans), {'id':f'p{i}','goal_id':'g1','version_no':i,'timezone':'Asia/Shanghai',
            'record_status':status,'confirmation_status':'confirmed' if status != 'draft' else 'unconfirmed',
            'confirmation_message_id':1,'activity_content':f'散步 {i*5} 分钟','schedule_text':'晚饭后'})
    await db.execute(insert(cycles), {'id':'c1','goal_id':'g1','ordinal':1,'status':'completed','module_two_record_id':'p1'})
    await db.execute(insert(schema.tables['module_four_record']), {'id':'r1','cycle_id':'c1',
        'review_summary':'准备减少时长','review_decision':3})
    await db.execute(insert(schema.tables['pa_review_details']), {'review_id':'r1','action':'adjust','source_message_id':1,'source_quote':'减少时长'})
    for i, status in enumerate(('superseded','active'),1):
        await db.execute(insert(events), {'id':f'e{i}','user_id':'a','goal_id':'g1','cycle_id':'c1',
            'source_conversation_id':1,'source_message_id':i,'event_index':1,'event_kind':'performed',
            'status':status,'activity_content':'散步','source_quote':'私有审计原话'})
    await db.commit()
    result = (await client.get('/api/program/goals/g1/history')).json()
    assert result['totals'] == {'plans':3,'cycles':1,'activities':2}
    assert [p['record_status'] for p in result['plans']] == ['draft','confirmed','superseded']
    assert result['cycles'][0]['plan_version'] == 1
    assert result['cycles'][0]['review_action'] == 'adjust'
    assert {e['status'] for e in result['activities']} == {'active','superseded'}
    assert 'source_quote' not in str(result) and 'user_id' not in result['goal']
    assert (await db.execute(select(plans.c.record_status).where(plans.c.id=='p3'))).scalar_one()=='draft'


@pytest.mark.asyncio
async def test_history_pagination_and_invalid_bounds(goal_api):
    client, db, _ = goal_api
    for i in range(1,5):
        await db.execute(insert(schema.tables['module_two_record']), {'id':f'p{i}', 'goal_id':'g1',
            'version_no':i, 'timezone':'Asia/Shanghai'})
    await db.commit()
    result = (await client.get('/api/program/goals/g1/history?page=2&page_size=2')).json()
    assert [p['version_no'] for p in result['plans']] == [2,1]
    assert result['totals']['plans']==4
    assert (await client.get('/api/program/goals/g1/history?page=0')).status_code==422
    assert (await client.get('/api/program/goals/g1/history?page_size=500')).status_code==422


@pytest.mark.asyncio
async def test_history_denies_foreign_unknown_and_anonymous(goal_api):
    client, _, app = goal_api
    for target in ('private','unknown'):
        assert (await client.get(f'/api/program/goals/{target}/history')).status_code==404
    del app.dependency_overrides[require_subject_id]
    assert (await client.get('/api/program/goals/g1/history')).status_code==401


@pytest.mark.asyncio
async def test_bad_cross_goal_plan_reference_does_not_leak_content(goal_api):
    client, db, _ = goal_api
    await db.execute(insert(schema.tables['module_two_record']), {'id':'other-plan','goal_id':'private',
        'version_no':1,'timezone':'Asia/Shanghai','activity_content':'other user secret'})
    await db.execute(insert(schema.tables['pa_cycles']), {'id':'c1','goal_id':'g1','ordinal':1,
        'status':'planning','module_two_record_id':'other-plan'})
    await db.commit()
    result=(await client.get('/api/program/goals/g1/history')).json()
    assert result['cycles'][0]['activity_content'] is None
    assert result['plans']==[]


@pytest.mark.asyncio
async def test_history_preserves_coping_and_numeric_difficulty_without_inventing_support(goal_api):
    client, db, _ = goal_api
    plans = schema.tables['module_two_record']
    barriers = ['下雨', '容易忘记']
    coping = [{'barrier': '下雨', 'plan': '在室内走'}, {'barrier': '容易忘记', 'plan': '饭后设置提醒'}]
    await db.execute(insert(plans), {'id': 'draft', 'goal_id': 'g1', 'version_no': 2,
        'timezone': 'Asia/Shanghai', 'potential_barriers': barriers, 'barrier_coping_plan': coping,
        'difficulty_rating': 0, 'record_status': 'draft'})
    await db.execute(insert(plans), {'id': 'legacy', 'goal_id': 'g1', 'version_no': 1,
        'timezone': 'Asia/Shanghai'})
    await db.execute(insert(schema.tables['pa_plan_details']), {'plan_id': 'legacy',
        'difficulty': '有一点难', 'resources': ['家人愿意提醒']})
    await db.commit()
    response = await client.get('/api/program/goals/g1/history')
    assert response.status_code == 200
    current, legacy = response.json()['plans']
    assert current['potential_barriers'] == barriers
    assert current['barrier_coping_plan'] == coping
    assert current['difficulty_rating'] == 0
    assert current['companion'] is None and current['resources'] is None
    assert current['record_status'] == 'draft'
    assert legacy['difficulty_rating'] is None and legacy['difficulty'] == '有一点难'
    assert legacy['resources'] == ['家人愿意提醒']
