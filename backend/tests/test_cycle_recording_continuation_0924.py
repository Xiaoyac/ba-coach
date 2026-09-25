"""Continue a reviewed plan without inventing a recording agreement."""
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.m3_contract import VERSION, normalize
from app.models import ConversationMessage
from app.program_confirmation import confirmation_readiness, draft
from app.turn_confirmation import apply_pre_reply_decision
from app.v2_workflow import persist_record, runtime_for
from test_cycle_program_0914 import confirmation, seed_review
from test_goal_overview import goal_api
from test_m3_program_0924 import REQUIREMENT, PLAN, FEEDBACK, LIMITATIONS, confirm_current
from test_turn_confirmation_m1_m4_0924 import review_turn


@pytest.mark.parametrize("invalid", ["missing", "draft", "wrong_goal", "wrong_plan",
                                     "missing_source", "assistant_source", "foreign_source"])
async def test_unusable_recording_is_not_inherited_as_complete(goal_api, invalid):
    client, db, _ = goal_api
    await seed_review(db, decision=1, with_contract=invalid != "missing")
    records = schema.tables["module_three_record"]
    change = {"draft": {"record_status": "draft"}, "wrong_goal": {"goal_id": "g2"},
              "missing_source": {"confirmation_message_id": 999},
              "assistant_source": {"confirmation_message_id": 20},
              "foreign_source": {"confirmation_message_id": 30}}.get(invalid)
    if invalid == "wrong_plan":
        await db.execute(insert(schema.tables["module_two_record"]), {
            "id": "other-plan", "goal_id": "g1", "version_no": 2, "timezone": "Asia/Shanghai"})
        change = {"module_two_record_id": "other-plan"}
    if invalid == "foreign_source":
        await db.execute(insert(ConversationMessage), {
            "id": 30, "conversation_id": 3, "position": 0, "role": "user", "content": "我确认记录安排。"})
    if change:
        await db.execute(update(records).where(records.c.id == "cycle-contract").values(**change))
    await db.commit()
    before = [dict(row) for row in (await db.execute(select(records))).mappings()]
    await db.commit()

    response = await client.post("/api/program/chat-a/confirm", json=await confirmation(client))
    assert response.status_code == 200, response.text
    state = response.json()["runtime"]
    assert state["current_module"] == "module_3" and state["flow_status"] == "active"
    cycles, progress = schema.tables["pa_cycles"], schema.tables["pa_cycle_progress"]
    successor = (await db.execute(select(cycles).where(cycles.c.id == state["active_cycle_id"]))).mappings().one()
    copied = (await db.execute(select(progress).where(progress.c.cycle_id == successor["id"]))).mappings().one()
    assert successor["status"] == "planning" and successor["module_two_record_id"] == "cycle-plan"
    assert successor["module_three_record_id"] is None and successor["started_at"] is None
    assert copied["module_3_steps"] == [] and copied["module_4_steps"] == []
    assert [dict(row) for row in (await db.execute(select(records))).mappings()] == before


async def test_new_cycle_can_record_new_decision_but_cannot_replay_old_draft(goal_api):
    _, db, _ = goal_api
    state, context = await review_turn(db, decision=1, with_contract=False)
    # An old-cycle draft has real sources but was never committed. It must
    # remain historical even though the next cycle uses the same M2 plan.
    old_messages = [
        ConversationMessage(id=2, conversation_id=1, position=-3, role="assistant",
            content=REQUIREMENT + PLAN + FEEDBACK + LIMITATIONS),
        ConversationMessage(id=3, conversation_id=1, position=-2, role="user", content="好，就每天记一次。"),
    ]
    db.add_all(old_messages)
    await db.flush()
    old_evidence = {
        "requirement": {"message_id": 2, "quote": REQUIREMENT},
        "plan": {"message_id": 2, "quote": PLAN},
        "feedback": {"message_id": 2, "quote": FEEDBACK},
        "limitations": {"message_id": 2, "quote": LIMITATIONS},
        "decision": {"message_id": 3, "quote": "好，就每天记一次。", "status": "accepted", "scope": "current_arrangement"},
    }
    old_values = normalize({"recording_status": "accepted", "recording_evidence": old_evidence},
        old_messages, session_id="chat-a", cycle_id="reviewed-cycle", user_message_id=3)
    records = schema.tables["module_three_record"]
    await db.execute(insert(records), {"id": "old-draft", "goal_id": "g1",
        "module_two_record_id": "cycle-plan", "version_no": 1, **old_values})
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 2).values(
        current_module="module_4", active_goal_id="g1", active_cycle_id="reviewed-cycle",
        memory={"conversation_anchor": "另一聊天的事实"}))
    await db.commit()
    original = dict((await db.execute(select(records).where(records.c.id == "old-draft"))).mappings().one())
    await db.commit()

    result = await apply_pre_reply_decision(state, context, type("Decision", (), {"target_module": "module_3"})())
    assert result["current_module"] == "module_3"
    successor_id = result["active_cycle_id"]
    conversation, current = await runtime_for(db, "chat-a")
    assert current["flow_status"] == "active" and successor_id != "reviewed-cycle"
    assert await draft(db, current, "a") is None
    _, other = await runtime_for(db, "new-chat-a")
    assert other["flow_status"] == "completed" and other["active_cycle_id"] == "reviewed-cycle"
    assert other["memory"]["conversation_anchor"] == "另一聊天的事实"
    events = schema.tables["ai_decision_logs"]
    event = (await db.execute(select(events).where(events.c.module_name == "module_4",
        events.c.decision_type == "user_confirmation"))).mappings().one()
    assert event["decision_value"]["next_cycle_id"] == successor_id
    await db.execute(insert(ConversationMessage), {"id": 30, "conversation_id": 1,
        "position": 12, "role": "user", "content": "这次还没决定记录方式。"})
    await db.commit()

    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    replay_id = await persist_record(maker, module="module_3", user_id="a", cycle_id=successor_id,
        data={"_source_session_id": "chat-a", "_source_user_message_id": 30,
              "recording_status": "accepted", "recording_evidence": old_evidence})
    assert replay_id != "old-draft"
    conversation, current = await runtime_for(db, "chat-a")
    ready = await confirmation_readiness(db, conversation=conversation, state=current,
        user_id="a", session_id="chat-a", confirmation_user_message_id=30)
    assert not ready["ready"]
    assert "recording_decision_evidence" in ready["missing_fields"]
    await db.execute(insert(ConversationMessage), [
        {"id": 31, "conversation_id": 1, "position": 13, "role": "assistant",
         "content": REQUIREMENT + PLAN + FEEDBACK + LIMITATIONS},
        {"id": 32, "conversation_id": 1, "position": 14, "role": "user", "content": "好，就每天记一次。"},
    ])
    await db.commit()
    new_evidence = {key: {**value, "message_id": 32 if key == "decision" else 31}
                    for key, value in old_evidence.items()}
    new_id = await persist_record(maker, module="module_3", user_id="a", cycle_id=successor_id,
        data={"_source_session_id": "chat-a", "_source_user_message_id": 32,
              "recording_status": "accepted", "recording_evidence": new_evidence})
    assert new_id == replay_id and await confirm_current(db) == new_id
    _, current = await runtime_for(db, "chat-a")
    assert current["current_module"] == "module_3" and current["flow_status"] == "waiting_execution"
    rows = (await db.execute(select(records).order_by(records.c.version_no))).mappings().all()
    assert dict(rows[0]) == original
    assert rows[1]["record_status"] == "confirmed" and rows[1]["confirmation_message_id"] == 32
    assert rows[1]["recording_evidence"]["cycle_id"] == successor_id
    assert rows[1]["recording_evidence"]["version"] == VERSION
