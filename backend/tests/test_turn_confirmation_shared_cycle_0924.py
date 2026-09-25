"""A cycle entering review must publish the same phase to each bound chat."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import insert, select, update

from app.database_v2_schema import metadata as schema
from app.models import Conversation, ConversationMessage
from app.pre_reply_routing import route_before_reply
from app.providers.base import Completion
from app.turn_confirmation import apply_pre_reply_decision
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api
from test_turn_confirmation_0924 import setup_turn


async def test_shared_cycle_review_reaches_other_chat_and_preserves_its_memory(goal_api, monkeypatch):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "刚才散步完成了。", module="module_3")
    rt = schema.tables["conversation_runtime_states"]
    other_memory = {
        "conversation_anchor": "B聊天自己的早期事实", "m2_activity_context": {"trial": "听音乐"},
        "turn_count": "9", "last_user_message": "B聊天的上一句",
        "dialogue_draft": {"old": True}, "module_extraction_freshness": {"module_3": {}},
        "pa_card": "旧卡片", "current_transition_evidence": {"old": True}, "current_step": "old",
    }
    await db.execute(update(rt).where(rt.c.conversation_id == 2).values(
        current_module="module_3", flow_status="waiting_execution", active_goal_id="g1",
        active_cycle_id="m2-cycle", row_version=6, memory=other_memory))
    # A different owner's runtime must not follow this user's transition.
    await db.execute(update(rt).where(rt.c.conversation_id == 3).values(
        current_module="module_3", row_version=4, memory={"private": "unchanged"}))
    before = (await db.execute(select(Conversation.revision).where(Conversation.id == 2))).scalar_one()
    await db.commit()

    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_4"))
    assert result["current_module"] == "module_4"
    _, other = await runtime_for(db, "new-chat-a")
    assert other["current_module"] == "module_4" and other["flow_status"] == "active"
    assert other["active_cycle_id"] == "m2-cycle" and other["row_version"] == 7
    assert other["memory"] == {key: other_memory[key] for key in (
        "conversation_anchor", "m2_activity_context", "turn_count", "last_user_message")}
    revision = (await db.execute(select(Conversation.revision).where(Conversation.id == 2))).scalar_one()
    assert revision == before + 1
    _, foreign = await runtime_for(db, "chat-b")
    assert foreign["current_module"] == "module_3" and foreign["row_version"] == 4
    assert foreign["memory"] == {"private": "unchanged"}

    # Simulate B's stale in-memory module from before A's commit. Its next
    # graph route must read durable M4 and must not try the M3 entry gate.
    await db.execute(insert(ConversationMessage), {"id": 22, "conversation_id": 2,
        "position": 0, "role": "user", "content": "继续聊这次执行。"})
    await db.commit()
    context.settings.database_schema_version = "v2"
    context.settings.router_reasoning_max_tokens = 1000
    context.router_prompt = None
    context.router_provider.name = "isolated-router"
    context.router_provider.route_detailed = AsyncMock(return_value=Completion(
        text='{"target_module":"4","knowledge_task":"general"}', model="test"))
    from app.router_agent import RouterDecision
    async def propose(provider, **kwargs):
        assert kwargs["current_module"] == "module_4"
        return RouterDecision(target_module="module_4", reasoning_content="", model="test",
                              completed_steps=[], usage={})
    monkeypatch.setattr("app.pre_reply_routing.decide_target_module_with_reasoning", propose)
    extraction = AsyncMock(return_value=False)
    monkeypatch.setattr("app.turn_confirmation._refresh_extraction", extraction)
    selected = await route_before_reply({"subject_id": "a", "session_id": "new-chat-a",
        "current_module": "module_3", "user_input": "继续聊这次执行。", "user_message_id": 22,
        "memory": {}}, context)
    assert selected["extracted_intent"] == selected["next_module"] == "module_4"
    extraction.assert_awaited_once()
    assert extraction.call_args.args[0]["current_module"] == "module_4"


async def test_m3_plan_revision_retains_known_context_on_the_new_version(goal_api):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "我还没执行，想先改这个计划。", module="module_3")
    details = schema.tables["pa_plan_details"]
    known = {"schedule_kind": "recurring", "review_cadence": "一周后一起回顾",
             "difficulty": "晚上容易觉得累", "resources": ["朋友可以陪着散步"]}
    await db.execute(insert(details), {"plan_id": "m2-draft", **known})
    await db.commit()
    previous = dict((await db.execute(select(details).where(
        details.c.plan_id == "m2-draft"))).mappings().one())
    await db.commit()

    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    plans = schema.tables["module_two_record"]
    latest = (await db.execute(select(plans).where(plans.c.goal_id == "g1").order_by(
        plans.c.version_no.desc()).limit(1))).mappings().one()
    retained = (await db.execute(select(details).where(details.c.plan_id == latest["id"]))).mappings().one()
    assert result["current_module"] == "module_2" and latest["id"] != "m2-draft"
    assert latest["record_status"] == "draft" and latest["version_no"] == 2
    assert {key: retained[key] for key in known} == known
    original = dict((await db.execute(select(details).where(details.c.plan_id == "m2-draft"))).mappings().one())
    assert original == previous
