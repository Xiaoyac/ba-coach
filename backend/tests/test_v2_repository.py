import pytest
import pytest_asyncio
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database_v2_schema import metadata
from app.models import Conversation, ConversationMessage
from app.v2_repository import (V2Conflict, V2NotFound, active_memories, append_memory,
    append_plan_draft, can_reuse_m1, confirm_plan, create_goal, initial_module,
    owned_goal, start_cycle, transition_cycle)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.run_sync(Conversation.__table__.create)
        await connection.run_sync(ConversationMessage.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await session.execute(insert(metadata.tables["user_profile"]), [{"uuid": "a"}, {"uuid": "b"}])
        await session.execute(insert(Conversation.__table__), [
            {"id": 1, "subject_id": "a", "session_id": "chat-a", "title": "a"},
            {"id": 2, "subject_id": "b", "session_id": "chat-b", "title": "b"}])
        await session.execute(insert(ConversationMessage.__table__), [
            {"id": 1, "conversation_id": 1, "position": 0, "role": "user", "content": "这个安排难度是4分，我确认。"},
            {"id": 2, "conversation_id": 2, "position": 0, "role": "user", "content": "这个安排难度是4分，我确认。"},
            {"id": 3, "conversation_id": 1, "position": 1, "role": "assistant", "content": "已确认"}])
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_goal_owner_and_multiple_goals(db):
    first = await create_goal(db, user_id="a", title="散步")
    second = await create_goal(db, user_id="a", title="阅读")
    assert first != second
    assert (await owned_goal(db, "a", first))["title"] == "散步"
    with pytest.raises(V2NotFound):
        await owned_goal(db, "b", first)


@pytest.mark.asyncio
async def test_imported_m1_completion_reuses_progress_without_fabricating_evidence(db):
    states = metadata.tables["user_module_one_state"]
    await db.execute(insert(states), {"user_id": "a", "completed_steps": [],
        "status": "completed", "completion_source": "legacy_imported", "evidence_status": "missing"})
    assert await initial_module(db, user_id="a") == "module_2"
    assert await initial_module(db, user_id="b") == "module_1"
    state = (await db.execute(select(states).where(states.c.user_id == "a"))).mappings().one()
    assert state["confirmed_formulation_id"] is None and state["completed_steps"] == []
    await db.execute(update(states).where(states.c.user_id == "a").values(status="revisit_needed"))
    assert await initial_module(db, user_id="a") == "module_2"


@pytest.mark.parametrize("source,evidence,formulation,status,expected", [
    ("none", "missing", None, "in_progress", False),
    ("none", "missing", None, "completed", False),
    ("legacy_imported", "missing", None, "completed", True),
    ("legacy_imported", "missing", None, "in_progress", False),
    ("legacy_imported", "available", "fake", "completed", False),
    ("user_confirmed", "available", "actual-version", "completed", True),
    ("user_confirmed", "missing", None, "completed", False),
])
def test_m1_completion_provenance_rules(source, evidence, formulation, status, expected):
    assert can_reuse_m1({"status": status, "completion_source": source,
        "evidence_status": evidence, "confirmed_formulation_id": formulation}) is expected


@pytest.mark.asyncio
async def test_planning_cycle_exists_before_card_and_cannot_duplicate(db):
    goal = await create_goal(db, user_id="a", title="散步")
    cycle = await start_cycle(db, user_id="a", goal_id=goal)
    progress = metadata.tables["pa_cycle_progress"]
    assert (await db.execute(select(progress.c.module_2_steps).where(progress.c.cycle_id == cycle))).scalar_one() == []
    with pytest.raises(V2Conflict):
        await start_cycle(db, user_id="a", goal_id=goal)


@pytest.mark.asyncio
async def test_versions_are_append_only_and_confirmation_is_owned(db):
    goal = await create_goal(db, user_id="a", title="散步")
    cycle = await start_cycle(db, user_id="a", goal_id=goal)
    fields = {"activity_content": "散步十分钟", "schedule_text": "晚饭后", "location": "小区", "duration_minutes": 10,
              "frequency_rule": {"schema_version": 1, "text": "每天"}, "potential_barriers": ["下雨"],
              "barrier_coping_plan": [{"barrier": "下雨", "plan": "在室内走"}],
              "difficulty_rating": 4, "difficulty_evidence": {"rating": {"value": 4, "message_id": 1, "quote": "这个安排难度是4分，我确认。", "score_text": "4"}}}
    plan = await append_plan_draft(db, user_id="a", goal_id=goal, fields=fields)
    for message in (2, 3):
        with pytest.raises(V2Conflict):
            await confirm_plan(db, user_id="a", goal_id=goal, cycle_id=cycle, plan_id=plan, message_id=message)
    await confirm_plan(db, user_id="a", goal_id=goal, cycle_id=cycle, plan_id=plan, message_id=1)
    newer = await append_plan_draft(db, user_id="a", goal_id=goal, fields={**fields, "activity_content": "散步五分钟"})
    plans = metadata.tables["module_two_record"]
    original = (await db.execute(select(plans).where(plans.c.id == plan))).mappings().one()
    assert original["activity_content"] == "散步十分钟"
    assert original["record_status"] == "confirmed"
    assert (await db.execute(select(plans.c.version_no).where(plans.c.id == newer))).scalar_one() == 2


@pytest.mark.asyncio
async def test_plan_cannot_be_bound_to_other_goal_cycle(db):
    g1 = await create_goal(db, user_id="a", title="一")
    g2 = await create_goal(db, user_id="a", title="二")
    cycle = await start_cycle(db, user_id="a", goal_id=g1)
    plan = await append_plan_draft(db, user_id="a", goal_id=g2, fields={"activity_content": "阅读", "schedule_text": "今晚"})
    with pytest.raises(V2NotFound):
        await confirm_plan(db, user_id="a", goal_id=g1, cycle_id=cycle, plan_id=plan, message_id=1)


@pytest.mark.asyncio
async def test_waiting_requires_confirmed_contract(db):
    goal = await create_goal(db, user_id="a", title="散步")
    cycle = await start_cycle(db, user_id="a", goal_id=goal)
    plan = await append_plan_draft(db, user_id="a", goal_id=goal, fields={"activity_content": "散步", "schedule_text": "晚上",
        "location": "小区", "duration_minutes": 10, "frequency_rule": {"schema_version": 1, "text": "每天"},
        "potential_barriers": ["下雨"], "barrier_coping_plan": [{"barrier": "下雨", "plan": "室内走"}],
        "difficulty_rating": 4, "difficulty_evidence": {"rating": {"value": 4, "message_id": 1, "quote": "这个安排难度是4分，我确认。", "score_text": "4"}}})
    await confirm_plan(db, user_id="a", goal_id=goal, cycle_id=cycle, plan_id=plan, message_id=1)
    with pytest.raises(V2Conflict):
        await transition_cycle(db, user_id="a", goal_id=goal, cycle_id=cycle, target="waiting_execution")


@pytest.mark.asyncio
async def test_memory_same_type_coexists_and_replacement_is_versioned(db):
    one = await append_memory(db, user_id="a", memory_type="资源", content="姐姐能陪我", source_kind="ai_inference")
    two = await append_memory(db, user_id="a", memory_type="资源", content="朋友能陪我", source_kind="ai_inference")
    duplicate = await append_memory(db, user_id="a", memory_type="资源", content="朋友能陪我", source_kind="ai_inference")
    assert duplicate == two
    assert len(await active_memories(db, user_id="a")) == 2
    newer = await append_memory(db, user_id="a", memory_type="资源", content="姐姐只有周末能陪我",
        source_kind="user_confirmation", source_message_id=1, supersedes_id=one)
    result = await active_memories(db, user_id="a")
    assert {r["id"] for r in result} == {two, newer}
    memories = metadata.tables["ba_memory"]
    old = (await db.execute(select(memories).where(memories.c.id == one))).mappings().one()
    current = next(r for r in result if r["id"] == newer)
    assert old["status"] == "superseded" and old["memory_key"] == current["memory_key"]
    assert await active_memories(db, user_id="b") == []
