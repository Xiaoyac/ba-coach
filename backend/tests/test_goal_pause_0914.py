import pytest
from sqlalchemy import insert, select, update
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from test_goal_overview import goal_api
from test_cycle_program_0914 import seed_review, confirmation


async def action(db, value):
    # Bind the follow-up action to the actual decision utterance produced by
    # seed_review.  M4 confirmation now rejects synthetic source quotes.
    source = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.id == 18))).scalar_one()
    await db.execute(insert(schema.tables['pa_review_details']), {'review_id': 'review-draft',
        'action': value, 'source_message_id': source.id, 'source_quote': source.content})
    goals = schema.tables['pa_goals']
    await db.execute(update(goals).where(goals.c.id == 'g1').values(current_plan_record_id='cycle-plan'))
    await db.commit()


@pytest.mark.asyncio
async def test_legacy_pause_followup_does_not_pause_review_closure(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=4)
    await action(db, 'pause')
    response = await client.post('/api/program/chat-a/confirm', json=await confirmation(client))
    assert response.status_code == 200, response.text
    state = response.json()['runtime']
    assert state['current_module'] == 'module_2'
    assert state['active_cycle_id'] is None
    assert next(g for g in response.json()['goals'] if g['id'] == 'g1')['status'] == 'active'
    response = await client.post('/api/program/new-chat-a/goal', json={'goal_id': 'g1', 'row_version': 0})
    assert response.status_code == 200, response.text
    state = response.json()['runtime']
    assert state['current_module'] == 'module_2'
    cycle = (await db.execute(select(schema.tables['pa_cycles']).where(
        schema.tables['pa_cycles'].c.id == state['active_cycle_id']))).mappings().one()
    assert cycle['ordinal'] == 2 and cycle['module_two_record_id'] != 'cycle-plan'
    assert cycle['module_three_record_id'] is None


@pytest.mark.asyncio
async def test_legacy_replace_followup_keeps_goal_and_new_selection_replans(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=2)
    await action(db, 'replace_keep')
    response = await client.post('/api/program/chat-a/confirm', json=await confirmation(client))
    assert response.status_code == 200, response.text
    assert response.json()['runtime']['active_goal_id'] is None
    goal = next(g for g in response.json()['goals'] if g['id'] == 'g1')
    assert goal['status'] == 'active'
    resumed = await client.post('/api/program/new-chat-a/goal', json={'goal_id': 'g1', 'row_version': 0})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['runtime']['current_module'] == 'module_2'


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', [2, 4])
async def test_missing_followup_evidence_never_closes_goal(goal_api, decision):
    client, db, _ = goal_api
    await seed_review(db, decision=decision)
    state = (await client.get('/api/program/chat-a')).json()
    assert state['can_confirm'] and 'review_followup' not in state['missing_fields']
    assert (await client.post('/api/program/chat-a/confirm', json=await confirmation(client))).status_code == 200
    assert (await db.execute(select(schema.tables['pa_goals'].c.status).where(
        schema.tables['pa_goals'].c.id == 'g1'))).scalar_one() == 'active'
