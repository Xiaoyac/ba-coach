"""Router-only stage authority without invented clinical confirmations."""
import dataclasses
import json

import pytest
import pytest_asyncio
from sqlalchemy import delete, insert, select, update, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database_v2_schema import metadata as schema
from app.models import AccountSettings, AIExecutionEvent, Conversation, ConversationMessage, UserAccount
from app.pre_reply_routing import load_routing_snapshot, route_before_reply
from app.router_agent import RouterDecision, decide_target_module_with_reasoning
from app.routing_modes import ROUTER_CODE, ROUTER_ONLY, apply_router_only_decision, effective_routing_mode


@pytest.mark.parametrize("current", [f"module_{n}" for n in range(1, 5)])
@pytest.mark.parametrize("target", [f"module_{n}" for n in range(1, 5)])
async def test_router_only_accepts_all_valid_modules_without_card_or_steps(provider, current, target):
    provider.route_result = json.dumps({"target_module": target, "completed_steps": [],
        "revoked_steps": ["pa_card_completed"], "revocation_evidence": "重新安排"})
    decision = await decide_target_module_with_reasoning(provider, current_module=current,
        user_input="重新安排", has_pa_card=False, routing_mode=ROUTER_ONLY)
    assert decision.target_module == target and not decision.error_code
    assert "数据库已确认才允许跳转等执行前置在此模式停用" in provider.route_systems[-1]


@pytest.mark.parametrize("raw", ["", "not json", "5", '{"target_module":"module_9"}', '{"target_module":true}'])
async def test_router_only_invalid_output_holds_position(provider, raw):
    provider.route_result = raw
    decision = await decide_target_module_with_reasoning(provider, current_module="module_2",
        user_input="继续", has_pa_card=False, routing_mode=ROUTER_ONLY)
    assert decision.target_module == "module_2" and decision.error_code


@pytest_asyncio.fixture
async def router_only_db(context):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(schema.create_all)
        for model in (Conversation, ConversationMessage, AIExecutionEvent, UserAccount, AccountSettings):
            await connection.run_sync(model.__table__.create)
    async with maker() as db:
        await db.execute(insert(schema.tables["user_profile"]), [{"uuid": "a"}, {"uuid": "b"}])
        await db.execute(insert(UserAccount), [
            {"id": 1, "username": "admin-test", "password_hash": "unused", "profile_uuid": "a"},
            {"id": 2, "username": "member-test", "password_hash": "unused", "profile_uuid": "b"}])
        await db.execute(insert(AccountSettings), [{"account_id": 1, "role": "admin"}, {"account_id": 2, "role": "user"}])
        await db.execute(insert(Conversation), [{"id": 1, "session_id": "s", "subject_id": "a", "revision": 2},
                                                {"id": 2, "session_id": "other", "subject_id": "b", "revision": 1}])
        await db.execute(insert(ConversationMessage), [
            {"id": 101, "conversation_id": 1, "position": -1, "role": "assistant", "content": "开场白"},
            {"id": 102, "conversation_id": 1, "position": 0, "role": "user", "content": "下一步"}])
        await db.execute(insert(schema.tables["conversation_runtime_states"]), [
            {"conversation_id": 1, "current_module": "module_1", "row_version": 7,
             "memory": {"routing_mode": ROUTER_ONLY, "fresh_m1": True}},
            {"conversation_id": 2, "current_module": "module_1", "row_version": 0,
             "memory": {"routing_mode": ROUTER_ONLY}}])
        await db.commit()
    ctx = dataclasses.replace(context, sessionmaker=maker,
        settings=context.settings.model_copy(update={"database_schema_version": "v2"}))
    await ctx.store.adopt(session_id="s", messages=[], module="module_1", memory={"routing_mode": ROUTER_ONLY})
    try:
        yield ctx, maker
    finally:
        await engine.dispose()


def current_turn(**extra):
    return {"session_id": "s", "subject_id": "a", "user_message_id": 102,
            "user_input": "下一步", "current_module": "module_1", **extra}


async def test_mode_comes_from_owned_memory_and_current_admin_role(router_only_db):
    ctx, maker = router_only_db
    async with maker() as db:
        admin = await db.get(Conversation, 1)
        member = await db.get(Conversation, 2)
        assert await effective_routing_mode(db, conversation=admin,
            state={"memory": {"routing_mode": ROUTER_ONLY}}, user_id="a") == ROUTER_ONLY
        assert await effective_routing_mode(db, conversation=admin,
            state={"memory": {}}, user_id="a") == ROUTER_CODE
        assert await effective_routing_mode(db, conversation=member,
            state={"memory": {"routing_mode": ROUTER_ONLY}}, user_id="b") == ROUTER_ONLY
        assert await effective_routing_mode(db, conversation=admin,
            state={"memory": {"routing_mode": ROUTER_ONLY}}, user_id="b") == ROUTER_CODE
    # Request/state-injected values cannot override the durable preference.
    rt = schema.tables["conversation_runtime_states"]
    async with maker() as db:
        await db.execute(update(rt).where(rt.c.conversation_id == 1).values(memory={"routing_mode": ROUTER_CODE}))
        await db.commit()
    snapshot = await load_routing_snapshot(current_turn(routing_mode=ROUTER_ONLY,
        memory={"routing_mode": ROUTER_ONLY}, metadata={"routing_mode": ROUTER_ONLY}), ctx)
    assert snapshot["routing_mode"] == ROUTER_CODE


@pytest.mark.parametrize("target", ["module_2", "module_3", "module_4"])
async def test_router_only_commits_without_clinical_completion_and_survives_reload(router_only_db, provider, monkeypatch, target):
    ctx, maker = router_only_db
    provider.route_result = target
    from app import turn_confirmation
    async def forbidden(*args, **kwargs):
        pytest.fail("router-only must not invoke the completion/confirmation service")
    monkeypatch.setattr(turn_confirmation, "apply_pre_reply_decision", forbidden)
    result = await route_before_reply(current_turn(), ctx)
    assert result["current_module"] == result["extracted_intent"] == result["next_module"] == target
    assert result["routing_mode"] == ROUTER_ONLY
    assert result["telemetry"]["router_pre_reply"]["routing_mode"] == ROUTER_ONLY
    assert result["telemetry"]["prompt_sources"][-1]["database_transition_preconditions_disabled"] is True
    assert result.get("confirmation_receipt") is None
    assert (await ctx.store.get("s")).module == target
    assert (await load_routing_snapshot(current_turn(), ctx))["current_module"] == target
    async with maker() as db:
        runtime = (await db.execute(select(schema.tables["conversation_runtime_states"]).where(
            schema.tables["conversation_runtime_states"].c.conversation_id == 1))).mappings().one()
        assert runtime["row_version"] == 8
        assert runtime["active_goal_id"] is None and runtime["active_cycle_id"] is None
        assert runtime["flow_status"] == "active"
        assert (await db.get(Conversation, 1)).revision == 3
        for name in ("module_one_record", "module_two_record", "module_three_record", "module_four_record", "pa_goals", "pa_cycles"):
            assert await db.scalar(select(func.count()).select_from(schema.tables[name])) == 0
        logs = (await db.execute(select(schema.tables["ai_decision_logs"]))).mappings().all()
        assert len(logs) == 1 and logs[0]["decision_type"] == "router_only_decision"
        assert logs[0]["evidence_message_ids"] == [102]
        assert logs[0]["decision_value"]["business_confirmation_performed"] is False


async def test_router_only_ignores_forced_request_module(router_only_db, provider):
    ctx, _ = router_only_db
    provider.route_result = "module_4"
    result = await route_before_reply(current_turn(forced_module="module_2"), ctx)
    assert result["extracted_intent"] == "module_4" and result["forced_module"] is None
    assert result["telemetry"]["ignored_forced_module"] == "module_2"
    assert len(provider.route_calls) == 1


@pytest.mark.parametrize("mutation", ["version", "new_user", "other_user", "account_removed", "mode_changed", "goal_binding"])
async def test_router_only_stale_or_unauthorized_proposal_cannot_commit(router_only_db, mutation):
    ctx, maker = router_only_db
    prepared = {**current_turn(), **await load_routing_snapshot(current_turn(), ctx)}
    rt = schema.tables["conversation_runtime_states"]
    async with maker() as db:
        if mutation == "version":
            await db.execute(update(rt).where(rt.c.conversation_id == 1).values(row_version=8))
        elif mutation == "new_user":
            await db.execute(insert(ConversationMessage), {"id": 103, "conversation_id": 1,
                "position": 2, "role": "user", "content": "更新的输入"})
        elif mutation == "other_user":
            prepared["subject_id"] = "b"
        elif mutation == "account_removed":
            await db.execute(delete(AccountSettings).where(AccountSettings.account_id == 1))
        elif mutation == "mode_changed":
            await db.execute(update(rt).where(rt.c.conversation_id == 1).values(memory={"routing_mode": ROUTER_CODE}))
        else:
            await db.execute(insert(schema.tables["pa_goals"]), {"id": "g", "user_id": "a", "title": "新的目标"})
            await db.execute(update(rt).where(rt.c.conversation_id == 1).values(active_goal_id="g"))
        await db.commit()
    decision = RouterDecision(target_module="module_4", reasoning_content="", model="stub", completed_steps=[], usage={})
    with pytest.raises(ValueError):
        await apply_router_only_decision(prepared, ctx, decision)
    async with maker() as db:
        assert await db.scalar(select(rt.c.current_module).where(rt.c.conversation_id == 1)) == "module_1"
        assert await db.scalar(select(func.count()).select_from(schema.tables["ai_decision_logs"])) == 0


@pytest.mark.parametrize("failure", ["invalid", "timeout"])
async def test_router_only_failure_keeps_persisted_module(router_only_db, provider, monkeypatch, failure):
    ctx, maker = router_only_db
    if failure == "invalid":
        provider.route_result = "not a module"
    else:
        async def timeout(**kwargs):
            raise TimeoutError("simulated")
        monkeypatch.setattr(provider, "route_with_reasoning", timeout)
    result = await route_before_reply(current_turn(), ctx)
    assert result["current_module"] == result["next_module"] == "module_1"
    assert result["telemetry"]["router_pre_reply"]["status"] == "failed"
    assert "保留当前模块" in result["routing_reasoning_content"]


async def test_risk_takes_precedence_without_router_or_module_change(router_only_db, provider):
    ctx, _ = router_only_db
    result = await route_before_reply(current_turn(risk={"risk_status": 1}), ctx)
    assert result["next_module"] == "module_1" and result["routed_by"] == "risk_hold"
    assert not provider.route_calls


@pytest.mark.parametrize("current,target", [("module_2", "module_3"), ("module_3", "module_4"), ("module_4", "module_1")])
async def test_router_only_keeps_existing_unconfirmed_business_records_intact(router_only_db, provider, current, target):
    ctx, maker = router_only_db
    rt, goals, plans, cycles, progress = [schema.tables[name] for name in (
        "conversation_runtime_states", "pa_goals", "module_two_record", "pa_cycles", "pa_cycle_progress")]
    async with maker() as db:
        await db.execute(insert(goals), {"id": "g", "user_id": "a", "title": "散步", "status": "draft"})
        await db.execute(insert(plans), {"id": "p", "goal_id": "g", "version_no": 1, "timezone": "Asia/Shanghai",
            "activity_content": "散步", "potential_barriers": ["会累"], "barrier_coping_plan": []})
        await db.execute(insert(cycles), {"id": "c", "goal_id": "g", "ordinal": 1, "status": "planning"})
        await db.execute(insert(progress), {"cycle_id": "c", "module_2_steps": [], "module_3_steps": [], "module_4_steps": []})
        await db.execute(update(rt).where(rt.c.conversation_id == 1).values(
            current_module=current, active_goal_id="g", active_cycle_id="c"))
        await db.commit()
        before = {table.name: [dict(row) for row in (await db.execute(select(table))).mappings()]
                  for table in (goals, plans, cycles, progress)}
    provider.route_result = target
    result = await route_before_reply(current_turn(current_module=current), ctx)
    assert result["next_module"] == target
    assert result["active_cycle_id"] == "c"
    async with maker() as db:
        after = {table.name: [dict(row) for row in (await db.execute(select(table))).mappings()]
                 for table in (goals, plans, cycles, progress)}
    assert before == after


async def test_router_only_committed_stage_survives_context_refresh_failure(router_only_db, provider, monkeypatch):
    ctx, _ = router_only_db
    from app import v2_workflow
    async def broken_context(*args, **kwargs):
        raise RuntimeError("simulated enrichment failure")
    monkeypatch.setattr(v2_workflow, "clinical_context", broken_context)
    provider.route_result = "4"
    result = await route_before_reply(current_turn(clinical_context=["previous module facts"]), ctx)
    assert result["current_module"] == result["next_module"] == "module_4"
    assert result["clinical_context"] == []
    assert result["telemetry"]["router_pre_reply"]["database_module"] == "module_4"


async def test_router_code_retains_business_gate_for_same_router_output(router_only_db, provider, monkeypatch):
    ctx, maker = router_only_db
    rt = schema.tables["conversation_runtime_states"]
    async with maker() as db:
        await db.execute(update(rt).where(rt.c.conversation_id == 1).values(memory={"routing_mode": ROUTER_CODE}))
        await db.commit()
    from app import turn_confirmation
    calls = []
    async def normal_commit(state, context, decision):
        calls.append(decision.target_module)
        return {"current_module": "module_1", "diagnostics": {"block_reasons": ["missing completion"]}}
    monkeypatch.setattr(turn_confirmation, "apply_pre_reply_decision", normal_commit)
    provider.route_result = "4"
    result = await route_before_reply(current_turn(), ctx)
    assert calls == ["module_1"]  # The normal structural clamp remains active.
    assert result["next_module"] == "module_1"
    assert result["telemetry"]["router_pre_reply"]["routing_mode"] == ROUTER_CODE
