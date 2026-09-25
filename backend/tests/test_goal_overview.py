"""Goal workspace regression tests: isolated SQLite, no real accounts or LLM."""
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database_v2_schema import metadata as schema
from app.db import get_db
from app.identity import require_subject_id
from app.models import Conversation, ConversationMessage
from app.routes import program
from app.session import InMemorySessionStore, get_session_store
from app.v2_workflow import create_goal_from_agent_dialogue


@pytest_asyncio.fixture
async def goal_api(monkeypatch):
    monkeypatch.setattr(program, "enabled", lambda: True)
    engine = create_async_engine("sqlite+aiosqlite://")
    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    async with engine.begin() as connection:
        await connection.run_sync(schema.create_all)
        for model in (Conversation, ConversationMessage):
            await connection.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        await db.execute(insert(schema.tables["user_profile"]), [{"uuid": "a"}, {"uuid": "b"}])
        await db.execute(insert(Conversation), [
            {"id": 1, "session_id": "chat-a", "subject_id": "a"},
            {"id": 2, "session_id": "new-chat-a", "subject_id": "a"},
            {"id": 3, "session_id": "chat-b", "subject_id": "b"}])
        await db.execute(insert(schema.tables["conversation_runtime_states"]), [
            {"conversation_id": n, "memory": {}} for n in (1, 2, 3)])
        await db.execute(insert(schema.tables["user_module_one_state"]), {
            "user_id": "a", "completed_steps": [], "status": "completed",
            "completion_source": "legacy_imported", "evidence_status": "missing"})
        await db.execute(insert(schema.tables["pa_goals"]), [
            {"id": "g1", "user_id": "a", "title": "晚饭后散步", "status": "active"},
            {"id": "g2", "user_id": "a", "title": "晨间练习", "status": "draft"},
            {"id": "private", "user_id": "b", "title": "他人的目标", "status": "active"}])
        await db.commit()
        app = FastAPI()
        app.include_router(program.router, prefix="/api")
        async def test_db():
            yield db
        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[require_subject_id] = lambda: "a"
        store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
        app.dependency_overrides[get_session_store] = lambda: store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
            yield client, db, app
    await engine.dispose()


@pytest.mark.asyncio
async def test_overview_owned_scoped_and_independent_of_chat(goal_api):
    client, db, _ = goal_api
    goals, plans, cycles = (schema.tables[name] for name in ("pa_goals", "module_two_record", "pa_cycles"))
    await db.execute(insert(plans), {"id": "p1", "goal_id": "g1", "version_no": 1,
        "timezone": "Asia/Shanghai", "record_status": "confirmed", "confirmation_status": "confirmed",
        "confirmation_message_id": 1, "activity_content": "走十分钟", "schedule_text": "晚饭后", "duration_minutes": 10})
    await db.execute(update(goals).where(goals.c.id == "g1").values(current_plan_record_id="p1"))
    await db.execute(insert(cycles), [
        {"id": "c1", "goal_id": "g1", "ordinal": 1, "status": "completed", "module_two_record_id": "p1"},
        {"id": "c2", "goal_id": "g1", "ordinal": 2, "status": "planning", "module_two_record_id": None}])
    response = await client.get("/api/program/goals/overview")
    assert response.status_code == 200
    result = response.json()
    assert result["enabled"] and result["m1_reusable"]
    rows = {g["id"]: g for g in result["goals"]}
    assert set(rows) == {"g1", "g2"}
    assert rows["g1"]["latest_cycle"] == {"ordinal": 2, "status": "planning"}
    assert rows["g1"]["plan"]["activity_content"] == "走十分钟"
    assert rows["g2"]["plan"] is None
    assert rows["g2"]["latest_cycle"] is None
    assert "user_id" not in rows["g1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_owner,confirmed", [("g2", True), ("g1", False)])
async def test_overview_never_exposes_foreign_or_unconfirmed_plan(goal_api, plan_owner, confirmed):
    client, db, _ = goal_api
    await db.execute(insert(schema.tables["module_two_record"]), {"id": "p", "goal_id": plan_owner,
        "version_no": 1, "timezone": "Asia/Shanghai", "record_status": "confirmed" if confirmed else "draft",
        "confirmation_status": "confirmed" if confirmed else "unconfirmed", "confirmation_message_id": 1,
        "activity_content": "must not leak", "schedule_text": "later"})
    table = schema.tables["pa_goals"]
    await db.execute(update(table).where(table.c.id == "g1").values(current_plan_record_id="p"))
    rows = (await client.get("/api/program/goals/overview")).json()["goals"]
    assert next(g for g in rows if g["id"] == "g1")["plan"] is None


@pytest.mark.asyncio
async def test_authentication_is_required(goal_api):
    client, _, app = goal_api
    del app.dependency_overrides[require_subject_id]
    assert (await client.get("/api/program/goals/overview")).status_code == 401


@pytest.mark.asyncio
async def test_legacy_explicitly_disabled_without_database_queries(goal_api, monkeypatch):
    client, _, _ = goal_api
    monkeypatch.setattr(program, "enabled", lambda: False)
    assert (await client.get("/api/program/goals/overview")).json() == {
        "enabled": False, "m1_reusable": False, "goals": []}


@pytest.mark.asyncio
async def test_multiple_goals_select_in_separate_chats_without_overwriting(goal_api):
    client, _, _ = goal_api
    one = await client.post("/api/program/chat-a/goal", json={"goal_id": "g1", "row_version": 0})
    assert one.status_code == 200, one.text
    assert one.json()["runtime"]["active_goal_id"] == "g1"
    blocked = await client.post("/api/program/chat-a/goal", json={"goal_id": "g2", "row_version": 1})
    assert blocked.status_code == 409
    two = await client.post("/api/program/new-chat-a/goal", json={"goal_id": "g2", "row_version": 0})
    assert two.status_code == 200, two.text
    assert two.json()["runtime"]["active_goal_id"] == "g2"
    assert (await client.get("/api/program/chat-a")).json()["runtime"]["active_goal_id"] == "g1"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,code", [("foreign_goal", 409), ("foreign_chat", 404),
    ("stale", 409), ("m1", 409), ("sandbox", 409), ("paused", 409), ("completed", 409)])
async def test_selection_guards(goal_api, scenario, code):
    client, db, _ = goal_api
    runtime, goals = schema.tables["conversation_runtime_states"], schema.tables["pa_goals"]
    body = {"goal_id": "private" if scenario == "foreign_goal" else "g1", "row_version": 20 if scenario == "stale" else 0}
    if scenario == "m1":
        state = schema.tables["user_module_one_state"]
        await db.execute(update(state).where(state.c.user_id == "a").values(status="in_progress"))
    if scenario == "sandbox":
        await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(memory={"sandbox_mode": "true"}))
    if scenario in ("paused", "completed"):
        await db.execute(update(goals).where(goals.c.id == "g1").values(status=scenario))
    response = await client.post(f"/api/program/{'chat-b' if scenario == 'foreign_chat' else 'chat-a'}/goal", json=body)
    assert response.status_code == code, response.text
    assert (await db.execute(select(runtime.c.active_goal_id).where(runtime.c.conversation_id == 1))).scalar_one() is None


@pytest.mark.asyncio
async def test_web_cannot_create_goal_by_title(goal_api):
    client, db, _ = goal_api
    assert (await client.post("/api/program/chat-a/goal", json={"title": "   ", "row_version": 0})).status_code == 422
    assert (await client.post("/api/program/chat-a/goal", json={
        "goal_id": "g1", "title": "绕过 Agent", "row_version": 0})).status_code == 422
    titles = (await db.execute(select(schema.tables["pa_goals"].c.title))).scalars().all()
    assert "绕过 Agent" not in titles


@pytest.mark.asyncio
async def test_agent_creates_goal_only_when_router_and_extractor_agree(goal_api):
    _, db, _ = goal_api
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 2).values(current_module="module_2"))
    await db.execute(insert(ConversationMessage), [
        {"id": 100, "conversation_id": 2, "position": 0, "role": "user", "content": "我选晚饭后散步"},
        {"id": 101, "conversation_id": 2, "position": 1, "role": "assistant", "content": "好，我们把它具体化。"},
    ])
    await db.commit()

    diagnostics = {}
    assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a",
        data={"target_activity_content": "晚饭后散步"}, completed_steps=[], assistant_message_id=101,
        diagnostics=diagnostics) is None
    assert diagnostics["goal_creation"]["reason_code"] == "selection_extraction_missing"
    diagnostics = {}
    assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a",
        data={}, completed_steps=["activity_selected"], assistant_message_id=101,
        diagnostics=diagnostics) is None
    assert diagnostics["goal_creation"]["reason_code"] == "activity_missing"

    created = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a",
        data={"target_activity_content": "晚饭后散步", "schedule_text": "每天晚饭后",
              "goal_proposal": {"selection_status": "selected", "selection_role": "core", "goal_kind": "secondary", "selection_quote": "我选晚饭后散步", "activity_quote": "散步"}},
        completed_steps=["activity_selected", "values_or_intention_explored"], assistant_message_id=101)
    assert created is not None
    await db.commit()
    state = (await db.execute(select(runtime).where(runtime.c.conversation_id == 2))).mappings().one()
    assert state["active_goal_id"] == created["goal_id"]
    assert state["active_cycle_id"] == created["cycle_id"]
    assert state["last_transition_reason"] == "agent_created_goal_from_dialogue"
    assert state["memory"]["module_extraction_freshness"]["module_2"] == {
        "assistant_message_id": 101, "cycle_id": created["cycle_id"]}
    plan = (await db.execute(select(schema.tables["module_two_record"]).where(
        schema.tables["module_two_record"].c.id == created["plan_id"]))).mappings().one()
    assert plan["activity_content"] == "晚饭后散步"
    audit = (await db.execute(select(schema.tables["ai_decision_logs"]).where(
        schema.tables["ai_decision_logs"].c.decision_type == "agent_goal_created"))).mappings().one()
    assert audit["evidence_message_ids"] == [100]

    assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a",
        data={"target_activity_content": "另一个目标"}, completed_steps=["activity_selected"],
        assistant_message_id=101) is None
    owned = (await db.execute(select(schema.tables["pa_goals"].c.id).where(
        schema.tables["pa_goals"].c.user_id == "a"))).scalars().all()
    assert len(owned) == 3


@pytest.mark.asyncio
async def test_agent_goal_creation_uses_verified_proposal_when_router_omits_step(goal_api):
    """A missing Router step must not discard a clearly selected chat goal."""
    _, db, _ = goal_api
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 2).values(current_module="module_2"))
    await db.execute(insert(ConversationMessage), [
        {"id": 110, "conversation_id": 2, "position": 0, "role": "user",
         "content": "我选择晚饭后散步十分钟作为一个独立的小目标。"},
        {"id": 111, "conversation_id": 2, "position": 1, "role": "assistant",
         "content": "好，我记下你选择的晚饭后散步十分钟。"},
    ])
    created = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a",
        data={"target_activity_content": "晚饭后散步十分钟", "schedule_text": "每天晚饭后",
              "goal_proposal": {"selection_status": "selected", "selection_role": "core", "goal_kind": "secondary",
                  "selection_quote": "我选择晚饭后散步十分钟作为一个独立的小目标。",
                  "activity_quote": "散步十分钟"}},
        completed_steps=[], assistant_message_id=111)
    assert created is not None
    await db.commit()
    audit = (await db.execute(select(schema.tables["ai_decision_logs"]).where(
        schema.tables["ai_decision_logs"].c.decision_type == "agent_goal_created")
        .order_by(schema.tables["ai_decision_logs"].c.id.desc()))).mappings().first()
    assert audit["decision_value"]["creation_rule"] == "extractor_evidence_fallback_router_step_missing"
