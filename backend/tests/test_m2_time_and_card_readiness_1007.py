"""Isolated M2 regression: real SQLite tools, no provider or production data."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database_v2_schema import metadata as schema
from app.goal_card_interaction import (
    GOAL_CARD_POLICY, M2_SCHEDULE_POLICY, bind_card_action, confirmation_button_text, review_passes,
)
from app.goal_card_workspace import _plan_fields
from app.models import AccountSettings, UserAccount, Conversation, ConversationMessage
from app.pa_card_tools import PACardTools
from app.plan_contract import missing_plan_fields, normalize_coping_evidence
from app.prompts import build_system_prompt, build_system_segments


SCHEDULE = "下周五组会结束后的晚上"
NO_BARRIER = "基本没有什么阻力"
SOURCE = f"我选择{SCHEDULE}去打羽毛球，难度0，{NO_BARRIER}。"


def no_barrier_data(message_id=101, quote=NO_BARRIER):
    return {"potential_barriers": [quote], "barrier_coping_plan": [{
        "status": "not_applicable", "source_message_id": message_id, "source_quote": quote}]}


def plan_data():
    return {"id": "plan", "goal_id": "goal", "version_no": 1,
        "timezone": "Asia/Shanghai", "record_status": "draft",
        "confirmation_status": "unconfirmed", "activity_content": "打羽毛球",
        "schedule_text": SCHEDULE, "difficulty_rating": 0,
        "difficulty_evidence": {"rating": {"value": 0, "message_id": 101, "quote": "0", "score_text": "0"}},
        "potential_barriers": [NO_BARRIER], "barrier_coping_plan": []}


def test_old_empty_coping_does_not_become_verified_no_barrier_automatically():
    old = plan_data()
    original = deepcopy(old)
    assert missing_plan_fields(old) == ["barrier_coping_plan"]
    source = SimpleNamespace(id=101, role="user", content=SOURCE)
    explicit = normalize_coping_evidence(no_barrier_data(), [source], existing=old)
    assert missing_plan_fields({**old, **explicit}) == []
    assert old == original


@pytest.mark.parametrize("message_id,quote", [(999, NO_BARRIER), (101, "用户明确表示没有障碍")])
def test_no_barrier_normalization_requires_real_user_id_and_literal_quote(message_id, quote):
    source = SimpleNamespace(id=101, role="user", content=SOURCE)
    with pytest.raises(ValueError, match="invalid_no_barrier_source"):
        normalize_coping_evidence(no_barrier_data(message_id, quote), [source], existing=plan_data())


@pytest_asyncio.fixture
async def card_db():
    # Dedicated engine; no get_engine(), app startup, .env or provider factory.
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(schema.create_all)
        for model in (UserAccount, AccountSettings, Conversation, ConversationMessage):
            await conn.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await db.execute(insert(schema.tables["user_profile"]), [{"uuid": "a"}, {"uuid": "b"}])
        await db.execute(insert(Conversation), [
            {"id": 1, "session_id": "m2-test", "subject_id": "a"},
            {"id": 2, "session_id": "other-test", "subject_id": "b"}])
        await db.execute(insert(ConversationMessage), [
            {"id": 101, "conversation_id": 1, "position": 0, "role": "user", "content": SOURCE},
            {"id": 102, "conversation_id": 1, "position": 1, "role": "assistant", "content": "已了解你的选择。"},
            {"id": 201, "conversation_id": 2, "position": 0, "role": "user", "content": SOURCE}])
        await db.execute(insert(schema.tables["pa_goals"]), {"id": "goal", "user_id": "a", "title": "打羽毛球"})
        await db.execute(insert(schema.tables["module_two_record"]), plan_data())
        await db.execute(insert(schema.tables["pa_cycles"]), {
            "id": "cycle", "goal_id": "goal", "ordinal": 1, "status": "planning"})
        await db.execute(insert(schema.tables["pa_cycle_progress"]), {
            "cycle_id": "cycle", "module_2_steps": [], "module_3_steps": [], "module_4_steps": []})
        await db.execute(insert(schema.tables["conversation_runtime_states"]), {
            "conversation_id": 1, "current_module": "module_2", "flow_status": "active",
            "active_goal_id": "goal", "active_cycle_id": "cycle", "memory": {}})
        await db.execute(insert(schema.tables["goal_card_workspaces"]), {
            "id": "card", "user_id": "a", "conversation_id": 1, "kind": "primary",
            "phase": "discussing", "revision": 1, "fields": _plan_fields(plan_data()),
            "concerns": [], "goal_id": "goal", "plan_id": "plan", "cycle_id": "cycle",
            "opened_message_id": 101})
        await db.commit()
    yield maker
    await engine.dispose()


def executor(maker, *, boundary=101, assistant=102):
    return PACardTools(maker=maker, session_id="m2-test", user_id="a",
        user_message_id=boundary, module="module_2", ui_enabled=True, assistant_message_id=assistant)


async def call(tools, name, args):
    return await tools.execute({"id": f"test-{name}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})


async def review(tools, **changes):
    snapshot = await call(tools, "get_pa_card", {})
    args = {"state_version": snapshot["state_version"], "card_id": "card",
        "card_revision": snapshot["goal_card"]["revision"], "is_pa": True,
        "near_term": False, "schedule_status": "actionable",
        "schedule_source": {"source_message_id": 101, "source_quote": SCHEDULE},
        "manageable": True, "concerns": []}
    args.update(changes)
    return await call(tools, "review_goal_card", args)


async def save_no_barrier(tools):
    snapshot = await call(tools, "get_pa_card", {})
    return await call(tools, "save_pa_card", {"state_version": snapshot["state_version"],
        "data": {"target_activity_content": "打羽毛球", "goal_proposal": None, **no_barrier_data()}})


async def confirmed(maker):
    async with maker() as db:
        card = (await db.execute(select(schema.tables["goal_card_workspaces"]))).mappings().one()
        content = confirmation_button_text("primary", card["revision"])
        await db.execute(insert(ConversationMessage), {
            "id": 103, "conversation_id": 1, "position": 2, "role": "user", "content": content})
        conversation = await db.get(Conversation, 1)
        await bind_card_action(db, conversation, 103, {"goal_card_action": "confirm",
            "goal_card_id": "card", "goal_card_revision": str(card["revision"])})
        await db.commit()
    tools = executor(maker, boundary=103, assistant=None)
    snapshot = await call(tools, "get_pa_card", {})
    return await call(tools, "confirm_pa_card", {"state_version": snapshot["state_version"]})


@pytest.mark.asyncio
async def test_old_empty_coping_stays_blocked_then_sourced_repair_displays_and_confirms(card_db):
    tools = executor(card_db)
    blocked = await review(tools)
    assert blocked["status"] == "goal_card_reviewed" and blocked["ready"] is False
    assert blocked["readiness"]["missing_fields"] == ["barrier_coping_plan"]
    snapshot = await call(tools, "get_pa_card", {})
    assert snapshot["draft"]["barrier_coping_plan"] == []
    present = await call(tools, "present_pa_card", {"state_version": snapshot["state_version"]})
    assert present["status"] == "blocked"
    assert present["missing_fields"] == ["barrier_coping_plan"]
    assert (await save_no_barrier(tools))["status"] == "draft_saved"
    ready = await review(tools)
    assert ready["status"] == "ready_to_display"
    assert ready["goal_card"]["phase"] == "ready"
    async with card_db() as db:
        message = await db.get(ConversationMessage, 102)
        assert ready["display_text"] in message.content
    result = await confirmed(card_db)
    assert result["status"] == "confirmed", result
    assert result["module"] == "module_3"
    async with card_db() as db:
        saved = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
        assert saved["record_status"] == "confirmed"
        assert saved["schedule_text"] == SCHEDULE
        assert saved["confirmation_message_id"] == 103


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule,source_quote", [
    ("下周五组会结束后的晚上", SCHEDULE),
    ("今天晚饭后", "今天晚饭后"),
    ("现在", "现在"),
    ("周五晚饭后", "不是昨天，是周五晚饭后"),
])
async def test_actionable_schedule_is_not_limited_to_three_days_or_exact_clock(card_db, schedule, source_quote):
    async with card_db() as db:
        await db.execute(update(ConversationMessage).where(ConversationMessage.id == 101).values(
            content=f"我选择{source_quote}去打羽毛球，难度0，{NO_BARRIER}。"))
        await db.execute(update(schema.tables["module_two_record"]).values(schedule_text=schedule))
        row = (await db.execute(select(schema.tables["goal_card_workspaces"]))).mappings().one()
        await db.execute(update(schema.tables["goal_card_workspaces"]).values(fields={**row["fields"], "schedule_text": schedule}))
        await db.commit()
    tools = executor(card_db)
    assert (await save_no_barrier(tools))["status"] == "draft_saved"
    result = await review(tools, schedule_source={"source_message_id": 101, "source_quote": source_quote})
    assert result["status"] == "ready_to_display", result
    assert (await confirmed(card_db))["status"] == "confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["past", "unclear"])
async def test_past_or_unknown_not_replaced_by_near_term_flag(card_db, status):
    tools = executor(card_db)
    assert (await save_no_barrier(tools))["status"] == "draft_saved"
    result = await review(tools, near_term=True, schedule_status=status, schedule_source=None)
    assert not result["ready"]
    assert "pa_time_difficulty_review_not_passed" in result["readiness"]["blockers"]
    assert {"past": "schedule_in_past", "unclear": "schedule_unclear"}[status] in result["readiness"]["blockers"]
    assert result["goal_card"]["phase"] == "discussing"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_id,quote", [(201, SCHEDULE), (102, "已了解"), (101, "不存在的时间原话"), (True, SCHEDULE)])
async def test_schedule_review_cannot_borrow_other_user_assistant_or_invent_source(card_db, source_id, quote):
    result = await review(executor(card_db), schedule_source={"source_message_id": source_id, "source_quote": quote})
    assert result["status"] == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"is_pa": False}, {"manageable": False}, {"concerns": ["用户仍有未解决的安全顾虑"]}])
async def test_other_review_gates_remain(card_db, changes):
    tools = executor(card_db)
    assert (await save_no_barrier(tools))["status"] == "draft_saved"
    result = await review(tools, **changes)
    assert not result["ready"]
    assert result["goal_card"]["phase"] == "discussing"


def test_old_review_and_changed_versions_need_recheck_not_silent_upgrade():
    row = {"kind": "primary", "revision": 5, "concerns": [],
        "fields": {"activity_content": "打羽毛球", "schedule_text": SCHEDULE, "difficulty_rating": 0},
        "review": {"revision": 5, "is_pa": True, "near_term": True, "manageable": True}}
    assert not review_passes(row)
    row["review"].update(schedule_status="actionable", schedule_text=SCHEDULE,
        schedule_source={"source_message_id": 101, "source_quote": SCHEDULE})
    assert review_passes(row)
    row["revision"] = 6
    assert not review_passes(row)
    row["revision"] = 5
    row["fields"]["difficulty_rating"] = 6
    assert not review_passes(row)


@pytest.mark.asyncio
async def test_already_displayed_legacy_same_revision_can_still_confirm(card_db):
    tools = executor(card_db)
    assert (await save_no_barrier(tools))["status"] == "draft_saved"
    assert (await review(tools, near_term=True))["status"] == "ready_to_display"
    async with card_db() as db:
        table = schema.tables["goal_card_workspaces"]
        row = (await db.execute(select(table))).mappings().one()
        legacy_review = {key: value for key, value in row["review"].items()
            if key not in {"schedule_status", "schedule_source", "schedule_text"}}
        await db.execute(update(table).where(table.c.id == "card").values(review=legacy_review))
        await db.commit()
    result = await confirmed(card_db)
    assert result["status"] == "confirmed", result
    assert result["module"] == "module_3"


@pytest.mark.parametrize("changes,review_changes", [
    ({"phase": "discussing"}, {}),
    ({"display_text": None}, {}),
    ({"display_assistant_message_id": None}, {}),
    ({"revision": 6}, {}),
    ({}, {"near_term": False}),
    ({}, {"schedule_status": "past"}),
    ({"concerns": ["待解决安全顾虑"]}, {}),
    ({}, {"manageable": False}),
])
def test_legacy_compatibility_does_not_relax_other_guards(changes, review_changes):
    row = {"kind": "primary", "revision": 5, "phase": "ready", "concerns": [],
        "display_text": "已展示目标卡", "display_assistant_message_id": 102,
        "fields": {"activity_content": "打羽毛球", "schedule_text": SCHEDULE, "difficulty_rating": 0},
        "review": {"revision": 5, "is_pa": True, "near_term": True, "manageable": True}}
    assert review_passes(row)
    row.update(changes)
    row["review"].update(review_changes)
    assert not review_passes(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, [], {}])
async def test_missing_or_invalid_status_requires_review_using_existing_sources(card_db, status):
    result = await review(executor(card_db), schedule_status=status)
    assert result["status"] == "blocked", result
    assert result["reason"] == "schedule_review_required_use_existing_sources"


def test_m2_approved_schedule_policy_reaches_main_and_background_with_old_override():
    old_override = "旧版保存提示：核心行动必须在3天内。"
    segments = build_system_segments("module_2", module_prompt=old_override)
    main_prompt = "\n\n".join(segment.text for segment in segments)
    assert main_prompt.count(M2_SCHEDULE_POLICY) == 1
    assert main_prompt.index(M2_SCHEDULE_POLICY) > main_prompt.index(old_override)
    assert M2_SCHEDULE_POLICY in build_system_prompt("module_2", module_prompt=old_override)
    assert GOAL_CARD_POLICY.count(M2_SCHEDULE_POLICY) == 1
    assert M2_SCHEDULE_POLICY not in build_system_prompt("module_1")
