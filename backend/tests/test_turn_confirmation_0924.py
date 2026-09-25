"""Front-of-turn commits drive the reply; history stays auditable."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.providers.base import Completion
from app.turn_confirmation import apply_pre_reply_decision
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api
from test_extraction_freshness_0916 import seed_m2

async def setup_turn(db, text, *, module="module_2"):
    await seed_m2(db)
    if module == "module_3":
        plans, cycles, runtime = (schema.tables[k] for k in ("module_two_record", "pa_cycles", "conversation_runtime_states"))
        await db.execute(update(plans).where(plans.c.id == "m2-draft").values(record_status="confirmed", confirmation_status="confirmed", confirmation_message_id=17))
        await db.execute(update(cycles).where(cycles.c.id == "m2-cycle").values(module_two_record_id="m2-draft", status="waiting_execution"))
        await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_3", flow_status="waiting_execution"))
    await db.execute(insert(ConversationMessage), {"id":21,"conversation_id":1,"position":2,"role":"user","content":text})
    await db.commit()
    context = SimpleNamespace(sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        store=SimpleNamespace(set_module=AsyncMock(),set_memory=AsyncMock()),
        settings=SimpleNamespace(router_request_timeout_seconds=3, extraction_max_tokens=4800),
        router_provider=SimpleNamespace(route_detailed=AsyncMock(return_value=Completion(text=json.dumps({"matched":True,"message_id":21,"quote":text}),model="test"))))
    return {"subject_id":"a","session_id":"chat-a","user_message_id":21}, context

@pytest.mark.asyncio
async def test_current_confirmation_commits_before_selecting_reply_module(goal_api):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "确认，就按这个计划试试。")
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    assert result["current_module"] == "module_3"
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_3"
    row = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
    assert row["record_status"] == "confirmed" and row["confirmation_message_id"] == 21

@pytest.mark.asyncio
async def test_m3_edit_preserves_old_cycle_and_plan(goal_api):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "我还没执行，想先改这个计划。", module="module_3")
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    assert result["current_module"] == "module_2" and result["active_cycle_id"] != "m2-cycle"
    plans = (await db.execute(select(schema.tables["module_two_record"]).order_by(schema.tables["module_two_record"].c.version_no))).mappings().all()
    assert len(plans) == 2 and plans[0]["record_status"] == "confirmed" and plans[1]["record_status"] == "draft"
    assert plans[1]["difficulty_evidence"] == plans[0]["difficulty_evidence"]
    cycle = (await db.execute(select(schema.tables["pa_cycles"]).where(schema.tables["pa_cycles"].c.id == "m2-cycle"))).mappings().one()
    assert cycle["module_two_record_id"] == "m2-draft"

@pytest.mark.asyncio
async def test_m3_actual_feedback_enters_review_before_reply(goal_api):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "刚才散步完成了。", module="module_3")
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_4"))
    assert result["current_module"] == "module_4"
    cycle = (await db.execute(select(schema.tables["pa_cycles"]))).mappings().one()
    assert cycle["status"] == "reviewing"

@pytest.mark.asyncio
async def test_invented_user_source_cannot_move_m3(goal_api):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "明天准备散步。", module="module_3")
    context.router_provider.route_detailed.return_value = Completion(text='{"matched":true,"message_id":21,"quote":"我做完了"}',model="test")
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_4"))
    assert result["current_module"] == "module_3"

@pytest.mark.asyncio
async def test_failed_commit_never_publishes_proposal(goal_api, monkeypatch):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "确认，就按这个计划试试。")
    async def fail(*args, **kwargs):
        raise RuntimeError("simulated storage failure")
    monkeypatch.setattr("app.dialogue_confirmation.commit_confirmation", fail)
    with pytest.raises(RuntimeError):
        await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_3"))
    context.store.set_module.assert_not_called()
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_2"
