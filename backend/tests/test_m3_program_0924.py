import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.program_confirmation import commit_confirmation, confirmation_readiness, draft, record_hash
from app.v2_workflow import persist_record, record_values, runtime_for
from test_goal_overview import goal_api


REQUIREMENT = "每天记录整体感受，活动记录可以补充。"
PLAN = "可以每天在记录今日填写一次。"
FEEDBACK = "没完成、受阻或忘记了也可以在聊天里告诉我。"
LIMITATIONS = "少了活动记录，复盘时能参考的具体信息会少一些。"


async def seed(db, status):
    await db.execute(insert(ConversationMessage), [
        {"id":1, "conversation_id":1, "position":0, "role":"user", "content":"我确认散步计划。"},
        {"id":10, "conversation_id":1, "position":1, "role":"assistant", "content":REQUIREMENT+PLAN+FEEDBACK+LIMITATIONS},
        {"id":11, "conversation_id":1, "position":2, "role":"user",
         "content":"好，就每天记一次。" if status == "accepted" else "活动记录我不想填，但每天整体感受会继续记。"},
    ])
    await db.execute(insert(schema.tables["module_two_record"]), {
        "id":"plan", "goal_id":"g1", "version_no":1, "timezone":"Asia/Shanghai",
        "activity_content":"散步", "schedule_text":"今晚", "record_status":"confirmed",
        "confirmation_status":"confirmed", "confirmation_message_id":1})
    await db.execute(insert(schema.tables["pa_cycles"]), {
        "id":"cycle", "goal_id":"g1", "ordinal":1, "status":"planning", "module_two_record_id":"plan"})
    await db.execute(insert(schema.tables["pa_cycle_progress"]), {
        "cycle_id":"cycle", "module_2_steps":[], "module_3_steps":[], "module_4_steps":[]})
    await db.execute(update(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == 1).values(
        current_module="module_3", active_goal_id="g1", active_cycle_id="cycle", memory={}))
    await db.execute(insert(schema.tables["ai_decision_logs"]), {
        "conversation_id":1, "turn_id":"1", "cycle_id":"cycle", "module_name":"module_2",
        "decision_type":"user_confirmation", "decision_value":{}, "evidence_message_ids":[1]})
    await db.commit()
    return {"_source_session_id":"chat-a", "_source_user_message_id":11,
        "recording_status":status, "user_acceptance_level":2,
        "recording_evidence":{
            "requirement":{"message_id":10, "quote":REQUIREMENT},
            "plan":{"message_id":10, "quote":PLAN},
            "feedback":{"message_id":10, "quote":FEEDBACK},
            "limitations":{"message_id":10, "quote":LIMITATIONS},
            "decision":{"message_id":11, "status":status,
                "scope":"current_arrangement" if status == "accepted" else "activity_record",
                "quote":"好，就每天记一次。" if status == "accepted" else "活动记录我不想填，但每天整体感受会继续记。"},
        }}


@pytest.mark.parametrize("status", ["accepted", "declined"])
async def test_recording_decision_commits_before_reply_without_second_card(goal_api, status):
    _, db, _ = goal_api
    data = await seed(db, status)
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    record_id = await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data=data)
    conversation, state = await runtime_for(db, "chat-a")
    pending = await draft(db, state, "a")
    ready = await confirmation_readiness(db, conversation=conversation, state=state,
        user_id="a", session_id="chat-a", pending=pending, confirmation_user_message_id=11)
    assert ready["ready"] and ready["recording_decision_message_id"] == 11
    assert "dialogue_draft" not in state["memory"]
    message = await db.get(ConversationMessage, 11)
    result = await commit_confirmation(db, conversation=conversation, state=state, user_id="a",
        pending=pending, message=message, review_action=None, snapshot_hash=record_hash(pending))
    await db.commit()
    assert result == ("module_3", "cycle")
    _, state = await runtime_for(db, "chat-a")
    assert state["current_module"] == "module_3" and state["flow_status"] == "waiting_execution"
    row = (await db.execute(select(schema.tables["module_three_record"]).where(
        schema.tables["module_three_record"].c.id == record_id))).mappings().one()
    assert row["recording_status"] == status and row["record_status"] == "confirmed"
    assert row["acceptance_status"] == "2"  # attitude was not overwritten as consent
    assert (row["negotiated_record_plan"] is None) == (status == "declined")


@pytest.mark.parametrize("invalid", ["other_user", "wrong_role", "no_source"])
async def test_unsourced_or_wrong_owner_cannot_persist_recording_authority(goal_api, invalid):
    _, db, _ = goal_api
    data = await seed(db, "accepted")
    user = "b" if invalid == "other_user" else "a"
    if invalid == "wrong_role":
        data["_source_assistant_message_id"] = data.pop("_source_user_message_id")
    if invalid == "no_source":
        data.pop("_source_session_id")
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    assert await persist_record(maker, module="module_3", user_id=user, cycle_id="cycle", data=data) is None


async def test_newer_user_correction_makes_prior_extraction_stale(goal_api):
    _, db, _ = goal_api
    data = await seed(db, "accepted")
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data=data)
    await db.execute(insert(ConversationMessage), {"id":12, "conversation_id":1, "position":3,
        "role":"user", "content":"等等，我想改成另一种记录方式。"})
    await db.commit()
    conversation, state = await runtime_for(db, "chat-a")
    ready = await confirmation_readiness(db, conversation=conversation, state=state,
        user_id="a", session_id="chat-a", confirmation_user_message_id=12)
    assert not ready["ready"] and not ready["extraction_fresh"]


def test_raw_authority_fields_are_not_writable_without_normalization():
    values = record_values("module_3", {"recording_status":"accepted", "recording_evidence":{"verified":True}})
    assert values == {}


async def confirm_current(db):
    conversation, state = await runtime_for(db, "chat-a")
    pending = await draft(db, state, "a")
    ready = await confirmation_readiness(db, conversation=conversation, state=state,
        user_id="a", session_id="chat-a", pending=pending)
    assert ready["ready"]
    await commit_confirmation(db, conversation=conversation, state=state, user_id="a",
        pending=pending, message=await db.get(ConversationMessage, ready["recording_decision_message_id"]),
        review_action=None, snapshot_hash=record_hash(pending))
    await db.commit()
    return pending["id"]


@pytest.mark.parametrize("change", ["declined", "new_plan"])
async def test_waiting_user_can_change_recording_with_old_version_preserved(goal_api, change):
    _, db, _ = goal_api
    data = await seed(db, "accepted")
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data=data)
    old_id = await confirm_current(db)
    quote = "活动记录我不想填，但每天整体感受会继续记。" if change == "declined" else "我决定改成睡前填写每天的整体感受。"
    await db.execute(insert(ConversationMessage), {"id":12, "conversation_id":1, "position":3, "role":"user", "content":quote})
    await db.commit()
    status = "declined" if change == "declined" else "accepted"
    evidence = {**data["recording_evidence"], "decision":{"message_id":12, "quote":quote, "status":status,
        "scope":"activity_record" if change == "declined" else "current_arrangement"}}
    if change == "new_plan":
        evidence["plan"] = {"message_id":12, "quote":quote}
    new_id = await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data={**data,
        "_source_user_message_id":12, "recording_status":status, "recording_evidence":evidence})
    assert new_id and new_id != old_id
    assert await confirm_current(db) == new_id
    records = schema.tables["module_three_record"]
    rows = (await db.execute(select(records).order_by(records.c.version_no))).mappings().all()
    assert len(rows) == 2 and rows[0]["record_status"] == rows[1]["record_status"] == "confirmed"
    assert rows[0]["recording_status"] == "accepted" and rows[0]["negotiated_record_plan"]["text"] == PLAN
    assert rows[1]["recording_status"] == status
    assert rows[1]["negotiated_record_plan"] is None if change == "declined" else rows[1]["negotiated_record_plan"]["text"] == quote
    cycle = (await db.execute(select(schema.tables["pa_cycles"]).where(schema.tables["pa_cycles"].c.id == "cycle"))).mappings().one()
    _, state = await runtime_for(db, "chat-a")
    assert cycle["module_three_record_id"] == new_id and cycle["status"] == "waiting_execution"
    assert state["current_module"] == "module_3" and state["flow_status"] == "waiting_execution"


async def test_waiting_old_decision_or_identical_arrangement_cannot_create_duplicate_version(goal_api):
    _, db, _ = goal_api
    data = await seed(db, "accepted")
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data=data)
    await confirm_current(db)
    await db.execute(insert(ConversationMessage), {"id":12, "conversation_id":1, "position":3,
        "role":"user", "content":"我还是按原来的方式记录。"})
    await db.commit()
    replay = {**data, "_source_user_message_id":12}
    assert await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data=replay) is None
    replay["recording_evidence"] = {**data["recording_evidence"], "decision":{
        "message_id":12, "quote":"我还是按原来的方式记录。", "status":"accepted", "scope":"current_arrangement"}}
    assert await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data=replay) is None
    rows = (await db.execute(select(schema.tables["module_three_record"]))).mappings().all()
    assert len(rows) == 1 and rows[0]["record_status"] == "confirmed"


async def test_confirmation_keeps_activity_context_and_other_chat_facts(goal_api):
    _, db, _ = goal_api
    data = await seed(db, "accepted")
    stable = {"m2_activity_context":{"trial":{"activity":"拉伸", "source_message_id":1}},
        "conversation_anchor":"已谈过的事实", "turn_count":"7", "last_user_message":"当前原话"}
    transient = {"dialogue_draft":{"stale":True}, "pa_card":"旧计划", "last_module":"module_2"}
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(memory={**stable, **transient}))
    await db.execute(update(runtime).where(runtime.c.conversation_id == 2).values(
        active_goal_id="g1", active_cycle_id="cycle", current_module="module_3",
        memory={"conversation_anchor":"另一个聊天的独立事实", "pa_card":"旧卡"}))
    await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    await persist_record(maker, module="module_3", user_id="a", cycle_id="cycle", data=data)
    await confirm_current(db)
    _, state = await runtime_for(db, "chat-a")
    assert state["memory"] == stable
    _, other = await runtime_for(db, "new-chat-a")
    assert other["memory"] == {"conversation_anchor":"另一个聊天的独立事实"}
