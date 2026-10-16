"""PA work overlaps prose and safely crosses the reply persistence boundary."""
import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import insert, select
from app.database_v2_schema import metadata as schema
from app.graph.nodes import ModuleConfig, make_module_node, schedule_background_routing
from app.models import AIExecutionEvent, ConversationMessage
from app.pa_reply_preparation import prepare_pa_reply_node
from app.providers.base import as_text
from test_goal_overview import goal_api
from test_pa_background_integration import REPLY, ToolRouter, finish_task, persist_reply, worker_context, worker_state
from test_turn_confirmation_0924 import setup_turn

TEXT = '地点不变，时间改成五分钟。'

async def launch(context, db, router, text=TEXT):
    ctx = worker_context(context, db, router)
    if await ctx.store.get('chat-a') is None:
        await ctx.store.adopt('chat-a', [], 'module_2', {})
    state = worker_state(text)
    update = await asyncio.wait_for(prepare_pa_reply_node(state, SimpleNamespace(context=ctx)), .5)
    assert update['pa_background_pending'] and not update['pa_operations_completed']
    assert ctx.pa_tools_started
    assert update['telemetry']['pa_background_tools']['timing'] == 'parallel_with_main_reply'
    return ctx, {**state, **update}, ctx.pa_background_task

async def second_request(router):
    while len(router.requests) < 2:
        await asyncio.sleep(.01)

async def duration(db):
    await db.rollback()
    plan = (await db.execute(select(schema.tables['module_two_record']))).mappings().one()
    return plan['duration_minutes']

@pytest.mark.parametrize('fail', [False, True])
async def test_main_finishes_while_writer_generates(goal_api, context, fail):
    _, db, _ = goal_api
    await setup_turn(db, TEXT)
    router = ToolRouter(action='save_pa_card', data={'target_activity_duration_minutes': 5}, hold=True, fail=fail)
    ctx, state, task = await launch(context, db, router)
    try:
        await asyncio.wait_for(router.entered.wait(), 2)
        result = await asyncio.wait_for(make_module_node('module_2', ModuleConfig(retrieve=False))(
            state, SimpleNamespace(context=ctx), writer=lambda _: None), 3)
        assert result['final_response'] and not result.get('error')
        assert not task.done() and not router.release.is_set()
        assert '独立后台处理' in as_text(ctx.provider.systems[-1])
        assert result['telemetry']['pa_background_tools']['status'] == 'running_in_parallel'
        await persist_reply(db)
        schedule_background_routing({**state, **result}, ctx, assistant_message_id=22)
        assert ctx.pa_background_task is task
    finally:
        router.release.set()
        await finish_task(task)
    assert (await duration(db) == 5) is not fail
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == REPLY
    event = (await db.execute(select(AIExecutionEvent).where(AIExecutionEvent.stage == 'pa_background_tools'))).scalars().one()
    assert event.event_metadata['pa_background_tools']['timing'] == 'parallel_with_main_reply'
    assert bool(event.error_code) == fail

async def test_writer_commits_before_main_reply(goal_api, context):
    _, db, _ = goal_api
    await setup_turn(db, TEXT)
    router = ToolRouter(action='save_pa_card', data={'target_activity_duration_minutes': 5})
    await context.store.adopt('chat-a', [], 'module_2', {})
    lock = await context.store.get_turn_lock('chat-a')
    await lock.acquire()
    ctx, _, task = await launch(context, db, router)
    try:
        await asyncio.wait_for(second_request(router), 3)
        assert await duration(db) == 5
        assert await db.get(ConversationMessage, 22) is None
        assert not task.done()  # Only display/cache publishing waits for prose.
        await persist_reply(db)
    finally:
        lock.release()
        await finish_task(task)
    event = (await db.execute(select(AIExecutionEvent).where(AIExecutionEvent.stage == 'pa_background_tools'))).scalars().one()
    assert event.assistant_message_id == 22
    assert event.event_metadata['user_message_id'] == 21

@pytest.mark.parametrize('reply_first', [False, True])
async def test_new_user_invalidates_writer(goal_api, context, reply_first):
    _, db, _ = goal_api
    await setup_turn(db, TEXT)
    router = ToolRouter(action='save_pa_card', data={'target_activity_duration_minutes': 5}, hold=True)
    ctx, _, task = await launch(context, db, router)
    try:
        await asyncio.wait_for(router.entered.wait(), 2)
        if reply_first:
            await persist_reply(db)
        await db.execute(insert(ConversationMessage), {'id': 23, 'conversation_id': 1,
            'position': 4 if reply_first else 3, 'role': 'user', 'content': '等等，不要改。'})
        await db.commit()
    finally:
        router.release.set()
        await finish_task(task)
    assert await duration(db) != 5

async def test_explicit_cancel_stops_writer(goal_api, context):
    from app.generation_control import GenerationControl
    _, db, _ = goal_api
    await setup_turn(db, TEXT)
    context.generation = GenerationControl()
    router = ToolRouter(action='save_pa_card', data={'target_activity_duration_minutes': 5}, hold=True)
    ctx, _, task = await launch(context, db, router)
    await asyncio.wait_for(router.entered.wait(), 2)
    ctx.generation.stop.set()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
    assert task.cancelled()
    assert await duration(db) != 5

@pytest.mark.parametrize('reply_first', [False, True])
async def test_card_display_binds_once_to_own_reply(goal_api, context, reply_first, monkeypatch):
    _, db, _ = goal_api
    await setup_turn(db, '请再展示计划')
    router = ToolRouter(action='present_pa_card', hold=reply_first)
    from app import pa_background
    publish_entered = asyncio.Event()
    publish = pa_background._publish
    async def observed_publish(*args, **kwargs):
        publish_entered.set()
        return await publish(*args, **kwargs)
    monkeypatch.setattr(pa_background, '_publish', observed_publish)
    await context.store.adopt('chat-a', [], 'module_2', {})
    lock = await context.store.get_turn_lock('chat-a')
    await lock.acquire()
    ctx, _, task = await launch(context, db, router, '请再展示计划')
    try:
        await asyncio.wait_for(router.entered.wait(), 2)
        if not reply_first:
            await asyncio.wait_for(publish_entered.wait(), 3)
        await persist_reply(db)
        from app.schemas import Message
        assistant = await db.get(ConversationMessage, 22)
        await ctx.store.append('chat-a', Message(role='assistant', content=REPLY, created_at=assistant.created_at))
    finally:
        lock.release()
        router.release.set()
        await finish_task(task)
    await db.rollback()
    content = await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22))
    assert content.startswith(REPLY) and len(content) > len(REPLY)
    assert (await ctx.store.get('chat-a')).messages[-1].content == content

async def test_semantic_confirmation_does_not_lock_out_main_persistence(goal_api):
    import json
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.pa_card_tools import PACardTools
    from app.v2_workflow import runtime_for
    from test_pa_native_tools import call
    _, db, _ = goal_api
    wording = '我看完了所有安排，没有要改的地方，愿意照这一版去执行。'
    await setup_turn(db, wording)
    opened = 0
    entered, release = asyncio.Event(), asyncio.Event()
    class TrackedSession(AsyncSession):
        async def __aenter__(self):
            nonlocal opened
            opened += 1
            return await super().__aenter__()
        async def __aexit__(self, *args):
            nonlocal opened
            try:
                return await super().__aexit__(*args)
            finally:
                opened -= 1
    class SemanticProvider:
        async def route(self, **kwargs):
            assert opened == 0
            entered.set()
            await release.wait()
            assert opened == 0
            return json.dumps({'intent': 'confirm', 'source_quote': wording,
                               'amendment': False, 'unresolved': False}, ensure_ascii=False)
    tool = PACardTools(maker=async_sessionmaker(db.bind, class_=TrackedSession, expire_on_commit=False),
        session_id='chat-a', user_id='a', user_message_id=21, module='module_2',
        provider=SemanticProvider(), parallel_reply=True)
    snapshot = await tool.execute(call('get_pa_card', {}))
    task = asyncio.create_task(tool.execute(call('confirm_pa_card', {'state_version': snapshot['state_version']})))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(persist_reply(db), 1)
    finally:
        release.set()
    result = await asyncio.wait_for(task, 3)
    assert result['status'] == 'confirmed', result
    assert tool.assistant_message_id == 22
    await db.rollback()
    _, actual = await runtime_for(db, 'chat-a')
    assert actual['current_module'] == 'module_3'
    plan = (await db.execute(select(schema.tables['module_two_record']))).mappings().one()
    assert plan['confirmation_message_id'] == 21

@pytest.mark.parametrize('invalid', ['owner', 'extra_assistant', 'gap'])
async def test_parallel_boundary_cannot_adopt_unrelated_reply(goal_api, invalid):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.pa_card_tools import PACardTools
    from test_pa_native_tools import call
    _, db, _ = goal_api
    await setup_turn(db, TEXT)
    await persist_reply(db, position=4 if invalid == 'gap' else 3)
    if invalid == 'extra_assistant':
        await persist_reply(db, message_id=23, position=4)
    tool = PACardTools(maker=async_sessionmaker(db.bind, expire_on_commit=False),
        session_id='chat-a', user_id='b' if invalid == 'owner' else 'a',
        user_message_id=21, module='module_2', parallel_reply=True)
    result = await tool.execute(call('get_pa_card', {}))
    assert result['status'] == 'blocked', result

async def test_confirmation_commit_survives_foreground_old_runtime_snapshot(goal_api, context, monkeypatch):
    from app import pa_background
    from app.routes.chat import _finalize_pa_display
    from app.v2_workflow import runtime_for
    _, db, _ = goal_api
    await setup_turn(db, '确认，就按这个计划试试。')
    await context.store.adopt('chat-a', [], 'module_2', {})
    lock = await context.store.get_turn_lock('chat-a')
    await lock.acquire()
    entered = asyncio.Event()
    publish = pa_background._publish
    async def observed_publish(*args, **kwargs):
        entered.set()
        return await publish(*args, **kwargs)
    monkeypatch.setattr(pa_background, '_publish', observed_publish)
    ctx, _, task = await launch(context, db, ToolRouter(action='confirm_pa_card'), '确认，就按这个计划试试。')
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await db.rollback()
        _, actual = await runtime_for(db, 'chat-a')
        assert actual['current_module'] == 'module_3'
        await db.rollback()
        # Prose was generated from the earlier M2 snapshot. Its completion
        # must reload the committed card state instead of writing that back.
        await ctx.store.set_module('chat-a', 'module_2')
        await ctx.store.set_memory('chat-a', {'old_foreground_snapshot': True})
        await persist_reply(db)
        assert ctx.pa_tools_started
        await _finalize_pa_display(db, store=ctx.store, session_id='chat-a',
                                  subject_id='a', assistant_message_id=22)
        assert (await ctx.store.get('chat-a')).module == 'module_3'
        assert not (await ctx.store.get('chat-a')).memory.get('old_foreground_snapshot')
    finally:
        lock.release()
        await finish_task(task)
    await db.rollback()
    _, actual = await runtime_for(db, 'chat-a')
    assert actual['current_module'] == 'module_3'
