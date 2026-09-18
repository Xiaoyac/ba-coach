"""Latest-turn extraction is required before an old V2 draft can be confirmed."""
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.m4_contract import normalize
from app.models import ConversationMessage
from app.v2_workflow import persist_record, record_steps
from app.workflow_contract import MODULE_STEP_KEYS
from test_goal_overview import goal_api


async def seed_m2(db):
    plans, cycles, progress, runtime = (schema.tables[name] for name in (
        "module_two_record", "pa_cycles", "pa_cycle_progress", "conversation_runtime_states"))
    await db.execute(insert(plans), {"id": "m2-draft", "goal_id": "g1", "version_no": 1,
        "timezone": "Asia/Shanghai", "activity_content": "晚饭后散步十分钟",
        "schedule_text": "每天晚饭后", "location": "小区", "duration_minutes": 10,
        "frequency_rule": {"schema_version": 1, "text": "每天"},
        "potential_barriers": ["下雨"],
        "barrier_coping_plan": [{"barrier": "下雨", "plan": "室内走"}]})
    await db.execute(insert(cycles), {"id": "m2-cycle", "goal_id": "g1", "ordinal": 1,
        "status": "planning"})
    await db.execute(insert(progress), {"cycle_id": "m2-cycle",
        "module_2_steps": list(MODULE_STEP_KEYS["module_2"]),
        "module_3_steps": [], "module_4_steps": []})
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(
        current_module="module_2", active_goal_id="g1", active_cycle_id="m2-cycle",
        last_transition_reason="awaiting_record_confirmation",
        memory={"module_extraction_freshness": {"module_2": {
            "assistant_message_id": 20, "cycle_id": "m2-cycle"}}}))
    await db.execute(insert(ConversationMessage), {"id": 20, "conversation_id": 1,
        "position": 1, "role": "assistant", "content": "旧草稿"})
    await db.commit()


async def seed_m4(db):
    plans, cycles, progress, reviews, details, runtime = (schema.tables[name] for name in (
        "module_two_record", "pa_cycles", "pa_cycle_progress", "module_four_record",
        "pa_review_details", "conversation_runtime_states"))
    await db.execute(insert(plans), {"id": "m4-plan", "goal_id": "g1", "version_no": 1,
        "timezone": "Asia/Shanghai", "record_status": "confirmed", "confirmation_status": "confirmed",
        "confirmation_message_id": 1, "activity_content": "晚饭后散步十分钟", "schedule_text": "每天晚饭后"})
    await db.execute(insert(cycles), {"id": "m4-cycle", "goal_id": "g1", "ordinal": 1,
        "status": "waiting_execution", "module_two_record_id": "m4-plan"})
    await db.execute(insert(progress), {"cycle_id": "m4-cycle", "module_2_steps": [],
        "module_3_steps": [], "module_4_steps": list(MODULE_STEP_KEYS["module_4"])})
    summary = "总结：晚饭后散步十分钟，完成十分钟，做完后感觉轻松。"
    education = "BA教育：先行动，再观察感受和结果。"
    review_summary = "复盘：本次已完成，继续按计划执行。"
    messages = [
        ConversationMessage(id=10, conversation_id=1, position=1, role="user", content="我晚饭后散步了十分钟。"),
        ConversationMessage(id=11, conversation_id=1, position=2, role="user", content="我确实完成了十分钟散步。"),
        ConversationMessage(id=12, conversation_id=1, position=3, role="user", content="做完后我感觉轻松了一点。"),
        ConversationMessage(id=13, conversation_id=1, position=4, role="assistant", content=summary),
        ConversationMessage(id=14, conversation_id=1, position=5, role="user", content="这个总结准确。"),
        ConversationMessage(id=15, conversation_id=1, position=6, role="assistant", content=education),
        ConversationMessage(id=16, conversation_id=1, position=7, role="user", content="我理解先行动再观察感受。"),
        ConversationMessage(id=17, conversation_id=1, position=8, role="user", content="这次没有额外困难。"),
        ConversationMessage(id=18, conversation_id=1, position=9, role="user", content="我决定继续按计划。"),
        ConversationMessage(id=20, conversation_id=1, position=10, role="assistant", content=review_summary),
    ]
    db.add_all(messages)
    values = normalize({
        "phase_a": {"event": "晚饭后散步"},
        "phase_b": {"overt": {"activity": "晚饭后散步", "action_taken": True,
                    "completion_status": "complete", "actual_duration_minutes": 10}},
        "phase_c": {"effect": "做完后感觉轻松"},
        "ai_abc_chain_summary": summary,
        "ba_reeducation_content": education,
        "review_decision": 1,
        "review_summary": review_summary,
        "m4_contract": {
            "phase_a_quote": "我晚饭后散步了十分钟。",
            "phase_b_quote": "我确实完成了十分钟散步。",
            "phase_c_quote": "做完后我感觉轻松了一点。",
            "emotion_improved": True,
            "emotion_quote": "做完后我感觉轻松了一点。",
            "summary_quote": summary,
            "chain_status": "confirmed",
            "confirmation_quote": "这个总结准确。",
            "education_quote": education,
            "understanding_quote": "我理解先行动再观察感受。",
            "core_questions_resolved": True,
            "difficulty_status": "none",
            "difficulty_quote": "这次没有额外困难。",
            "decision_quote": "我决定继续按计划。",
            "review_summary_quote": review_summary,
        },
    }, messages, session_id="chat-a", cycle_id="m4-cycle", assistant_message_id=20)
    await db.execute(insert(reviews), {"id": "m4-draft", "cycle_id": "m4-cycle", **values})
    await db.execute(insert(details), {"review_id": "m4-draft", "action": "continue",
        "source_message_id": 18, "source_quote": "我决定继续按计划。"})
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(
        current_module="module_4", active_goal_id="g1", active_cycle_id="m4-cycle",
        last_transition_reason="awaiting_record_confirmation",
        memory={"module_extraction_freshness": {"module_4": {
            "assistant_message_id": 20, "cycle_id": "m4-cycle"}}}))
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("module,seed,record_id", [
    ("module_2", seed_m2, "m2-draft"),
    ("module_4", seed_m4, "m4-draft"),
])
async def test_failed_latest_extraction_cannot_reuse_old_draft_readiness(goal_api, module, seed, record_id):
    client, db, _ = goal_api
    await seed(db)
    before = (await client.get("/api/program/chat-a")).json()
    assert before["can_confirm"] is True
    payload = {"record_id": record_id, "record_hash": before["record_hash"],
               "row_version": before["runtime"]["row_version"]}
    # The person corrects the draft.  Extraction for the visible reply (22)
    # then fails, so the only durable source marker remains the old reply (20).
    await db.execute(insert(ConversationMessage), [
        {"id": 21, "conversation_id": 1, "position": 11, "role": "user", "content": "我更正前面的安排"},
        {"id": 22, "conversation_id": 1, "position": 12, "role": "assistant", "content": "我会重新核对"},
    ])
    await db.commit()
    # Direct confirmation has the same latest-turn gate even before the
    # delayed router gets a chance to clear its accumulated step list.
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409
    await record_steps(db, session_id="chat-a", user_id="a", module=module,
        requested_target=module, steps=list(MODULE_STEP_KEYS[module]), assistant_message_id=22)
    await db.commit()

    state = (await db.execute(select(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == 1))).mappings().one()
    progress = (await db.execute(select(schema.tables["pa_cycle_progress"]).where(
        schema.tables["pa_cycle_progress"].c.cycle_id == state["active_cycle_id"]))).mappings().one()
    assert state["last_transition_reason"] == "discussion_required"
    assert progress[f"{module}_steps"] == []
    assert (await client.get("/api/program/chat-a")).json()["can_confirm"] is False
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409


@pytest.mark.asyncio
async def test_m2_side_channel_only_extraction_does_not_refresh_an_old_plan(goal_api):
    _, db, _ = goal_api
    await seed_m2(db)
    await db.execute(insert(ConversationMessage), [
        {"id": 21, "conversation_id": 1, "position": 2, "role": "user", "content": "今天没有执行"},
        {"id": 22, "conversation_id": 1, "position": 3, "role": "assistant", "content": "我已记录"},
    ])
    await db.commit()

    await persist_record(async_sessionmaker(db.bind, expire_on_commit=False), module="module_2",
        user_id="a", cycle_id="m2-cycle", data={"activity_observations": [],
            "_source_session_id": "chat-a", "_source_assistant_message_id": 22})

    state = (await db.execute(select(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == 1))).mappings().one()
    assert state["memory"]["module_extraction_freshness"]["module_2"]["assistant_message_id"] == 20
