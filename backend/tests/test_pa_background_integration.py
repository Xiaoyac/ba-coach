"""The PA worker owns tools; durable replies and later turns stay independent."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.graph.nodes import ModuleConfig, make_module_node, route_next_module_node
from app.models import AIExecutionEvent, Conversation, ConversationMessage
from app.pa_card_tools import PACardTools
from app.providers.base import Completion, ProviderError, StreamDelta
from app.schemas import Message
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api
from test_turn_confirmation_0924 import setup_turn


REPLY = "好，我们继续看看这个计划。"


def tool_call(name, arguments=None):
    return {"id": f"call-{name}", "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}


async def persist_reply(db, *, message_id=22, position=3, content=REPLY):
    await db.execute(insert(ConversationMessage), {"id": message_id, "conversation_id": 1,
        "position": position, "role": "assistant", "content": content})
    await db.commit()


def background_executor(db, *, user_id="a", assistant_message_id=22):
    return PACardTools(maker=async_sessionmaker(db.bind, expire_on_commit=False),
        session_id="chat-a", user_id=user_id, user_message_id=21,
        assistant_message_id=assistant_message_id, module="module_2")


def worker_state(text="请再展示计划"):
    return {"session_id": "chat-a", "subject_id": "a", "user_message_id": 21,
        "user_input": text, "memory": {}, "current_module": "module_2",
        "extracted_intent": "module_2", "next_module": "module_2",
        "final_response": REPLY, "pa_background_pending": True, "telemetry": {}}


def worker_context(context, db, router, **overrides):
    return replace(context, router_provider=router,
        sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        settings=context.settings.model_copy(update={"database_schema_version": "v2",
            "pa_card_tools_enabled": True, "goal_card_ui_enabled": False,
            "knowledge_mediator_enabled": False}),
        prompt_snapshot={"global": "effective global", "module_2": "effective M2",
            "module_3": "effective M3", "module_4": "effective M4"}, **overrides)


class ToolRouter:
    """Query the real database, then submit a scripted, version-bound action."""
    name = "background-test"
    model = "background-model"
    supports_native_tools = True

    def __init__(self, action="present_pa_card", data=None, *, arguments=None, fail=False, hold=False):
        self.action, self.data = action, data
        self.arguments = arguments or {}
        self.fail, self.hold = fail, hold
        self.requests = []
        self.thinking = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def with_thinking(self, enabled):
        self.thinking.append(enabled)
        return self

    async def stream_tools(self, **request):
        self.requests.append(request)
        self.entered.set()
        if self.hold:
            await self.release.wait()
        if self.fail:
            raise ProviderError("background unavailable")
        results = [json.loads(message["content"]) for message in request["messages"]
            if message["role"] == "tool"]
        if not results:
            command = tool_call("get_pa_card")
        elif (len(results) == 1 and results[0]["status"] == "ok"
                and self.action != "continue_pa_conversation"):
            args = {"state_version": results[0]["state_version"]}
            args.update(self.arguments)
            if self.data is not None:
                args["data"] = self.data
            command = tool_call(self.action, args)
        else:
            command = tool_call("continue_pa_conversation")
        yield StreamDelta(kind="tool_calls", tool_calls=[command])
        yield StreamDelta(kind="usage", request_id=f"background-{len(self.requests)}",
            usage={"input_tokens": 7, "output_tokens": 2})


async def finish_task(task):
    try:
        await asyncio.wait_for(asyncio.shield(task), 3)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_owned_tools_confirm_after_own_durable_reply_and_replay(goal_api):
    _, db, _ = goal_api
    await setup_turn(db, "确认，就按这个计划试试。")
    await persist_reply(db)
    tool = background_executor(db)
    snapshot = await tool.execute(tool_call("get_pa_card"))
    assert snapshot["status"] == "ok", snapshot
    command = tool_call("confirm_pa_card", {"state_version": snapshot["state_version"]})
    result = await tool.execute(command)
    assert result["status"] == "confirmed", result
    assert (await tool.execute(command))["replayed"]
    await db.rollback()
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_3"
    plan = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
    assert plan["confirmation_message_id"] == 21
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == REPLY


async def test_owned_tools_save_user_sourced_draft_after_reply(goal_api):
    _, db, _ = goal_api
    await setup_turn(db, "我想改成五分钟。")
    await persist_reply(db)
    tool = background_executor(db)
    snapshot = await tool.execute(tool_call("get_pa_card"))
    result = await tool.execute(tool_call("save_pa_card", {
        "state_version": snapshot["state_version"], "data": {"target_activity_duration_minutes": 5}}))
    assert result["status"] == "draft_saved", result
    await db.rollback()
    plan = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
    assert plan["duration_minutes"] == 5
    assert plan["record_status"] != "confirmed"
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == REPLY


async def test_background_tools_create_new_goal_from_user_not_generated_reply(goal_api):
    _, db, _ = goal_api
    text = "我选晚饭后散步十分钟，每天一次，难度4分，下雨就在室内走。"
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_2"))
    await db.execute(insert(ConversationMessage), {"id": 21, "conversation_id": 1,
        "position": 2, "role": "user", "content": text})
    await persist_reply(db, content="那就游泳三十分钟吧。")
    tool = background_executor(db)
    snapshot = await tool.execute(tool_call("get_pa_card"))
    assert [row["message_id"] for row in snapshot["recent_sources"]] == [21]
    data = {"target_activity_content": "散步十分钟", "schedule_text": "晚饭后",
        "target_activity_duration_minutes": 10, "difficulty_rating": 4,
        "difficulty_evidence": {"rating": {"message_id": 21, "quote": "难度4分", "score_text": "4"}},
        "potential_barriers": ["下雨"], "barrier_coping_plan": [{"barrier": "下雨", "plan": "室内走"}],
        "goal_proposal": {"selection_status": "selected", "selection_role": "core", "goal_kind": "primary",
            "selection_message_id": 21, "selection_quote": text, "activity_quote": "散步十分钟"}}
    result = await tool.execute(tool_call("save_pa_card", {"state_version": snapshot["state_version"], "data": data}))
    assert result["status"] == "draft_saved", result
    await db.rollback()
    plan = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
    assert plan["activity_content"] == "散步十分钟" and plan["duration_minutes"] == 10


async def test_background_tools_close_review_after_reply_with_prior_evidence(goal_api):
    from test_pa_native_tools import review_for_tool
    _, db, _ = goal_api
    data = await review_for_tool(db)
    await persist_reply(db, position=12)
    tool = PACardTools(maker=async_sessionmaker(db.bind, expire_on_commit=False),
        session_id="chat-a", user_id="a", user_message_id=21,
        assistant_message_id=22, module="module_4")
    snapshot = await tool.execute(tool_call("get_pa_card"))
    assert snapshot["status"] == "ok", snapshot
    result = await tool.execute(tool_call("close_pa_card", {"state_version": snapshot["state_version"],
        "data": data, "summary": "这次散步完成了，行动后感觉轻松，你理解了先行动再观察状态的经验。"}))
    assert result["status"] == "closed", result
    await db.rollback()
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_2" and actual["active_cycle_id"] is None
    assert await db.scalar(select(schema.tables["pa_cycles"].c.status)) == "completed"


@pytest.mark.parametrize("changed", ["owner", "new_user", "missing_reply", "missing_user", "extra_assistant"])
async def test_post_reply_tools_reject_changed_or_unowned_boundary(goal_api, changed):
    _, db, _ = goal_api
    await setup_turn(db, "确认")
    await persist_reply(db)
    if changed == "new_user":
        await db.execute(insert(ConversationMessage), {"id": 23, "conversation_id": 1,
            "position": 4, "role": "user", "content": "等等，先别确认"})
    elif changed == "missing_reply":
        await db.execute(delete(ConversationMessage).where(ConversationMessage.id == 22))
    elif changed == "missing_user":
        await db.execute(delete(ConversationMessage).where(ConversationMessage.id == 21))
    elif changed == "extra_assistant":
        await persist_reply(db, message_id=23, position=4)
    await db.commit()
    tool = background_executor(db, user_id="b" if changed == "owner" else "a")
    result = await tool.execute(tool_call("get_pa_card"))
    assert result["status"] == "blocked", result
    result = await tool.execute(tool_call("confirm_pa_card", {"state_version": 0}))
    assert result["status"] == "blocked", result
    await db.rollback()
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_2"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("module", ["module_2", "module_4"])
@pytest.mark.parametrize("reply_mode", ["ordinary", "ack_deep_max"])
async def test_foreground_reply_never_calls_tools(goal_api, context, stream, module, reply_mode):
    _, db, _ = goal_api
    await setup_turn(db, "请继续")
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module=module))
    await db.commit()

    class Foreground:
        name = "foreground-test"
        model = "foreground-model"
        calls = []

        async def complete(self, **request):
            self.calls.append(("complete", request))
            return Completion(text=REPLY, model=self.model)

        async def stream(self, **request):
            self.calls.append(("stream", request))
            yield StreamDelta(kind="content", text=REPLY)

        async def stream_tools(self, **request):
            pytest.fail("foreground model must never receive PA tools")
            yield  # pragma: no cover

    foreground = Foreground()
    foreground.deep_reply_enabled = reply_mode == "ack_deep_max"
    foreground.reply_effort = "max" if foreground.deep_reply_enabled else None
    router = ToolRouter(hold=True)
    ctx = worker_context(context, db, router, provider=foreground, stream=stream)
    state = {**worker_state("请继续"), "current_module": module, "extracted_intent": module}
    if reply_mode == "ack_deep_max":
        ctx.reply_lead_task = asyncio.get_running_loop().create_future()
        ctx.reply_lead_task.set_result({"text": ""})
        state["telemetry"] = {"reply_mode": "ack_deep"}
    events = []
    result = await asyncio.wait_for(make_module_node(module, ModuleConfig(retrieve=False))(
        state, SimpleNamespace(context=ctx), writer=events.append), 3)
    assert result.get("error") is None, result
    assert result["final_response"] == REPLY
    assert result["pa_background_pending"] is True
    assert not result.get("pa_tools_used")
    assert len(foreground.calls) == 1
    assert set(foreground.calls[0][1]) == {"system", "messages"}
    assert not router.requests
    if stream:
        assert "".join(event["text"] for event in events if event["type"] == "delta") == REPLY
    routing = await route_next_module_node({**state, **result}, SimpleNamespace(context=ctx), writer=lambda _: None)
    assert routing["routing_pending"] is False


async def test_worker_appends_real_card_and_binds_durable_assistant(goal_api, context):
    from app.pa_background import schedule_pa_background
    _, db, _ = goal_api
    await setup_turn(db, "请再展示计划")
    await persist_reply(db)
    router = ToolRouter()
    ctx = worker_context(context, db, router)
    stored = await db.get(ConversationMessage, 22)
    await ctx.store.adopt("chat-a", [Message(role="assistant", content=stored.content,
        created_at=stored.created_at)], "module_2")
    await db.rollback()
    task = schedule_pa_background(worker_state(), ctx, assistant_message_id=22)
    assert task is not None
    await finish_task(task)
    assert router.thinking == [False]
    assert 2 <= len(router.requests) <= 3
    await db.rollback()
    _, actual = await runtime_for(db, "chat-a")
    marker = actual["memory"]["dialogue_draft"]
    assert marker["assistant_message_id"] == 22
    assert marker["summary_verified"] is True
    reply = await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22))
    assert reply.startswith(REPLY + "\n\n")
    assert "晚饭后" in reply and "十分钟" in reply
    live = await ctx.store.get("chat-a")
    assert live.messages[-1].content == reply
    assert live.memory["dialogue_draft"]["assistant_message_id"] == 22
    assert len((await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == 1))).all()) == 4
    telemetry = (await db.execute(select(AIExecutionEvent).where(
        AIExecutionEvent.stage == "pa_background_tools"))).scalars().all()
    assert telemetry and telemetry[-1].assistant_message_id == 22


async def test_present_commit_already_binds_display_before_next_user_confirms(goal_api):
    """A fast confirmation never observes a ready card without its anchor."""
    _, db, _ = goal_api
    await setup_turn(db, "请再展示计划")
    await persist_reply(db)
    tool = background_executor(db)
    snapshot = await tool.execute(tool_call("get_pa_card"))
    command = tool_call("present_pa_card", {"state_version": snapshot["state_version"]})
    result = await tool.execute(command)
    assert result["status"] == "ready_to_display", result

    # No worker publication/finalization has run: execute's own commit must
    # make the card text and the consent marker visible together.
    await db.rollback()
    reply = await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22))
    assert reply == REPLY + "\n\n" + result["display_text"]
    _, durable = await runtime_for(db, "chat-a")
    marker = durable["memory"]["dialogue_draft"]
    assert marker["assistant_message_id"] == 22 and marker["summary_verified"]
    assert "pa_tool_display" not in durable["memory"]
    await db.rollback()
    assert (await tool.execute(command))["replayed"]
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == reply

    await db.execute(insert(ConversationMessage), {"id": 23, "conversation_id": 1,
        "position": 4, "role": "user", "content": "确认，就按这个计划试试。"})
    await persist_reply(db, message_id=24, position=5)
    confirmation = PACardTools(maker=tool.maker, session_id="chat-a", user_id="a",
        user_message_id=23, assistant_message_id=24, module="module_2")
    snapshot = await confirmation.execute(tool_call("get_pa_card"))
    confirmed = await confirmation.execute(tool_call("confirm_pa_card", {
        "state_version": snapshot["state_version"]}))
    assert confirmed["status"] == "confirmed", confirmed
    await db.rollback()
    assert await db.scalar(select(schema.tables["module_two_record"].c.confirmation_message_id)) == 23
    _, durable = await runtime_for(db, "chat-a")
    assert durable["current_module"] == "module_3"


async def test_secondary_display_commit_binds_form_before_immediate_confirmation(goal_api, context, monkeypatch):
    from app.goal_card_workspace import read_card_row
    from test_goal_card_interaction import open_primary, submit_form, review, invoke, message
    client, db, _ = goal_api
    settings = context.settings.model_copy(update={"goal_card_ui_enabled": True,
        "pa_card_tools_enabled": True, "database_schema_version": "v2"})
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    await setup_turn(db, "核心计划已经确认。", module="module_3")
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_2"))
    await db.commit()
    _, _, card = await open_primary(db, "保留原核心目标，额外加一项骑车。", kind="secondary")
    user, _, card = await submit_form(client, db, card, {"activity_content": "骑车"})
    assistant = await message(db, REPLY, "assistant")
    tool = PACardTools(maker=async_sessionmaker(db.bind, expire_on_commit=False),
        session_id="chat-a", user_id="a", user_message_id=user.id,
        assistant_message_id=assistant.id, module="module_2", ui_enabled=True)
    checked = await review(tool, card, near_term=None, manageable=None)
    assert checked["ready"], checked
    result = await invoke(tool, "present_secondary_goal_card", card_id=card["id"],
        card_revision=checked["goal_card"]["revision"])
    assert result["status"] == "ready_to_display", result
    await db.rollback()
    durable_card = await read_card_row(db, 1, "a")
    assert durable_card["phase"] == "ready"
    assert durable_card["display_assistant_message_id"] == assistant.id
    text = await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == assistant.id))
    assert text == REPLY + "\n\n" + result["display_text"]

    user = await message(db, "确认，就按这个计划试试。")
    assistant = await message(db, REPLY, "assistant")
    confirmation = PACardTools(maker=tool.maker, session_id="chat-a", user_id="a",
        user_message_id=user.id, assistant_message_id=assistant.id, module="module_2", ui_enabled=True)
    confirmed = await invoke(confirmation, "confirm_secondary_goal_card", card_id=card["id"],
        card_revision=result["goal_card"]["revision"])
    assert confirmed["status"] == "secondary_confirmed", confirmed
    assert confirmed["core_unchanged"]


async def test_ui_feature_alone_opens_form_from_background_provider(goal_api, context):
    from app.pa_background import schedule_pa_background
    from app.goal_card_workspace import read_card_row
    from app.providers.base import as_text
    _, db, _ = goal_api
    text = "我愿意试试散步，开始定个目标吧。"
    await setup_turn(db, text)
    await persist_reply(db)
    router = ToolRouter(action="open_goal_card", arguments={"kind": "primary",
        "source_message_id": 21, "source_quote": text})
    ctx = worker_context(context, db, router)
    ctx.settings = ctx.settings.model_copy(update={"pa_card_tools_enabled": False, "goal_card_ui_enabled": True})
    task = schedule_pa_background(worker_state(text), ctx, assistant_message_id=22)
    assert task is not None
    await finish_task(task)
    assert router.thinking == [False]
    assert "effective M2" in as_text(router.requests[0]["system"])
    assert "网页目标卡交互" in as_text(router.requests[0]["system"])
    await db.rollback()
    card = await read_card_row(db, 1, "a")
    assert card is not None and card["kind"] == "primary" and card["phase"] == "formulating"
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == REPLY
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_2"


async def test_background_failure_keeps_persisted_reply(goal_api, context):
    from app.pa_background import schedule_pa_background
    _, db, _ = goal_api
    await setup_turn(db, "请再展示计划")
    await persist_reply(db)
    router = ToolRouter(fail=True)
    task = schedule_pa_background(worker_state(), worker_context(context, db, router), assistant_message_id=22)
    assert task is not None
    await finish_task(task)
    await db.rollback()
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == REPLY
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_2"
    telemetry = (await db.execute(select(AIExecutionEvent).where(
        AIExecutionEvent.stage == "pa_background_tools"))).scalars().all()
    assert telemetry and telemetry[-1].error_code


async def test_pending_worker_does_not_enter_routing_wait_or_hold_turn_lock(goal_api, context):
    from app.graph.nodes import _routing_tasks, wait_for_pending_routing
    from app.pa_background import _pa_tasks, schedule_pa_background
    _, db, _ = goal_api
    await setup_turn(db, "请再展示计划")
    await persist_reply(db)
    router = ToolRouter(hold=True, action="continue_pa_conversation")
    ctx = worker_context(context, db, router)
    task = schedule_pa_background(worker_state(), ctx, assistant_message_id=22)
    assert task is not None
    try:
        await asyncio.wait_for(router.entered.wait(), 3)
        assert _pa_tasks.get("chat-a") is task
        assert "chat-a" not in _routing_tasks
        assert await asyncio.wait_for(wait_for_pending_routing("chat-a"), .2)
        lock = await ctx.store.get_turn_lock("chat-a")
        await asyncio.wait_for(lock.acquire(), .2)
        lock.release()
        await db.execute(insert(ConversationMessage), {"id": 23, "conversation_id": 1,
            "position": 4, "role": "user", "content": "还想再聊聊"})
        await db.commit()
        next_state = {**worker_state("还想再聊聊"), "user_message_id": 23}
        reply = await asyncio.wait_for(make_module_node("module_2", ModuleConfig(retrieve=False))(
            next_state, SimpleNamespace(context=ctx), writer=lambda _: None), 3)
        assert reply.get("error") is None
        assert reply["final_response"]
        assert not task.done()
    finally:
        router.release.set()
        await finish_task(task)


async def test_delayed_worker_rejects_new_user_and_preserves_old_reply(goal_api, context):
    from app.pa_background import schedule_pa_background
    _, db, _ = goal_api
    await setup_turn(db, "确认")
    await persist_reply(db)
    router = ToolRouter(hold=True, action="confirm_pa_card")
    task = schedule_pa_background(worker_state("确认"), worker_context(context, db, router), assistant_message_id=22)
    assert task is not None
    try:
        await asyncio.wait_for(router.entered.wait(), 3)
        await db.execute(insert(ConversationMessage), {"id": 23, "conversation_id": 1,
            "position": 4, "role": "user", "content": "等等，我还想修改"})
        await db.commit()
    finally:
        router.release.set()
        await finish_task(task)
    await db.rollback()
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_2"
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == REPLY


async def test_delayed_worker_cannot_recreate_deleted_conversation(goal_api, context):
    from app.pa_background import schedule_pa_background
    _, db, _ = goal_api
    await setup_turn(db, "确认")
    await persist_reply(db)
    router = ToolRouter(hold=True, action="confirm_pa_card")
    task = schedule_pa_background(worker_state("确认"), worker_context(context, db, router), assistant_message_id=22)
    assert task is not None
    try:
        await asyncio.wait_for(router.entered.wait(), 3)
        conversation = await db.get(Conversation, 1)
        await db.delete(conversation)
        await db.commit()
    finally:
        router.release.set()
        await finish_task(task)
    await db.rollback()
    assert await db.scalar(select(Conversation.id).where(Conversation.session_id == "chat-a")) is None
    assert await db.scalar(select(ConversationMessage.id).where(ConversationMessage.id == 22)) is None
    assert await db.scalar(select(schema.tables["module_two_record"].c.record_status)) != "confirmed"


async def test_shutdown_finishes_pending_worker_cancellation_before_database_disposal(goal_api, context):
    from app.pa_background import _pa_tasks, schedule_pa_background, shutdown_pa_background
    _, db, _ = goal_api
    await setup_turn(db, "请再展示计划")
    await persist_reply(db)
    router = ToolRouter(hold=True)
    task = schedule_pa_background(worker_state(), worker_context(context, db, router), assistant_message_id=22)
    assert task is not None
    try:
        await asyncio.wait_for(router.entered.wait(), 3)
        await asyncio.wait_for(shutdown_pa_background(), 3)
        assert task.cancelled()
        assert "chat-a" not in _pa_tasks
        assert not router.release.is_set()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    await db.rollback()
    assert await db.scalar(select(ConversationMessage.content).where(ConversationMessage.id == 22)) == REPLY
    event = (await db.execute(select(AIExecutionEvent).where(
        AIExecutionEvent.stage == "pa_background_tools"))).scalars().one()
    assert event.event_metadata["pa_background_tools"]["status"] == "cancelled"
