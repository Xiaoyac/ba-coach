"""Offline end-to-end V2 API smoke test; never uses a configured database."""
import os
os.environ["DATABASE_SCHEMA_VERSION"] = "v2"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["STARTUP_DB_MAINTENANCE"] = "false"
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from sqlalchemy import select, update, insert, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.main import app
from app.db import Base, get_db
from app.database_v2_schema import metadata as schema
from app.models_business import RiskMonitoring, InteractionStatus
from app.models import Conversation, ConversationMessage
from app.routes import auth
from app.v2_workflow import persist_record, record_steps
from app.workflow_contract import MODULE_STEP_KEYS


async def main(engine_url="sqlite+aiosqlite://"):
    from sqlalchemy.engine import make_url
    url = make_url(engine_url)
    if url.get_backend_name() != "sqlite":
        import re
        if not re.fullmatch(r"ba_coach_v2_rehearsal_[0-9]{8}t[0-9]{6}z", url.database or ""):
            raise RuntimeError("Writes are restricted to an isolated rehearsal database")
    engine = create_async_engine(engine_url)
    if url.get_backend_name() == "sqlite":
        async with engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            await conn.run_sync(schema.create_all)
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[t for t in Base.metadata.sorted_tables if t.name not in schema.tables]))
            await conn.run_sync(RiskMonitoring.__table__.create)
            await conn.run_sync(InteractionStatus.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async def dependency():
        async with maker() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    auth.email_delivery_configured = lambda: False
    checks = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def request(method, path, body=None):
            response = await client.request(method, "/api" + path, json=body)
            assert response.status_code < 400, (path, response.status_code, response.text[:500])
            return response.json() if response.content else None
        account = await request("POST", "/auth/register", {"username": "v2testuser", "password": "OfflineTestOnly123",
            "email": "v2test@example.com", "nickname": "测试用户", "tag": "12345", "age": 28,
            "physical_condition": ["易疲劳"], "behavior_taboo": ["不能剧烈运动"]})
        token = account.get("token") or account.get("access_token")
        assert token, account.keys()
        client.headers["Authorization"] = "Bearer " + token
        me = await request("GET", "/auth/me")
        user = me["profile_uuid"]
        profile = await request("PATCH", "/profile", {"supporters": [
            {"relation": "同学", "nickname": "甲"}, {"relation": "导师", "nickname": "乙"},
            {"relation": "邻居", "nickname": "丙"}], "reminder_window": {"start_minute": 600, "end_minute": 630}})
        assert len(profile["supporters"]) == 3 and profile["physical_condition"] == ["易疲劳"]
        checks.append("register_login_normalized_profile_three_supporters")
        first = await request("POST", "/conversations")
        assert first["next_module"] == "module_1"
        first_session = first["session_id"]
        async with maker() as db:
            convo = (await db.execute(select(Conversation.id).where(Conversation.session_id == first_session))).scalar_one()
            evidence = (await db.execute(select(ConversationMessage.id).where(ConversationMessage.conversation_id == convo).limit(1))).scalar_one()
            await record_steps(db, session_id=first_session, user_id=user, module="module_1",
                requested_target="module_2", steps=list(MODULE_STEP_KEYS["module_1"]), assistant_message_id=evidence)
            await db.commit()
        await persist_record(maker, module="module_1", user_id=user,
            data={"chief_complaint": "下班后总是拖延", "ai_depression_cycle_summary": "回避让当下轻松，但之后更担心"}, cycle_id=None)
        one = await request("GET", f"/program/{first_session}")
        stale = await client.post(f"/api/program/{first_session}/confirm", json={
            "record_id": one["draft"]["id"], "record_hash": "0" * 64, "row_version": one["runtime"]["row_version"]})
        assert stale.status_code == 409
        one = await request("POST", f"/program/{first_session}/confirm", {
            "record_id": one["draft"]["id"], "record_hash": one["record_hash"], "row_version": one["runtime"]["row_version"]})
        assert one["runtime"]["current_module"] == "module_2"
        checks.append("new_m1_real_confirmation_and_stale_record_rejection")
        async with maker() as db:
            m1 = schema.tables["user_module_one_state"]
            await db.execute(update(m1).where(m1.c.user_id == user).values(
                status="completed", completion_source="legacy_imported", evidence_status="missing", confirmed_formulation_id=None))
            await db.commit()
        chat = await request("POST", "/conversations")
        assert chat["next_module"] == "module_2"
        session = chat["session_id"]
        status = await request("GET", f"/program/{session}")
        assert status["m1_reusable"] and status["runtime"]["active_goal_id"] is None
        status = await request("POST", f"/program/{session}/goal", {"title": "晚饭后散步", "row_version": status["runtime"]["row_version"]})
        goal, cycle = status["runtime"]["active_goal_id"], status["runtime"]["active_cycle_id"]
        checks.append("legacy_m1_reuse_and_explicit_goal_selection")
        stages = [
            ("module_2", "module_3", {"target_activity_content": "散步", "schedule_text": "晚饭后", "target_activity_duration_minutes": 10,
                "target_activity_location": "小区", "frequency_rule": {"schema_version": 1, "text": "每天"},
                "potential_barriers": ["下雨"], "barrier_coping_plan": [{"barrier": "下雨", "plan": "室内走"}]}),
            ("module_3", "module_4", {"negotiated_record_plan": "走完记下心情和分钟数"}),
            ("module_4", "module_2", {"execution_result": 1, "review_decision": 1, "review_summary": "完成十分钟散步", "phase_b": {"action": "走了十分钟"}}),
        ]
        for module, target, values in stages:
            async with maker() as db:
                convo = (await db.execute(select(Conversation.id).where(Conversation.session_id == session))).scalar_one()
                evidence = (await db.execute(select(ConversationMessage.id).where(ConversationMessage.conversation_id == convo).limit(1))).scalar_one()
                await record_steps(db, session_id=session, user_id=user, module=module,
                    requested_target=target, steps=list(MODULE_STEP_KEYS[module]), assistant_message_id=evidence)
                await db.commit()
            await persist_record(maker, module=module, user_id=user, data=values, cycle_id=cycle)
            status = await request("GET", f"/program/{session}")
            assert status["can_confirm"]
            status = await request("POST", f"/program/{session}/confirm", {
                "record_id": status["draft"]["id"], "record_hash": status["record_hash"],
                "row_version": status["runtime"]["row_version"]})
            assert status["runtime"]["current_module"] == target
            checks.append(module + "_record_confirmation_transition")
        assert status["runtime"]["active_cycle_id"] != cycle
        next_chat = await request("POST", "/conversations")
        next_status = await request("GET", f"/program/{next_chat['session_id']}")
        other = await request("POST", f"/program/{next_chat['session_id']}/goal", {
            "title": "每周阅读", "row_version": next_status["runtime"]["row_version"]})
        assert other["runtime"]["active_goal_id"] != goal
        await request("DELETE", f"/conversations/{session}")
        async with maker() as db:
            goals, cycles = schema.tables["pa_goals"], schema.tables["pa_cycles"]
            assert (await db.execute(select(func.count()).select_from(goals).where(goals.c.user_id == user))).scalar_one() == 2
            assert (await db.execute(select(func.count()).select_from(cycles).where(cycles.c.goal_id == goal))).scalar_one() == 2
        checks.append("multiple_goals_and_delete_chat_preserves_goal_history")
    await engine.dispose()
    print(json.dumps({"passed": checks, "production_accessed": False}))


if __name__ == "__main__":
    asyncio.run(main())
