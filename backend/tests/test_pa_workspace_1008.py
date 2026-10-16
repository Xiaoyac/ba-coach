"""Saved drafts reach the reply model; content versions exclude workflow writes."""
import json
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.database_v2_schema import metadata as schema
from app.goal_card_workspace import read_card_row, record_card_update, pause_card
from app.models import ConversationMessage
from app.reply_workflow import read_reply_workflow, workflow_prompt
from test_goal_card_workspace import setup_card, enable_card_feature
from test_goal_overview import goal_api


@pytest.mark.parametrize('kind', ['primary', 'secondary'])
async def test_saved_unsubmitted_draft_is_owned_reply_context(goal_api, kind):
    client, db, _ = goal_api
    conversation, state, user, card = await setup_card(db, kind=kind)
    fields = {'activity_content': '暂存独有信息：傍晚在阳台站立伸展', 'difficulty_rating': 0}
    result = await client.put('/api/program/chat-a/goal-card', json={
        'card_id': card['id'], 'revision': card['revision'], 'fields': fields, 'mode': 'draft'})
    assert result.status_code == 200, result.text
    saved = result.json()['card']
    assert saved['content_revision'] == 2
    row = await read_card_row(db, 1, 'a')
    assert row['submission_message_id'] is None and row['confirmation_message_id'] is None
    assert await db.scalar(select(func.count()).select_from(ConversationMessage)) == 1
    assert await db.scalar(select(func.count()).select_from(schema.tables['module_two_record'])) == 0
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    authority = await read_reply_workflow(maker, 'a', 'chat-a')
    assert authority['goal_card_draft']['fields']['activity_content'] == fields['activity_content']
    prompt = workflow_prompt(authority)
    assert fields['activity_content'] in prompt and '不等于最终确认' in prompt
    assert '暂时看不到' not in prompt
    assert (await read_reply_workflow(maker, 'b', 'chat-a')) == {'available': False}
    await pause_card(db, conversation, state, user.id)
    await db.commit()
    paused = await read_reply_workflow(maker, 'a', 'chat-a')
    assert paused['goal_card_draft']['phase'] == 'paused'
    assert fields['activity_content'] in workflow_prompt(paused)


async def test_content_counter_ignores_review_presentation_pause_and_identical_saves(goal_api):
    client, db, _ = goal_api
    conversation, state, user, card = await setup_card(db)
    async def save(revision, fields):
        result = await client.put('/api/program/chat-a/goal-card', json={
            'card_id': card['id'], 'revision': revision, 'fields': fields, 'mode': 'draft'})
        assert result.status_code == 200, result.text
        return result.json()['card']
    card = await save(1, {'activity_content': '散步', 'difficulty_rating': 0})
    count = card['content_revision']
    same = await save(card['revision'], {'activity_content': '散步', 'difficulty_rating': 0, 'location': ''})
    assert same['revision'] == card['revision'] and same['content_revision'] == count
    row = await read_card_row(db, 1, 'a')
    for changes in [{'review': {'is_pa': True}}, {'phase': 'ready', 'display_text': '核对当前安排'}, {'phase': 'discussing'}]:
        row = await record_card_update(db, row, changes, reason='test_workflow')
        assert row['content_revision'] == count
    await db.commit()
    changed = await save(row['revision'], {'activity_content': '散步五分钟', 'difficulty_rating': 0})
    assert changed['content_revision'] == count + 1
    assert changed['revision'] > changed['content_revision']
    paused = await pause_card(db, conversation, state, user.id)
    assert paused['content_revision'] == changed['content_revision']


async def test_migration_backfills_content_only_and_can_resume(tmp_path):
    from scripts.migrate_goal_card_content_revision_1008 import migrate
    url = 'sqlite+aiosqlite:///' + str(tmp_path / 'old.sqlite')
    engine = create_async_engine(url)
    async with engine.begin() as db:
        await db.execute(text('CREATE TABLE goal_card_workspaces (id TEXT PRIMARY KEY, fields TEXT, revision INTEGER)'))
        await db.execute(text('CREATE TABLE ai_decision_logs (id INTEGER PRIMARY KEY, decision_type TEXT, decision_value TEXT)'))
        await db.execute(text('INSERT INTO goal_card_workspaces VALUES (:id,:fields,12)'), [{'id':'known','fields':json.dumps({'activity_content':'散步'})},{'id':'unknown','fields':'{}'}])
        for i, fields in enumerate([{}, {'activity_content':'散步'}, {'activity_content':'散步','location':None}, {'activity_content':'散步'}], 1):
            await db.execute(text('INSERT INTO ai_decision_logs VALUES (:n,:type,:value)'), {'n':i,'type':'goal_card_revision','value':json.dumps({'id':'known','revision':i,'fields':fields})})
    assert (await migrate(url))['status'] == 'dry_run'
    result = await migrate(url, apply=True)
    assert result['rows'] == 2 and result['unknown_history'] == 1
    async with engine.begin() as db:
        rows = (await db.execute(text('SELECT id,revision,content_revision FROM goal_card_workspaces ORDER BY id'))).all()
        assert rows == [('known',12,2),('unknown',12,None)]
        await db.execute(text("UPDATE goal_card_workspaces SET content_revision=NULL WHERE id='known'"))
    await migrate(url, apply=True)
    async with engine.begin() as db:
        assert (await db.execute(text("SELECT content_revision FROM goal_card_workspaces WHERE id='known'"))).scalar_one() == 2
    await engine.dispose()


@pytest.mark.parametrize('stream', [False, True])
async def test_actual_foreground_request_contains_saved_draft_without_form_message(goal_api, context, stream):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.graph.nodes import make_module_node
    from app.graph.nodes import ModuleConfig
    from app.providers.base import Completion, StreamDelta
    from app.providers.base import as_text
    from test_pa_background_integration import worker_context, worker_state, ToolRouter
    client, db, _ = goal_api
    _, _, user, card = await setup_card(db)
    fact = '独有暂存信息：周日午后在阳台伸展七分钟'
    response = await client.put('/api/program/chat-a/goal-card', json={
        'card_id': card['id'], 'revision': card['revision'], 'fields': {'activity_content': fact}, 'mode': 'draft'})
    assert response.status_code == 200
    class Provider:
        name, model, deep_reply_enabled = 'capture-draft', 'capture-draft', False
        calls = []
        async def complete(self, **request):
            self.calls.append(request)
            return Completion(text='可以一起看看你写下的安排。', model=self.model)
        async def stream(self, **request):
            self.calls.append(request)
            yield StreamDelta(kind='content', text='可以一起看看你写下的安排。')
    provider = Provider()
    ctx = worker_context(context, db, ToolRouter(hold=True), provider=provider, stream=stream)
    ctx = replace(ctx, settings=ctx.settings.model_copy(update={'goal_card_ui_enabled': True}))
    state = {**worker_state('我暂存的活动是什么？'), 'user_message_id': user.id}
    result = await make_module_node('module_2', ModuleConfig(retrieve=False))(state, SimpleNamespace(context=ctx), writer=lambda _: None)
    assert not result.get('error'), result
    assert len(provider.calls) == 1
    assert fact in as_text(provider.calls[0]['system'])
    assert '暂存不等于最终确认' in as_text(provider.calls[0]['system'])
    assert await db.scalar(select(func.count()).select_from(ConversationMessage)) == 1


@pytest.mark.parametrize('kind', ['primary', 'secondary'])
async def test_number_free_confirm_button_keeps_exact_version_safety(goal_api, context, kind):
    from test_structured_goal_confirmation import ready_button
    from app.structured_goal_confirmation import confirm_goal_button
    from app.goal_card_interaction import structured_confirmation_evidence
    label = '核心目标' if kind == 'primary' else '次要目标'
    db, state, ctx, card, assistant = await ready_button(goal_api, context, kind=kind, text=f'确认这张{label}卡，就按这个安排。')
    user = await db.get(ConversationMessage, state['user_message_id'])
    from app.v2_workflow import runtime_for
    conversation, _ = await runtime_for(db, 'chat-a')
    evidence = await structured_confirmation_evidence(db, conversation, user)
    assert evidence is not None
