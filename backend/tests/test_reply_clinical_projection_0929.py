"""Background records retain their scope/status without overruling live dialogue."""
import json

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.models import Conversation, ConversationMessage
from app.v2_repository import append_memory, append_plan_draft, create_goal, start_cycle
from app.v2_workflow import clinical_context
from test_v2_repository import db


async def setup_goal(db):
    goal = await create_goal(db, user_id="a", title="旧目标健身操", conversation_id=1)
    cycle = await start_cycle(db, user_id="a", goal_id=goal, conversation_id=1)
    await db.execute(insert(schema.tables["conversation_runtime_states"]), {
        "conversation_id": 1, "current_module": "module_2", "active_goal_id": goal,
        "active_cycle_id": cycle, "memory": {},
    })
    return goal, cycle


async def project(db, user_id="a", session_id="chat-a"):
    return await clinical_context(async_sessionmaker(db.bind, expire_on_commit=False), user_id, session_id)


def block(lines, prefix):
    return json.loads(next(line for line in lines if line.startswith(prefix)).split("：", 1)[1])


async def bound_plan(db, goal, cycle, *, confirmed=True):
    plan = await append_plan_draft(db, user_id="a", goal_id=goal, fields={
        "activity_content": "原有舒展安排", "schedule_text": "中午", "duration_minutes": 5,
    })
    plans = schema.tables["module_two_record"]
    if confirmed:
        await db.execute(update(plans).where(plans.c.id == plan).values(
            record_status="confirmed", confirmation_status="confirmed", confirmation_message_id=1))
        goals = schema.tables["pa_goals"]
        await db.execute(update(goals).where(goals.c.id == goal).values(
            status="active", current_plan_record_id=plan))
    cycles = schema.tables["pa_cycles"]
    await db.execute(update(cycles).where(cycles.c.id == cycle).values(module_two_record_id=plan))
    return plan


@pytest.mark.asyncio
async def test_old_title_is_a_stored_identifier_not_current_selection_or_confirmation(db):
    goal, cycle = await setup_goal(db)
    await db.execute(insert(ConversationMessage), {
        "id": 4, "conversation_id": 1, "position": 2, "role": "user",
        "content": "时间到10分钟吧，动作停留久一点",
    })
    await db.commit()
    lines = await project(db)
    text = "\n".join(lines)
    title = block(lines, "后台绑定目标记录")
    assert title["source"] == "pa_goals" and title["goal_id"] == goal
    assert title["stored_title"] == "旧目标健身操" and title["status"] == "draft"
    assert title["created_from_conversation_id"] == 1 and title["updated_at"]
    assert "本段聊天明确选择的目标" not in text
    assert "不代表本轮讨论内容或计划已确认" in text
    assert "用户最新提出的修改仍是讨论中的意愿" in text
    assert block(lines, "后台计划绑定状态") == {
        "source": "conversation_runtime_states/pa_cycles", "goal_id": goal,
        "cycle_id": cycle, "bound_plan_available": False,
    }
    # No derived 10-minute plan/confirmation is invented by this read path.
    assert not any(line.startswith("本周期绑定计划") for line in lines)
    assert "duration_minutes" not in block(lines, "后台计划绑定状态")


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [True, False])
async def test_bound_plan_keeps_its_version_and_confirmation_separate_from_new_draft(db, confirmed):
    goal, cycle = await setup_goal(db)
    original = await bound_plan(db, goal, cycle, confirmed=confirmed)
    newer = await append_plan_draft(db, user_id="a", goal_id=goal, fields={
        "activity_content": "新讨论草稿不可假定属于本周期", "schedule_text": "下午", "duration_minutes": 10,
    })
    await db.commit()
    lines = await project(db)
    plan = block(lines, "本周期绑定计划")
    assert plan["record_id"] == original and plan["record_id"] != newer
    assert plan["binding_source"] == "pa_cycles.module_two_record_id"
    assert plan["cycle_id"] == cycle and plan["version_no"] == 1
    assert plan["record_status"] == ("confirmed" if confirmed else "draft")
    assert plan["confirmation_status"] == ("confirmed" if confirmed else "unconfirmed")
    assert plan["confirmation_message_id"] == (1 if confirmed else None)
    assert plan["values"]["duration_minutes"] == 5 and plan["updated_at"]
    assert "新讨论草稿不可假定属于本周期" not in "\n".join(lines)
    assert "draft 是未确认草稿" in "\n".join(lines)
    assert "不自动代表正在讨论的修改" in "\n".join(lines)


@pytest.mark.asyncio
async def test_other_conversation_and_cycle_draft_is_not_recalled(db):
    goal, cycle = await setup_goal(db)
    original = await bound_plan(db, goal, cycle)
    await db.execute(insert(Conversation), {
        "id": 3, "subject_id": "a", "session_id": "chat-a-other", "title": "另一个聊天",
    })
    other_plan = await append_plan_draft(db, user_id="a", goal_id=goal, fields={
        "activity_content": "另一个会话与周期的草稿", "schedule_text": "晚上", "duration_minutes": 30,
    })
    await db.execute(insert(schema.tables["pa_cycles"]), {
        "id": "other-cycle", "goal_id": goal, "ordinal": 2,
        "module_two_record_id": other_plan, "started_from_conversation_id": 3,
    })
    await db.execute(insert(schema.tables["conversation_runtime_states"]), {
        "conversation_id": 3, "current_module": "module_2", "active_goal_id": goal,
        "active_cycle_id": "other-cycle", "memory": {},
    })
    await db.commit()
    own = await project(db)
    other = await project(db, session_id="chat-a-other")
    assert block(own, "本周期绑定计划")["record_id"] == original
    assert "另一个会话与周期的草稿" not in "\n".join(own)
    assert block(other, "本周期绑定计划")["record_id"] == other_plan
    assert block(other, "本周期绑定计划")["record_status"] == "draft"
    # Supplying a foreign account's session cannot borrow its goal binding.
    assert not any(line.startswith("后台绑定目标记录") for line in await project(db, user_id="b"))


@pytest.mark.asyncio
async def test_projection_is_read_only_and_preserves_user_memories_and_messages(db):
    goal, cycle = await setup_goal(db)
    await bound_plan(db, goal, cycle)
    await append_memory(db, user_id="a", memory_type="限制", content="用户说膝盖受过伤",
                        source_kind="user_statement", source_message_id=1)
    await db.commit()
    tracked = [schema.tables[name] for name in (
        "pa_goals", "pa_cycles", "module_two_record", "conversation_runtime_states", "ba_memory")]
    tracked.append(ConversationMessage.__table__)
    before = [list((await db.execute(select(table))).mappings()) for table in tracked]
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().upper())

    event.listen(db.bind.sync_engine, "before_cursor_execute", observe)
    try:
        lines = await project(db)
    finally:
        event.remove(db.bind.sync_engine, "before_cursor_execute", observe)
    after = [list((await db.execute(select(table))).mappings()) for table in tracked]
    assert before == after
    assert statements and all(statement.startswith("SELECT") for statement in statements)
    assert "用户说膝盖受过伤" in "\n".join(lines)
