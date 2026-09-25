from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import knowledge_context
from app.database_v2_schema import metadata


@pytest_asyncio.fixture
async def scoped_context(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await db.execute(insert(metadata.tables["user_profile"]), {"uuid":"a"})
        await db.execute(insert(metadata.tables["pa_goals"]), {
            "id":"goal", "user_id":"a", "title":"当前目标"})
        await db.execute(insert(metadata.tables["module_two_record"]), {
            "id":"plan", "goal_id":"goal", "version_no":1, "timezone":"Asia/Shanghai",
            "activity_content":"计划散步", "schedule_text":"今晚", "duration_minutes":30,
            "core_values":["不相关价值"], "potential_barriers":["下雨"],
            "pa_understanding_status":"unknown", "pa_willingness_status":"willing"})
        await db.execute(insert(metadata.tables["pa_cycles"]), [
            {"id":"old-cycle", "goal_id":"goal", "ordinal":1, "module_two_record_id":"plan"},
            {"id":"current-cycle", "goal_id":"goal", "ordinal":2, "module_two_record_id":"plan"}])
        await db.execute(insert(metadata.tables["module_four_record"]), [
            {"id":"old-review", "cycle_id":"old-cycle", "phase_b":{"behavior":"旧周期跑步"},
                "phase_c":{"result":"旧周期结果"}, "abc_chain_summary":"旧周期摘要"},
            {"id":"current-review", "cycle_id":"current-cycle", "phase_b":{"behavior":"本次在家休息"},
                "phase_c":{"result":"本次情绪平稳"}, "abc_chain_summary":"未确认的本次摘要"}])
        await db.execute(insert(metadata.tables["module_one_record"]), {
            "id":"m1", "user_id":"a", "version_no":1, "chief_complaint":"无关主诉",
            "coping_behavior":"回避", "coping_consequence":"短暂轻松",
            "functional_chain_summary":"未确认的行为状态关系"})
        await db.commit()

    async def runtime_for(db, session_id):
        return SimpleNamespace(subject_id="a"), {"active_goal_id":"goal", "active_cycle_id":"current-cycle"}

    monkeypatch.setattr(knowledge_context, "runtime_for", runtime_for)
    yield maker
    await engine.dispose()


async def test_general_question_does_not_open_database():
    def forbidden():
        raise AssertionError("General knowledge has no personal data requirement")
    result = await knowledge_context.assemble_knowledge_context(forbidden, "a", "session")
    assert result == {"task":"general", "goal_id":None, "cycle_id":None, "facts":[]}


async def test_abc_uses_current_cycle_actual_facts_not_plan_or_old_cycle(scoped_context):
    result = await knowledge_context.assemble_knowledge_context(scoped_context, "a", "session",
        module="module_4", task="m4_abc")
    assert result["cycle_id"] == "current-cycle" and len(result["facts"]) == 1
    fact = result["facts"][0]
    assert fact["source"] == "module_four_record" and fact["cycle_id"] == "current-cycle"
    assert fact["values"]["phase_b"] == {"behavior":"本次在家休息"}
    assert "旧周期" not in str(result) and "计划散步" not in str(result)
    assert "abc_chain_summary" not in fact["values"]


async def test_activity_projection_maps_alias_and_omits_other_personal_fields(scoped_context):
    result = await knowledge_context.assemble_knowledge_context(scoped_context, "a", "session",
        module="module_2", task="m2_activity")
    fact = result["facts"][0]
    assert fact["values"] == {"target_activity_content":"计划散步", "pa_understanding_status":"unknown"}
    assert fact["confirmation"] == "unconfirmed"
    assert "core_values" not in str(result) and "coping_behavior" not in str(result)


async def test_unconfirmed_relationship_keeps_source_and_confirmation(scoped_context):
    result = await knowledge_context.assemble_knowledge_context(scoped_context, "a", "session",
        module="module_1", task="m1_ba_personalization")
    fact = result["facts"][0]
    assert fact["confirmation"] == "unconfirmed" and fact["source_message_id"] is None
    assert fact["values"]["action_state_relationship_summary"] == "未确认的行为状态关系"
    assert "chief_complaint" not in fact["values"]


async def test_other_user_has_no_access_to_context(scoped_context):
    result = await knowledge_context.assemble_knowledge_context(scoped_context, "b", "session",
        module="module_4", task="m4_abc")
    assert result["facts"] == [] and result["goal_id"] is None


async def test_wrong_module_task_never_broadens_scope():
    with pytest.raises(ValueError, match="Invalid knowledge context task"):
        await knowledge_context.assemble_knowledge_context(None, "a", "session",
            module="module_1", task="m4_abc")


async def test_legacy_false_reminder_snapshot_does_not_claim_user_opt_out(scoped_context):
    async with scoped_context() as db:
        await db.execute(insert(metadata.tables["module_three_record"]), {
            "id":"recording", "goal_id":"goal", "module_two_record_id":"plan", "version_no":1,
            "reminder_enabled":False})
        await db.execute(update(metadata.tables["pa_cycles"]).where(
            metadata.tables["pa_cycles"].c.id == "current-cycle").values(module_three_record_id="recording"))
        await db.commit()
    result = await knowledge_context.assemble_knowledge_context(scoped_context, "a", "session",
        module="module_3", task="m3_reminder")
    values = result["facts"][0]["values"]
    assert values["reminder_enabled"] is None and values["reminder_authorization_status"] == "not_loaded"


async def test_recording_state_uses_only_current_confirmed_binding(scoped_context):
    from app.m3_contract import VERSION
    async with scoped_context() as db:
        await db.execute(insert(metadata.tables["module_three_record"]), {
            "id":"confirmed-recording", "goal_id":"goal", "module_two_record_id":"plan", "version_no":1,
            "record_status":"confirmed", "confirmation_message_id":9, "recording_status":"declined",
            "recording_evidence":{"version":VERSION, "evidence":{"decision":{"scope":"activity_record"}}}})
        await db.execute(insert(metadata.tables["module_three_record"]), {
            "id":"new-draft", "goal_id":"goal", "module_two_record_id":"plan", "version_no":2,
            "recording_status":"accepted"})
        await db.execute(update(metadata.tables["pa_cycles"]).where(
            metadata.tables["pa_cycles"].c.id == "current-cycle").values(module_three_record_id="confirmed-recording"))
        await db.commit()
        result = await knowledge_context.read_recording_state(db, "a", {
            "active_goal_id":"goal", "active_cycle_id":"current-cycle"})
        assert result == {"recording_status":"declined", "recording_decision_scope":"activity_record"}
        other = await knowledge_context.read_recording_state(db, "b", {
            "active_goal_id":"goal", "active_cycle_id":"current-cycle"})
        assert other["recording_status"] == "unknown"
