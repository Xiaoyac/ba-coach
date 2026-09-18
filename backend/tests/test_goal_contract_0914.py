"""Evidence-bound regression coverage for goal classification and activities."""
import pytest
from sqlalchemy import insert, select, update

from app.database_v2_schema import metadata as schema
from app.goal_contract import capture_activities, evidence_messages
from app.models import Conversation, ConversationMessage
from app.v2_workflow import create_goal_from_agent_dialogue, runtime_for
from test_goal_overview import goal_api  # shared isolated authenticated harness


async def seed_goal_dialogue(db, *, conversation_id=2, first_id=100):
    await db.execute(update(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == conversation_id).values(
            current_module="module_2", active_goal_id=None, active_cycle_id=None, flow_status="active", memory={}))
    await db.execute(insert(ConversationMessage), [
        {"id": first_id, "conversation_id": conversation_id, "position": 0, "role": "user",
         "content": "我想让自己更有精力。"},
        {"id": first_id + 1, "conversation_id": conversation_id, "position": 1, "role": "assistant",
         "content": "可以考虑每天晚饭后散步。"},
        {"id": first_id + 2, "conversation_id": conversation_id, "position": 2, "role": "user",
         "content": "我选择每天晚饭后散步，想让自己更有精力；每周日复盘。"},
        {"id": first_id + 3, "conversation_id": conversation_id, "position": 3, "role": "assistant",
         "content": "我会把这项选择整理成计划草稿。"},
    ])
    await db.commit()
    return first_id + 3


def primary_payload():
    return {
        "target_activity_content": "每天晚饭后散步",
        "schedule_text": "每天晚饭后", "target_activity_location": "小区",
        "target_activity_duration_minutes": 10,
        "goal_proposal": {"goal_kind": "primary", "long_term_direction": "让自己更有精力",
            "selection_quote": "我选择每天晚饭后散步", "activity_quote": "每天晚饭后散步",
            "direction_quote": "让自己更有精力"},
        "plan_context": {"schedule_kind": "recurring", "schedule_quote": "每天晚饭后",
            "review_cadence": "每周日复盘", "difficulty": "每天", "resources": ["小区"]},
    }


@pytest.mark.asyncio
async def test_new_primary_goal_requires_router_evidence_and_user_quotes(goal_api):
    _, db, _ = goal_api
    latest_assistant = await seed_goal_dialogue(db)
    payload = primary_payload()
    steps = ["activity_selected", "values_or_intention_explored"]

    for missing_steps, invalid in [(["activity_selected"], payload), (steps, {**payload, "goal_proposal": None}),
                                   (steps, {**payload, "goal_proposal": {**payload["goal_proposal"], "selection_quote": "可以考虑每天晚饭后散步"}}),
                                   (steps, {**payload, "goal_proposal": {**payload["goal_proposal"], "direction_quote": "并不在用户原话里"}})]:
        assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=invalid,
            completed_steps=missing_steps, assistant_message_id=latest_assistant) is None
    assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=steps, assistant_message_id=latest_assistant - 2) is None

    created = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=steps, assistant_message_id=latest_assistant)
    assert created is not None
    await db.commit()
    detail = (await db.execute(select(schema.tables["pa_goal_details"]).where(
        schema.tables["pa_goal_details"].c.goal_id == created["goal_id"]))).mappings().one()
    context = (await db.execute(select(schema.tables["pa_plan_details"]).where(
        schema.tables["pa_plan_details"].c.plan_id == created["plan_id"]))).mappings().one()
    assert (detail["goal_kind"], detail["long_term_direction"], detail["source_message_id"]) == ("primary", "让自己更有精力", 102)
    assert (context["schedule_kind"], context["review_cadence"], context["difficulty"]) == ("recurring", "每周日复盘", "每天")


@pytest.mark.asyncio
async def test_secondary_goal_is_independent_and_assistant_only_or_stale_evidence_is_rejected(goal_api):
    _, db, _ = goal_api
    latest_assistant = await seed_goal_dialogue(db)
    payload = primary_payload()
    payload["goal_proposal"] = {"goal_kind": "secondary", "long_term_direction": "不应保存",
        "selection_quote": "我选择每天晚饭后散步", "activity_quote": "每天晚饭后散步", "direction_quote": None}
    created = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=["activity_selected", "values_or_intention_explored"], assistant_message_id=latest_assistant)
    assert created is not None
    await db.commit()
    detail = (await db.execute(select(schema.tables["pa_goal_details"]).where(
        schema.tables["pa_goal_details"].c.goal_id == created["goal_id"]))).mappings().one()
    assert detail["goal_kind"] == "secondary" and detail["long_term_direction"] is None
    assert (await db.execute(select(schema.tables["pa_goal_details"].c.goal_id).where(
        schema.tables["pa_goal_details"].c.goal_kind == "primary"))).scalars().all() == []


@pytest.mark.asyncio
async def test_capture_activities_uses_only_the_latest_user_message_and_is_idempotent(goal_api):
    _, db, _ = goal_api
    await db.execute(insert(ConversationMessage), [
        {"id": 200, "conversation_id": 1, "position": 0, "role": "user", "content": "昨天游泳了。"},
        {"id": 201, "conversation_id": 1, "position": 1, "role": "assistant", "content": "谢谢你的补充。"},
        {"id": 202, "conversation_id": 1, "position": 2, "role": "user", "content": "今天没站桩，但散步了，感觉轻松。游泳可能不错。"},
        {"id": 203, "conversation_id": 1, "position": 3, "role": "assistant", "content": "我会记录今天实际发生的活动。"},
    ])
    await db.commit()
    conversation = (await db.execute(select(Conversation).where(Conversation.id == 1))).scalar_one()
    _, state = await runtime_for(db, "chat-a")
    messages = await evidence_messages(db, conversation.id, "a")
    raw = [
        {"event_kind": "not_performed", "activity_content": "站桩", "source_quote": "今天没站桩，但散步了，感觉轻松", "occurred_at_text": "今天"},
        {"event_kind": "performed", "activity_content": "散步", "source_quote": "今天没站桩，但散步了，感觉轻松", "effect": "感觉轻松"},
        {"event_kind": "idea", "activity_content": "游泳", "source_quote": "游泳可能不错"},
        {"event_kind": "performed", "activity_content": "游泳", "source_quote": "昨天游泳了"},
        {"event_kind": "performed", "activity_content": "跑步", "source_quote": "不存在的引用"},
    ]
    await capture_activities(db, user_id="a", conversation=conversation, state=state, raw=raw, messages=messages)
    await db.commit()
    activities = schema.tables["pa_activity_events"]
    first = (await db.execute(select(activities).where(activities.c.source_message_id == 202))).mappings().all()
    assert {(row["activity_content"], row["event_kind"], row["goal_id"]) for row in first} == {
        ("站桩", "not_performed", None), ("散步", "performed", None), ("游泳", "idea", None)}
    assert (await db.execute(select(schema.tables["pa_goals"].c.id).where(
        schema.tables["pa_goals"].c.user_id == "a"))).scalars().all() == ["g1", "g2"]
    await capture_activities(db, user_id="a", conversation=conversation, state=state, raw=raw, messages=messages)
    await db.commit()
    assert len((await db.execute(select(activities).where(activities.c.source_message_id == 202))).mappings().all()) == 3


@pytest.mark.asyncio
async def test_overview_has_safe_fallbacks_and_keeps_schedule_and_review_cadence_independent(goal_api):
    client, db, _ = goal_api
    plans, goals = schema.tables["module_two_record"], schema.tables["pa_goals"]
    await db.execute(insert(plans), {"id": "p-context", "goal_id": "g1", "version_no": 1,
        "timezone": "Asia/Shanghai", "record_status": "confirmed", "confirmation_status": "confirmed",
        "confirmation_message_id": 1, "activity_content": "晚饭后散步", "schedule_text": "每周三次"})
    await db.execute(update(goals).where(goals.c.id == "g1").values(current_plan_record_id="p-context"))
    await db.execute(insert(schema.tables["pa_goal_details"]), {"goal_id": "g1", "goal_kind": "primary",
        "long_term_direction": "保持精力", "source_conversation_id": 1, "source_message_id": 1,
        "evidence": {"selection_quote": "散步", "activity_quote": "散步", "direction_quote": "保持精力"}})
    await db.execute(insert(schema.tables["pa_plan_details"]), {"plan_id": "p-context", "schedule_kind": "recurring",
        "review_cadence": "每两周复盘"})
    await db.commit()
    payload = (await client.get("/api/program/goals/overview")).json()
    rows = {goal["id"]: goal for goal in payload["goals"]}
    assert rows["g1"]["goal_kind"] == "primary" and rows["g1"]["long_term_direction"] == "保持精力"
    assert rows["g1"]["plan"]["schedule_kind"] == "recurring"
    assert rows["g1"]["plan"]["review_cadence"] == "每两周复盘"
    assert rows["g2"]["goal_kind"] == "unclassified" and rows["g2"]["long_term_direction"] is None
