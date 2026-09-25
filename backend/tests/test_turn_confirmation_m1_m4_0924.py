"""Source-backed M1/M4 front-of-turn commits and stale/partial-write guards."""
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
from test_m1_contract_0914 import _data, _raw, _turns
from test_cycle_program_0914 import seed_review
from test_turn_confirmation_0924 import setup_turn


def extraction_context(db, raw):
    provider = SimpleNamespace(name="isolated-extractor", model="test",
        route_detailed=AsyncMock(return_value=Completion(
            text=json.dumps(raw, ensure_ascii=False), model="test")))
    return SimpleNamespace(sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        provider=provider, router_provider=provider,
        store=SimpleNamespace(set_module=AsyncMock(), set_memory=AsyncMock()),
        settings=SimpleNamespace(router_request_timeout_seconds=3, extraction_max_tokens=4800))


async def m1_turn(db, raw=None):
    await db.execute(update(schema.tables["user_module_one_state"]).where(
        schema.tables["user_module_one_state"].c.user_id == "a").values(
            status="in_progress", completion_source="none"))
    messages = [{"id": 100 + index, "conversation_id": 1, "position": index,
                 "role": role, "content": content}
                for index, (role, content) in enumerate(_turns())]
    await db.execute(insert(ConversationMessage), messages)
    await db.commit()
    return ({"subject_id": "a", "session_id": "chat-a", "user_message_id": 109},
            extraction_context(db, raw or {**_data(), "m1_contract": _raw()}))


async def review_turn(db, *, decision, with_contract=True):
    await seed_review(db, decision=decision, with_contract=with_contract)
    messages = list((await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == 1).order_by(ConversationMessage.position))).scalars())
    body = {m.id: m.content for m in messages}
    review = (await db.execute(select(schema.tables["module_four_record"]))).mappings().one()
    raw = {key: review[key] for key in ("phase_a", "phase_b", "phase_c",
        "ba_reeducation_content", "review_decision", "review_summary")}
    raw["ai_abc_chain_summary"] = review["abc_chain_summary"]
    raw["phase_c"] = {k: v for k, v in raw["phase_c"].items() if k != "_m4_contract"}
    raw["m4_contract"] = {
        "phase_a_quote": body[10], "phase_b_quote": body[11], "phase_c_quote": body[12],
        "emotion_improved": True, "emotion_quote": body[12], "summary_quote": body[13],
        "chain_status": "confirmed", "confirmation_quote": body[14],
        "education_quote": body[15], "understanding_quote": body[16],
        "core_questions_resolved": True, "difficulty_status": "none", "difficulty_quote": body[17],
        "decision_quote": body[18], "review_summary_quote": body[20],
    }
    raw["review_followup"] = {"action": "continue" if decision == 1 else "adjust", "source_quote": body[18]}
    await db.execute(insert(ConversationMessage), {"id": 21, "conversation_id": 1,
        "position": 11, "role": "user", "content": body[18]})
    await db.commit()
    return ({"subject_id": "a", "session_id": "chat-a", "user_message_id": 21},
            extraction_context(db, raw))


@pytest.mark.asyncio
async def test_m1_current_user_consent_is_committed_before_reply_selection(goal_api):
    _, db, _ = goal_api
    state, context = await m1_turn(db)
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    assert result["current_module"] == "module_2"
    assert result["confirmation_receipt"] == {"module": "module_2", "cycle_id": None}
    row = (await db.execute(select(schema.tables["module_one_record"]))).mappings().one()
    assert row["record_status"] == "confirmed"
    assert row["confirmation_message_id"] == row["willingness_message_id"] == 109
    assert '"turn": 9, "role": "user"' in context.provider.route_detailed.call_args.kwargs["user"]
    context.store.set_module.assert_awaited_once_with("chat-a", "module_2")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_source", ["assistant", "invented"])
async def test_m1_invalid_consent_source_cannot_be_promoted(goal_api, bad_source):
    _, db, _ = goal_api
    raw = _raw()
    raw["consent_quote"] = _turns()[8][1] if bad_source == "assistant" else "我已经完全理解并批准创建目标。"
    state, context = await m1_turn(db, {**_data(), "m1_contract": raw})
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    assert result["current_module"] == "module_1"
    assert "confirmation_receipt" not in result
    row = (await db.execute(select(schema.tables["module_one_record"]))).mappings().one()
    assert row["record_status"] == "draft" and row["confirmation_message_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,target", [(1, "module_3"), (3, "module_2")])
async def test_m4_sourced_current_turn_commits_next_cycle_before_reply(goal_api, decision, target):
    _, db, _ = goal_api
    state, context = await review_turn(db, decision=decision)
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module=target))
    assert result["current_module"] == target
    assert result["active_cycle_id"] != "reviewed-cycle"
    cycles, reviews = schema.tables["pa_cycles"], schema.tables["module_four_record"]
    previous = (await db.execute(select(cycles).where(cycles.c.id == "reviewed-cycle"))).mappings().one()
    successor = (await db.execute(select(cycles).where(cycles.c.id == result["active_cycle_id"]))).mappings().one()
    review = (await db.execute(select(reviews))).mappings().one()
    assert previous["status"] == "completed" and successor["ordinal"] == 2
    assert successor["status"] == ("waiting_execution" if decision == 1 else "planning")
    assert review["record_status"] == "confirmed" and review["confirmation_message_id"] == 14
    event = (await db.execute(select(schema.tables["ai_decision_logs"]).where(
        schema.tables["ai_decision_logs"].c.decision_type == "user_confirmation"))).mappings().one()
    assert event["turn_id"] == "21" and event["evidence_message_ids"] == [21]
    context.store.set_module.assert_awaited_once_with("chat-a", target)


@pytest.mark.asyncio
async def test_m4_continue_failure_rolls_back_confirmation_and_cycle_closure(goal_api, monkeypatch):
    from app.v2_repository import V2Conflict
    _, db, _ = goal_api
    state, context = await review_turn(db, decision=1, with_contract=False)
    async def unavailable(*args, **kwargs):
        raise V2Conflict("simulated cycle creation failure")
    monkeypatch.setattr("app.v2_repository.start_cycle", unavailable)
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_3"))
    assert result["current_module"] == "module_4"
    assert "confirmation_receipt" not in result
    cycles, reviews = schema.tables["pa_cycles"], schema.tables["module_four_record"]
    previous = (await db.execute(select(cycles).where(cycles.c.id == "reviewed-cycle"))).mappings().one()
    review = (await db.execute(select(reviews))).mappings().one()
    assert previous["status"] == "reviewing"  # validated extraction may advance review status
    assert previous["completed_at"] is None
    assert review["record_status"] == "draft"
    assert list((await db.execute(select(cycles.c.id))).scalars()) == ["reviewed-cycle"]


@pytest.mark.asyncio
async def test_m3_newer_user_during_event_classification_prevents_stale_transition(goal_api):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "我还没执行，想先改这个计划。", module="module_3")
    async def changed_turn(**kwargs):
        async with context.sessionmaker() as concurrent:
            await concurrent.execute(insert(ConversationMessage), {"id": 22, "conversation_id": 1,
                "position": 3, "role": "user", "content": "等等，先保持这个计划。"})
            await concurrent.commit()
        return Completion(text=json.dumps({"matched": True, "message_id": 21,
            "quote": "我还没执行，想先改这个计划。"}), model="test")
    context.router_provider.route_detailed.side_effect = changed_turn
    try:
        result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    except ValueError as exc:
        assert "superseded" in str(exc) or "boundary" in str(exc)
    else:
        assert result["current_module"] == "module_3"
    _, runtime = await runtime_for(db, "chat-a")
    assert runtime["current_module"] == "module_3" and runtime["active_cycle_id"] == "m2-cycle"
    assert list((await db.execute(select(schema.tables["pa_cycles"].c.id))).scalars()) == ["m2-cycle"]


@pytest.mark.asyncio
async def test_m3_runtime_revision_during_event_classification_prevents_stale_transition(goal_api):
    _, db, _ = goal_api
    state, context = await setup_turn(db, "我还没执行，想先改这个计划。", module="module_3")
    runtime = schema.tables["conversation_runtime_states"]
    async def changed_runtime(**kwargs):
        async with context.sessionmaker() as concurrent:
            await concurrent.execute(update(runtime).where(runtime.c.conversation_id == 1).values(
                row_version=runtime.c.row_version + 1, memory={"newer_runtime": True}))
            await concurrent.commit()
        return Completion(text=json.dumps({"matched": True, "message_id": 21,
            "quote": "我还没执行，想先改这个计划。"}), model="test")
    context.router_provider.route_detailed.side_effect = changed_runtime
    try:
        result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    except ValueError as exc:
        assert "superseded" in str(exc) or "boundary" in str(exc)
    else:
        assert result["current_module"] == "module_3"
    _, actual = await runtime_for(db, "chat-a")
    assert actual["current_module"] == "module_3" and actual["active_cycle_id"] == "m2-cycle"
    assert actual["memory"] == {"newer_runtime": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["module_1", "module_4"])
async def test_late_extraction_cannot_write_over_new_user_boundary(goal_api, module):
    _, db, _ = goal_api
    state, context = await (m1_turn(db) if module == "module_1" else review_turn(db, decision=1))
    completion = context.provider.route_detailed.return_value
    records = schema.tables["module_one_record" if module == "module_1" else "module_four_record"]
    before = [dict(row) for row in (await db.execute(select(records))).mappings()]
    await db.commit()
    async def late_extraction(**kwargs):
        async with context.sessionmaker() as concurrent:
            await concurrent.execute(insert(ConversationMessage), {"id": 200, "conversation_id": 1,
                "position": 30, "role": "user", "content": "等等，我想更正刚才的决定。"})
            await concurrent.commit()
        return completion
    context.provider.route_detailed.side_effect = late_extraction
    result = await apply_pre_reply_decision(state, context,
        SimpleNamespace(target_module="module_2" if module == "module_1" else "module_3"))
    assert result["current_module"] == module
    assert "confirmation_receipt" not in result
    after = [dict(row) for row in (await db.execute(select(records))).mappings()]
    assert after == before


@pytest.mark.asyncio
async def test_m1_late_extraction_cannot_recreate_draft_after_module_transition(goal_api):
    _, db, _ = goal_api
    state, context = await m1_turn(db)
    completion = context.provider.route_detailed.return_value
    runtime = schema.tables["conversation_runtime_states"]
    async def late_extraction(**kwargs):
        async with context.sessionmaker() as concurrent:
            await concurrent.execute(update(runtime).where(runtime.c.conversation_id == 1).values(
                current_module="module_2", row_version=runtime.c.row_version + 1))
            await concurrent.commit()
        return completion
    context.provider.route_detailed.side_effect = late_extraction
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_2"))
    assert result["current_module"] == "module_2"
    assert "confirmation_receipt" not in result
    assert not list((await db.execute(select(schema.tables["module_one_record"]))).mappings())


@pytest.mark.asyncio
async def test_m2_semantic_wait_cannot_confirm_after_a_newer_user_turn(goal_api):
    _, db, _ = goal_api
    text = "我已经看完全部安排，也愿意照此尝试。"
    state, context = await setup_turn(db, text)
    async def delayed_confirmation(**kwargs):
        async with context.sessionmaker() as concurrent:
            await concurrent.execute(insert(ConversationMessage), {"id": 22, "conversation_id": 1,
                "position": 4, "role": "user", "content": "等一下，我还想修改时间。"})
            await concurrent.commit()
        return json.dumps({"intent": "confirm", "source_quote": text, "amendment": False, "unresolved": False})
    context.router_provider.route = AsyncMock(side_effect=delayed_confirmation)
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module="module_3"))
    context.router_provider.route.assert_awaited_once()
    assert result["current_module"] == "module_2" and "confirmation_receipt" not in result
    row = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
    assert row["record_status"] == "draft" and row["confirmation_message_id"] is None


@pytest.mark.asyncio
async def test_full_v2_graph_commits_m2_before_loading_m3_reply_prompt(
    goal_api, provider, store, monkeypatch,
):
    from app.config import get_settings
    from app.db import Base
    from app.graph import get_graph
    from app.graph.state import GraphContext
    from app.prompts import MODULE_PROMPTS
    from app.providers.base import as_text
    from app.retrieval import StubKnowledgeBase
    from app.schemas import Message

    monkeypatch.setenv("DATABASE_SCHEMA_VERSION", "v2")
    get_settings.cache_clear()
    _, db, _ = goal_api
    # Add unrelated graph telemetry/profile tables to the same temporary DB;
    # the already-created V2 business tables retain their real definitions.
    await db.run_sync(lambda sync: Base.metadata.create_all(sync.connection()))
    state, seeded_context = await setup_turn(db, "确认，就按这个计划试试。")
    _, persisted = await runtime_for(db, "chat-a")
    prior = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == 1, ConversationMessage.id != 21)
        .order_by(ConversationMessage.position))).scalars().all()
    await db.commit()
    await store.adopt("chat-a", [Message(role=message.role, content=message.content)
        for message in prior], module="module_2", memory=persisted["memory"])
    router = type(provider)()
    router.route_result = '{"target_module":"2","completed_steps":[]}'
    observations = []
    async def checked_reply(*, system, messages):
        async with seeded_context.sessionmaker() as current:
            _, actual = await runtime_for(current, "chat-a")
            plan = (await current.execute(select(schema.tables["module_two_record"]))).mappings().one()
            assert actual["current_module"] == "module_3"
            assert plan["record_status"] == "confirmed" and plan["confirmation_message_id"] == 21
        assert MODULE_PROMPTS["module_3"] in as_text(system)
        assert MODULE_PROMPTS["module_2"] not in as_text(system)
        assert "风险信号检测器" in router.route_systems[0]
        assert any("target_module" in prompt for prompt in router.route_systems[1:])
        observations.append("committed_before_main_reply")
        return Completion(text="计划已经确认。接下来我们可以聊聊怎样记录执行后的感受。", model="test")
    monkeypatch.setattr(provider, "complete", checked_reply)
    context = GraphContext(provider=provider, router_provider=router, store=store,
        knowledge_base=StubKnowledgeBase({}), settings=get_settings(),
        sessionmaker=seeded_context.sessionmaker, stream=False)
    result = await get_graph().ainvoke({**state, "user_input": "确认，就按这个计划试试。", "metadata": {}}, context=context)
    assert observations == ["committed_before_main_reply"]
    assert result["current_module"] == result["extracted_intent"] == result["next_module"] == "module_3"
    assert result["confirmation_receipt"] == {"module": "module_3", "cycle_id": "m2-cycle"}
    assert (await store.get("chat-a")).module == "module_3"
