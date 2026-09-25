import pytest
import pytest_asyncio
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database_v2_schema import metadata
from app.models import Conversation, ConversationMessage
from app.v2_repository import V2Conflict, V2NotFound, continue_reviewed_cycle, create_goal


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
            {"id": 2, "subject_id": "b", "session_id": "chat-b", "title": "b"},
        ])
        await session.execute(insert(ConversationMessage.__table__), [
            {"id": 1, "conversation_id": 1, "position": 0, "role": "user", "content": "确认"},
            {"id": 2, "conversation_id": 2, "position": 0, "role": "user", "content": "确认"},
        ])
        await session.commit()
        yield session
    await engine.dispose()


async def seed_cycle(db, *, user_id="a", goal_status="active", cycle_status="completed",
                     review_decision=1, plan_goal=None, contract_goal=None,
                     confirmed_plan=True, confirmed_contract=True, ordinal=1):
    goal = await create_goal(db, user_id=user_id, title="散步")
    await db.execute(metadata.tables["pa_goals"].update().where(
        metadata.tables["pa_goals"].c.id == goal).values(status=goal_status))
    if plan_goal == "other" or contract_goal == "other":
        other_goal = await create_goal(db, user_id=user_id, title="另一个目标")
    plan_goal = other_goal if plan_goal == "other" else (plan_goal or goal)
    contract_goal = other_goal if contract_goal == "other" else (contract_goal or goal)
    plan = "plan-1"
    contract = "contract-1"
    await db.execute(insert(metadata.tables["module_two_record"]), {
        "id": plan, "goal_id": plan_goal, "version_no": 1,
        "record_status": "confirmed" if confirmed_plan else "draft",
        "activity_content": "散步十分钟", "schedule_text": "今晚", "timezone": "Asia/Shanghai",
        "location": "小区", "duration_minutes": 10,
        "frequency_rule": {"schema_version": 1, "text": "每天"},
        "potential_barriers": ["下雨"], "barrier_coping_plan": [{"barrier": "下雨", "plan": "室内走"}],
        "confirmation_status": "confirmed" if confirmed_plan else "unconfirmed",
        "confirmation_message_id": 1 if confirmed_plan else None})
    await db.execute(insert(metadata.tables["module_three_record"]), {
        "id": contract, "goal_id": contract_goal, "module_two_record_id": plan,
        "version_no": 1, "record_status": "confirmed" if confirmed_contract else "draft",
        "reminder_enabled": False, "confirmation_message_id": 1 if confirmed_contract else None})
    cycle = "cycle-1"
    await db.execute(insert(metadata.tables["pa_cycles"]), {
        "id": cycle, "goal_id": goal, "ordinal": ordinal, "status": cycle_status,
        "module_two_record_id": plan, "module_three_record_id": contract})
    await db.execute(insert(metadata.tables["pa_cycle_progress"]), {
        "cycle_id": cycle, "module_2_steps": [{"step": "m2", "done": True}],
        "module_3_steps": [{"step": "m3", "done": False}], "module_4_steps": [{"step": "old"}]})
    await db.execute(insert(metadata.tables["module_four_record"]), {
        "id": "review-1", "cycle_id": cycle, "record_status": "confirmed",
        "execution_result": 1, "review_decision": review_decision, "confirmation_message_id": 1})
    await db.commit()
    return goal, cycle, plan, contract


@pytest.mark.asyncio
async def test_continue_copies_confirmed_plan_contract_and_progress(db):
    goal, cycle, plan, contract = await seed_cycle(db)
    new_cycle = await continue_reviewed_cycle(db, user_id="a", goal_id=goal, cycle_id=cycle,
                                              conversation_id=1)
    cycles = metadata.tables["pa_cycles"]
    progress = metadata.tables["pa_cycle_progress"]
    row = (await db.execute(select(cycles).where(cycles.c.id == new_cycle))).mappings().one()
    copied = (await db.execute(select(progress).where(progress.c.cycle_id == new_cycle))).mappings().one()
    assert row["ordinal"] == 2 and row["status"] == "waiting_execution"
    assert row["module_two_record_id"] == plan and row["module_three_record_id"] == contract
    assert copied["module_2_steps"] == [{"step": "m2", "done": True}]
    assert copied["module_3_steps"] == [{"step": "m3", "done": False}]
    assert copied["module_4_steps"] == []
    assert (await db.execute(select(metadata.tables["module_four_record"]).where(
        metadata.tables["module_four_record"].c.cycle_id == new_cycle))).mappings().one_or_none() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"user_id": "b"}, {"cycle_status": "reviewing"}, {"review_decision": 2},
    {"confirmed_plan": False}, {"plan_goal": "other"},
])
async def test_continue_rejects_unqualified_source(db, kwargs):
    goal, cycle, _, _ = await seed_cycle(db, **kwargs)
    with pytest.raises((V2Conflict, V2NotFound)):
        await continue_reviewed_cycle(db, user_id="a", goal_id=goal, cycle_id=cycle)


@pytest.mark.asyncio
async def test_continue_rejects_duplicate_and_stale_replay(db):
    goal, cycle, _, _ = await seed_cycle(db)
    next_cycle = await continue_reviewed_cycle(db, user_id="a", goal_id=goal, cycle_id=cycle)
    with pytest.raises(V2Conflict):
        await continue_reviewed_cycle(db, user_id="a", goal_id=goal, cycle_id=cycle)
    cycles = metadata.tables["pa_cycles"]
    await db.execute(cycles.update().where(cycles.c.id == next_cycle).values(status="completed"))
    with pytest.raises(V2Conflict):
        await continue_reviewed_cycle(db, user_id="a", goal_id=goal, cycle_id=cycle)
