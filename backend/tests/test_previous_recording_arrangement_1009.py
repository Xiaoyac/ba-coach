"""Historical recording context is sourced data, never new-cycle consent."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database_v2_schema import metadata
from app.knowledge_context import read_previous_recording_arrangement, read_recording_state
from app.m3_contract import VERSION
from app.models import AccountSettings, UserAccount, Conversation, ConversationMessage


AT = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)
PLAN = "每天在每日记录里填写整体感受。"
REFUSAL = "活动记录我不想填，但每天整体感受会继续记。"
RUNTIME = {"active_goal_id": "new-goal", "active_cycle_id": "new-cycle"}


def evidence(status="declined", *, cycle="old-cycle", session="old-chat", message=12):
    return {"version": VERSION, "verified": True, "recording_status": status,
        "session_id": session, "cycle_id": cycle,
        "evidence": {
            "decision": {"message_id": message, "role": "user", "status": status,
                "scope": "activity_record" if status == "declined" else "current_arrangement",
                "quote": REFUSAL if status == "declined" else "好，就按这个方式记录。"},
            "plan": {"message_id": 11, "role": "assistant", "quote": PLAN}}}


@pytest_asyncio.fixture
async def records():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.run_sync(UserAccount.__table__.create)
        await connection.run_sync(AccountSettings.__table__.create)
        await connection.run_sync(Conversation.__table__.create)
        await connection.run_sync(ConversationMessage.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await db.execute(insert(metadata.tables["user_profile"]), [{"uuid": "a"}, {"uuid": "b"}])
        await db.execute(insert(UserAccount), [
            {"id": 1, "username": "membera", "password_hash": "test-only", "profile_uuid": "a"},
            {"id": 2, "username": "memberb", "password_hash": "test-only", "profile_uuid": "b"}])
        await db.execute(insert(AccountSettings), [
            {"account_id": 1, "role": "user"}, {"account_id": 2, "role": "user"}])
        await db.execute(insert(Conversation), [
            {"id": 1, "subject_id": "a", "session_id": "old-chat", "title": "旧聊天"},
            {"id": 2, "subject_id": "a", "session_id": "new-chat", "title": "新聊天"},
            {"id": 3, "subject_id": "b", "session_id": "foreign-chat", "title": "别人的聊天"}])
        await db.execute(insert(ConversationMessage), [
            {"id": 11, "conversation_id": 1, "position": 1, "role": "assistant", "content": PLAN,
             "created_at": AT - timedelta(days=2, minutes=1)},
            {"id": 12, "conversation_id": 1, "position": 2, "role": "user", "content": REFUSAL,
             "created_at": AT - timedelta(days=2)}])
        await db.execute(insert(metadata.tables["pa_goals"]), [
            {"id": "old-goal", "user_id": "a", "title": "旧散步目标", "created_from_conversation_id": 1},
            {"id": "new-goal", "user_id": "a", "title": "新拉伸目标", "created_from_conversation_id": 2},
            {"id": "foreign-goal", "user_id": "b", "title": "别人的目标", "created_from_conversation_id": 3}])
        await db.execute(insert(metadata.tables["module_two_record"]), [
            {"id": "old-plan", "goal_id": "old-goal", "version_no": 1,
             "timezone": "Asia/Shanghai", "activity_content": "散步"},
            {"id": "new-plan", "goal_id": "new-goal", "version_no": 1,
             "timezone": "Asia/Shanghai", "activity_content": "拉伸"}])
        await db.execute(insert(metadata.tables["module_three_record"]), {
            "id": "old-record", "goal_id": "old-goal", "module_two_record_id": "old-plan",
            "version_no": 1, "record_status": "confirmed", "confirmation_message_id": 12,
            "recording_status": "declined", "recording_evidence": evidence()})
        await db.execute(insert(metadata.tables["pa_cycles"]), [
            {"id": "old-cycle", "goal_id": "old-goal", "ordinal": 1,
             "module_two_record_id": "old-plan", "module_three_record_id": "old-record"},
            {"id": "new-cycle", "goal_id": "new-goal", "ordinal": 1,
             "module_two_record_id": "new-plan", "module_three_record_id": None}])
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_goal_and_chat_refusal_is_history_not_current_confirmation(records):
    async with records() as db:
        cycles = metadata.tables["pa_cycles"]
        before = [dict(row) for row in (await db.execute(select(cycles))).mappings()]
        current = await read_recording_state(db, "a", RUNTIME)
        assert current == {"recording_status": "unknown", "recording_decision_scope": None}
        previous = await read_previous_recording_arrangement(db, "a", RUNTIME)
        assert previous == {"scope": "historical", "applies_to_current_cycle": False,
            "record_id": "old-record", "goal_id": "old-goal", "cycle_id": "old-cycle",
            "plan_id": "old-plan", "activity_content": "散步", "recording_status": "declined",
            "recording_decision_scope": "activity_record", "decision_quote": REFUSAL,
            "recording_plan": None, "source_message_id": 12,
            "source_created_at": (AT - timedelta(days=2)).isoformat()}
        assert await read_recording_state(db, "a", RUNTIME) == current
        assert [dict(row) for row in (await db.execute(select(cycles))).mappings()] == before


@pytest.mark.asyncio
async def test_same_goal_new_cycle_and_plan_retain_only_historical_attitude(records):
    async with records() as db:
        plans, cycles = metadata.tables["module_two_record"], metadata.tables["pa_cycles"]
        await db.execute(update(plans).where(plans.c.id == "new-plan").values(
            goal_id="old-goal", version_no=2))
        await db.execute(update(cycles).where(cycles.c.id == "new-cycle").values(
            goal_id="old-goal", ordinal=2))
        runtime = {**RUNTIME, "active_goal_id": "old-goal"}
        previous = await read_previous_recording_arrangement(db, "a", runtime)
        assert previous["plan_id"] == "old-plan" and previous["cycle_id"] == "old-cycle"
        assert (await read_recording_state(db, "a", runtime))["recording_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["draft", "missing_source", "assistant_source", "foreign_source",
    "wrong_quote", "wrong_source_id", "wrong_scope", "unverified", "wrong_status", "wrong_session",
    "wrong_cycle", "wrong_cycle_goal", "wrong_cycle_plan", "wrong_plan_goal", "unbound"])
async def test_invalid_historical_evidence_never_becomes_context(records, corruption):
    async with records() as db:
        table, cycles, plans = (metadata.tables[n] for n in
                               ("module_three_record", "pa_cycles", "module_two_record"))
        contract = deepcopy(evidence())
        change = None
        if corruption == "draft": change = {"record_status": "draft"}
        elif corruption == "missing_source": change = {"confirmation_message_id": 999}
        elif corruption == "assistant_source":
            await db.execute(update(ConversationMessage).where(ConversationMessage.id == 12).values(role="assistant"))
        elif corruption == "foreign_source":
            await db.execute(update(ConversationMessage).where(ConversationMessage.id == 12).values(conversation_id=3))
        elif corruption == "wrong_quote": contract["evidence"]["decision"]["quote"] = "并未说过的话"
        elif corruption == "wrong_source_id": contract["evidence"]["decision"]["message_id"] = 11
        elif corruption == "wrong_scope": contract["evidence"]["decision"]["scope"] = "everything"
        elif corruption == "unverified": contract["verified"] = False
        elif corruption == "wrong_status": contract["recording_status"] = "accepted"
        elif corruption == "wrong_session": contract["session_id"] = "new-chat"
        elif corruption == "wrong_cycle": contract["cycle_id"] = "new-cycle"
        elif corruption == "wrong_cycle_goal":
            await db.execute(update(cycles).where(cycles.c.id == "old-cycle").values(goal_id="foreign-goal"))
        elif corruption == "wrong_cycle_plan":
            await db.execute(update(cycles).where(cycles.c.id == "old-cycle").values(module_two_record_id="new-plan"))
        elif corruption == "wrong_plan_goal":
            await db.execute(update(plans).where(plans.c.id == "old-plan").values(goal_id="foreign-goal"))
        elif corruption == "unbound":
            await db.execute(update(cycles).where(cycles.c.id == "old-cycle").values(module_three_record_id=None))
        await db.execute(update(table).where(table.c.id == "old-record").values(
            recording_evidence=contract, **(change or {})))
        assert await read_previous_recording_arrangement(db, "a", RUNTIME) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,runtime", [
    ("b", RUNTIME), ("a", {**RUNTIME, "active_goal_id": "old-goal"}),
    ("a", {**RUNTIME, "active_cycle_id": None}), ("a", {**RUNTIME, "active_goal_id": None})])
async def test_current_scope_must_be_owned_and_consistent(records, user_id, runtime):
    async with records() as db:
        assert await read_previous_recording_arrangement(db, user_id, runtime) is None


@pytest.mark.asyncio
async def test_current_binding_is_not_duplicated_as_previous_history(records):
    async with records() as db:
        cycles = metadata.tables["pa_cycles"]
        await db.execute(update(cycles).where(cycles.c.id == "new-cycle").values(
            goal_id="old-goal", ordinal=2, module_two_record_id="old-plan", module_three_record_id="old-record"))
        runtime = {**RUNTIME, "active_goal_id": "old-goal"}
        assert await read_previous_recording_arrangement(db, "a", runtime) is None
        assert (await read_recording_state(db, "a", runtime))["recording_status"] == "declined"


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_problem", [None, "missing", "foreign", "later", "wrong_text"])
async def test_accepted_plan_requires_its_own_real_prior_source(records, plan_problem):
    async with records() as db:
        table = metadata.tables["module_three_record"]
        contract = evidence("accepted")
        await db.execute(update(ConversationMessage).where(ConversationMessage.id == 12).values(
            content="好，就按这个方式记录。"))
        if plan_problem == "missing": contract["evidence"]["plan"]["message_id"] = 999
        elif plan_problem == "foreign":
            await db.execute(update(ConversationMessage).where(ConversationMessage.id == 11).values(conversation_id=3))
        elif plan_problem == "later":
            await db.execute(update(ConversationMessage).where(ConversationMessage.id == 11).values(position=3))
        await db.execute(update(table).where(table.c.id == "old-record").values(
            recording_status="accepted", recording_evidence=contract,
            negotiated_record_plan={"text": "与证据不一致" if plan_problem == "wrong_text" else PLAN}))
        previous = await read_previous_recording_arrangement(db, "a", RUNTIME)
        assert previous["recording_status"] == "accepted"
        assert previous["recording_plan"] == (None if plan_problem else PLAN)
        assert not previous["applies_to_current_cycle"]


@pytest.mark.asyncio
async def test_selects_latest_bound_decision_not_record_creation_or_unbound_version(records):
    async with records() as db:
        table, cycles = metadata.tables["module_three_record"], metadata.tables["pa_cycles"]
        await db.execute(insert(ConversationMessage), {
            "id": 14, "conversation_id": 1, "position": 4, "role": "user",
            "content": "好，就按这个方式记录。", "created_at": AT - timedelta(days=1)})
        await db.execute(insert(table), {
            "id": "later-record", "goal_id": "old-goal", "module_two_record_id": "old-plan",
            "version_no": 2, "record_status": "confirmed", "confirmation_message_id": 14,
            "recording_status": "accepted", "recording_evidence": evidence("accepted", message=14),
            "negotiated_record_plan": {"text": PLAN}})
        # An unbound version cannot replace the old cycle's authoritative choice.
        assert (await read_previous_recording_arrangement(db, "a", RUNTIME))["record_id"] == "old-record"
        await db.execute(update(cycles).where(cycles.c.id == "old-cycle").values(module_three_record_id="later-record"))
        previous = await read_previous_recording_arrangement(db, "a", RUNTIME)
        assert previous["record_id"] == "later-record" and previous["source_message_id"] == 14
        assert previous["recording_status"] == "accepted"


@pytest.mark.asyncio
async def test_multiple_past_cycles_choose_latest_real_user_decision(records):
    async with records() as db:
        table, cycles = metadata.tables["module_three_record"], metadata.tables["pa_cycles"]
        await db.execute(insert(ConversationMessage), {
            "id": 14, "conversation_id": 1, "position": 4, "role": "user",
            "content": "好，就按这个方式记录。", "created_at": AT - timedelta(days=1)})
        await db.execute(insert(table), {
            "id": "second-cycle-record", "goal_id": "old-goal", "module_two_record_id": "old-plan",
            "version_no": 2, "record_status": "confirmed", "confirmation_message_id": 14,
            "recording_status": "accepted", "recording_evidence": evidence(
                "accepted", cycle="second-old-cycle", message=14),
            "negotiated_record_plan": {"text": PLAN}, "created_at": AT - timedelta(days=10)})
        await db.execute(insert(cycles), {
            "id": "second-old-cycle", "goal_id": "old-goal", "ordinal": 2,
            "module_two_record_id": "old-plan", "module_three_record_id": "second-cycle-record"})
        previous = await read_previous_recording_arrangement(db, "a", RUNTIME)
        assert previous["source_message_id"] == 14 and previous["cycle_id"] == "second-old-cycle"
        # A later database snapshot cannot make a fabricated quote authoritative.
        bad = evidence("accepted", cycle="second-old-cycle", message=14)
        bad["evidence"]["decision"]["quote"] = "用户从未同意过的安排"
        await db.execute(update(table).where(table.c.id == "second-cycle-record").values(recording_evidence=bad))
        assert (await read_previous_recording_arrangement(db, "a", RUNTIME))["source_message_id"] == 12


@pytest.mark.asyncio
async def test_member_history_remains_available_across_owned_conversations(records):
    async with records() as db:
        previous = await read_previous_recording_arrangement(db, "a", RUNTIME, conversation_id=2)
        assert previous["decision_quote"] == REFUSAL
        assert await read_previous_recording_arrangement(db, "a", RUNTIME, conversation_id=3) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("conversation_id", [None, 1, 2, 3, 999])
async def test_admin_cannot_borrow_history_or_current_scope_from_other_chat(records, conversation_id):
    async with records() as db:
        await db.execute(update(AccountSettings).where(AccountSettings.account_id == 1).values(role="admin"))
        assert await read_previous_recording_arrangement(
            db, "a", RUNTIME, conversation_id=conversation_id) is None


@pytest.mark.asyncio
async def test_admin_same_chat_past_cycle_is_available_but_foreign_source_is_not(records):
    async with records() as db:
        goals = metadata.tables["pa_goals"]
        await db.execute(update(AccountSettings).where(AccountSettings.account_id == 1).values(role="admin"))
        await db.execute(update(goals).where(goals.c.id == "old-goal").values(created_from_conversation_id=2))
        # A goal with the right owner-chat cannot borrow a decision from another chat.
        assert await read_previous_recording_arrangement(db, "a", RUNTIME, conversation_id=2) is None
        await db.execute(update(ConversationMessage).where(ConversationMessage.id.in_([11, 12])).values(
            conversation_id=2))
        table = metadata.tables["module_three_record"]
        await db.execute(update(table).where(table.c.id == "old-record").values(
            recording_evidence=evidence(session="new-chat")))
        previous = await read_previous_recording_arrangement(db, "a", RUNTIME, conversation_id=2)
        assert previous["decision_quote"] == REFUSAL and not previous["applies_to_current_cycle"]
