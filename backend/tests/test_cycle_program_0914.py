"""Program confirmation integration coverage for M4 cycle decisions."""
import pytest
from sqlalchemy import insert, select, update

from app.database_v2_schema import metadata as schema
from app.m4_contract import normalize
from app.models import ConversationMessage
from app.models_business import InteractionStatus
from test_goal_overview import goal_api  # shared isolated authenticated harness


async def seed_review(db, *, decision, with_contract=True):
    """Make g1's current chat ready to confirm a completed review draft."""
    # The shared V2 fixture intentionally does not include unrelated analytics
    # models, but confirmation records the completed-cycle counter.
    await db.run_sync(lambda sync: InteractionStatus.__table__.create(sync.connection(), checkfirst=True))
    await db.execute(insert(ConversationMessage), {
        "id": 1, "conversation_id": 1, "position": -1, "role": "user",
        "content": "我确认这个散步计划和记录安排。"})
    plans = schema.tables["module_two_record"]
    cycles = schema.tables["pa_cycles"]
    await db.execute(insert(plans), {
        "id": "cycle-plan", "goal_id": "g1", "version_no": 1,
        "timezone": "Asia/Shanghai", "record_status": "confirmed",
        "confirmation_status": "confirmed", "confirmation_message_id": 1,
        "activity_content": "晚饭后散步十分钟", "schedule_text": "每天晚饭后",
        "location": "小区", "duration_minutes": 10,
        "frequency_rule": {"schema_version": 1, "text": "每天"},
        "potential_barriers": ["下雨"],
        "barrier_coping_plan": [{"barrier": "下雨", "plan": "室内走"}],
    })
    if with_contract:
        await db.execute(insert(schema.tables["module_three_record"]), {
            "id": "cycle-contract", "goal_id": "g1", "module_two_record_id": "cycle-plan",
            "version_no": 1, "record_status": "confirmed", "confirmation_message_id": 1,
            "reminder_enabled": False,
        })
    await db.execute(insert(cycles), {
        "id": "reviewed-cycle", "goal_id": "g1", "ordinal": 1,
        "status": "waiting_execution", "module_two_record_id": "cycle-plan",
        "module_three_record_id": "cycle-contract" if with_contract else None,
    })
    await db.execute(insert(schema.tables["pa_cycle_progress"]), {
        "cycle_id": "reviewed-cycle", "module_2_steps": ["activity_selected"],
        "module_3_steps": ["recording_agreed"], "module_4_steps": ["old_review"],
    })
    decision_quote = {
        1: "我决定继续按这个计划。",
        2: "我决定更换目标。",
        3: "我决定调整计划。",
        4: "我决定暂停目标。",
    }[decision]
    summary = "总结：晚饭后散步十分钟，完成十分钟，做完后感觉轻松。"
    education = "BA教育：先行动，再观察感受和结果。"
    review_summary = "复盘：本次已完成，下一步按决定执行。"
    messages = [
        ConversationMessage(id=10, conversation_id=1, position=1, role="user", content="我晚饭后散步了十分钟。"),
        ConversationMessage(id=11, conversation_id=1, position=2, role="user", content="我确实完成了十分钟散步。"),
        ConversationMessage(id=12, conversation_id=1, position=3, role="user", content="做完后我感觉轻松了一点。"),
        ConversationMessage(id=13, conversation_id=1, position=4, role="assistant", content=summary),
        ConversationMessage(id=14, conversation_id=1, position=5, role="user", content="这个总结准确。"),
        ConversationMessage(id=15, conversation_id=1, position=6, role="assistant", content=education),
        ConversationMessage(id=16, conversation_id=1, position=7, role="user", content="我理解先行动再观察感受。"),
        ConversationMessage(id=17, conversation_id=1, position=8, role="user", content="这次没有额外困难。"),
        ConversationMessage(id=18, conversation_id=1, position=9, role="user", content=decision_quote),
        ConversationMessage(id=20, conversation_id=1, position=10, role="assistant", content=review_summary),
    ]
    db.add_all(messages)
    review_values = normalize({
        "phase_a": {"event": "晚饭后散步"},
        "phase_b": {"overt": {"activity": "晚饭后散步", "action_taken": True,
                    "completion_status": "complete", "actual_duration_minutes": 10}},
        "phase_c": {"effect": "做完后感觉轻松"},
        "ai_abc_chain_summary": summary,
        "ba_reeducation_content": education,
        "review_decision": decision,
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
            "decision_quote": decision_quote,
            "review_summary_quote": review_summary,
        },
    }, messages, session_id="chat-a", cycle_id="reviewed-cycle", assistant_message_id=20)
    await db.execute(insert(schema.tables["module_four_record"]), {
        "id": "review-draft", "cycle_id": "reviewed-cycle", **review_values,
    })
    if decision in (1, 3):
        await db.execute(insert(schema.tables["pa_review_details"]), {
            "review_id": "review-draft", "action": "continue" if decision == 1 else "adjust",
            "source_message_id": 18, "source_quote": decision_quote})
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(
        current_module="module_4", flow_status="active", active_goal_id="g1",
        active_cycle_id="reviewed-cycle", last_transition_reason="awaiting_record_confirmation",
        memory={"module_extraction_freshness": {"module_4": {
            "assistant_message_id": 20, "cycle_id": "reviewed-cycle"}}},
    ))
    await db.commit()


async def confirmation(client):
    current = (await client.get("/api/program/chat-a")).json()
    return {
        "record_id": "review-draft", "record_hash": current["record_hash"],
        "row_version": current["runtime"]["row_version"],
    }


@pytest.mark.asyncio
async def test_decision_one_closes_attempt_and_defers_goal_choice(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=1)
    payload = await confirmation(client)
    response = await client.post("/api/program/chat-a/confirm", json=payload)
    assert response.status_code == 200, response.text
    state = response.json()["runtime"]
    assert (state["current_module"], state["active_goal_id"], state["active_cycle_id"]) == ("module_2", None, None)
    cycles, plans, reviews = (schema.tables[n] for n in ("pa_cycles", "module_two_record", "module_four_record"))
    assert (await db.execute(select(cycles.c.id))).scalars().all() == ["reviewed-cycle"]
    assert await db.scalar(select(cycles.c.status).where(cycles.c.id == "reviewed-cycle")) == "completed"
    assert await db.scalar(select(reviews.c.confirmation_message_id).where(reviews.c.id == "review-draft")) == 14
    assert (await db.execute(select(plans.c.id))).scalars().all() == ["cycle-plan"]
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409



@pytest.mark.asyncio
async def test_decision_three_returns_to_m2_without_creating_plan_and_cannot_replay(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=3)
    payload = await confirmation(client)
    response = await client.post("/api/program/chat-a/confirm", json=payload)
    assert response.status_code == 200, response.text
    state = response.json()["runtime"]
    assert (state["current_module"], state["active_goal_id"], state["active_cycle_id"]) == ("module_2", None, None)
    cycles, plans, reviews = (schema.tables[n] for n in ("pa_cycles", "module_two_record", "module_four_record"))
    assert (await db.execute(select(cycles.c.id))).scalars().all() == ["reviewed-cycle"]
    assert await db.scalar(select(cycles.c.status).where(cycles.c.id == "reviewed-cycle")) == "completed"
    assert await db.scalar(select(reviews.c.confirmation_message_id).where(reviews.c.id == "review-draft")) == 14
    assert (await db.execute(select(plans.c.id))).scalars().all() == ["cycle-plan"]
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409



@pytest.mark.asyncio
async def test_review_closure_does_not_call_cycle_creation(goal_api, monkeypatch):
    client, db, _ = goal_api
    await seed_review(db, decision=1, with_contract=False)
    async def forbidden(*args, **kwargs):
        raise AssertionError("M4 must not start the next attempt")
    monkeypatch.setattr("app.v2_repository.start_cycle", forbidden)
    response = await client.post("/api/program/chat-a/confirm", json=await confirmation(client))
    assert response.status_code == 200, response.text
    cycles = schema.tables["pa_cycles"]
    assert (await db.execute(select(cycles.c.id))).scalars().all() == ["reviewed-cycle"]
    assert await db.scalar(select(cycles.c.status).where(cycles.c.id == "reviewed-cycle")) == "completed"
