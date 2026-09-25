"""M2 manual alignment: choice drafts, self-rating sources, trial isolation."""
from types import SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint, MetaData, insert, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database_v2_schema import metadata as schema
from app.goal_contract import difficulty_values, evidence_messages, invalidate_stale_difficulty, save_m2_activity_context
from app.dialogue_confirmation import (advance_from_dialogue, fingerprint, _confirmation_snapshot, confirmation_marker_matches,
                                       render_confirmation_summary, summary_present)
from app.models import ConversationMessage
from app.plan_contract import missing_plan_fields
from app.v2_workflow import create_goal_from_agent_dialogue, persist_record, record_values, runtime_for
from scripts.migrate_m2_manual_0924 import migrate
from test_goal_overview import goal_api
from test_goal_contract_0914 import seed_goal_dialogue, primary_payload


def score_messages():
    return [SimpleNamespace(id=1, position=1, role="user", conversation_id=2, content="这个计划我觉得8分难。"),
            SimpleNamespace(id=2, position=2, role="assistant", conversation_id=2, content="我们按你说的减为5分钟，难度再评一下。"),
            SimpleNamespace(id=3, position=3, role="user", conversation_id=2, content="调整以后4分吧。")]


def scoring():
    return {"difficulty_rating": 4, "difficulty_original": 8,
            "difficulty_evidence": {"rating": {"message_id": 3, "quote": "调整以后4分吧。", "score_text": "4"},
                                    "original": {"message_id": 1, "quote": "这个计划我觉得8分难。", "score_text": "8"}}}


def complete_plan(**changes):
    return {"activity_content": "散步", "schedule_text": "今晚饭后", "potential_barriers": ["忘记"],
            "barrier_coping_plan": [{"barrier": "忘记", "plan": "晚饭后把鞋放门口"}],
            **difficulty_values(scoring(), score_messages()), **changes}


def test_user_ratings_with_sources_and_optional_plan_details():
    values = difficulty_values(scoring(), score_messages())
    assert values["difficulty_rating"] == 4 and values["difficulty_original"] == 8
    assert values["difficulty_evidence"]["rating"]["message_id"] == 3
    assert missing_plan_fields(complete_plan()) == []


def test_optional_fields_do_not_need_empty_labels_in_confirmation_card():
    plan = complete_plan()
    card = render_confirmation_summary("module_2", plan)
    assert card and "地点：" not in card and "时长：" not in card and "频率：" not in card
    assert "执行难度（自评）：4/10" in card
    assert summary_present("module_2", card, plan)


def test_confirmation_anchor_binds_score_and_its_evidence_to_plan_version():
    plan = complete_plan()
    marker = {"module": "module_2", "cycle_id": "c", "assistant_message_id": 8,
              "summary_verified": True, "field_complete": True,
              "fingerprint": fingerprint(plan), "snapshot": _confirmation_snapshot("module_2", plan)}
    assert confirmation_marker_matches("module_2", marker, plan, cycle_id="c", preceding_assistant_id=8)
    changed = {**plan, "difficulty_rating": 3, "difficulty_evidence": {
        "rating": {"value": 3, "message_id": 6, "quote": "现在3分", "score_text": "3"}}}
    assert fingerprint(changed) != fingerprint(plan)
    assert not confirmation_marker_matches("module_2", marker, changed, cycle_id="c", preceding_assistant_id=8)


@pytest.mark.parametrize("value", [None, -1, 6, 10, 11, True, "4"])
def test_final_difficulty_must_be_integer_at_most_five(value):
    assert "difficulty_rating" in missing_plan_fields(complete_plan(difficulty_rating=value))


@pytest.mark.parametrize("case", ["assistant", "invented", "wrong_number", "inferred", "out_of_range"])
def test_model_cannot_supply_or_invent_user_difficulty(case):
    data = scoring()
    if case == "assistant":
        data["difficulty_evidence"]["rating"] = {"message_id": 2, "quote": "减为5分钟", "score_text": "5"}
        data["difficulty_rating"] = 5
    elif case == "invented":
        data["difficulty_evidence"]["rating"]["message_id"] = 999
    elif case == "wrong_number":
        data["difficulty_rating"] = 3
    elif case == "inferred":
        data["difficulty_evidence"]["rating"]["score_text"] = "容易"
    else:
        data["difficulty_rating"] = 11
    assert difficulty_values(data, score_messages())["difficulty_rating"] is None


def test_raw_score_cannot_write_database_without_source_validation():
    assert record_values("module_2", scoring()) == {}


def test_plan_change_invalidates_old_score_without_altering_history():
    old = {**complete_plan(), "record_status": "draft", "duration_minutes": 10}
    messages = score_messages() + [SimpleNamespace(id=4, position=4, role="user", conversation_id=2,
                                                  content="我把时长改为30分钟。")]
    changed = invalidate_stale_difficulty({"duration_minutes": 30}, old, messages)
    assert changed["difficulty_rating"] is None
    assert changed["difficulty_evidence"]["previous_rating"]["value"] == 4
    assert "rating" not in changed["difficulty_evidence"]
    # A partial extraction that leaves the plan unchanged does not revoke a score.
    assert invalidate_stale_difficulty({}, old, messages) == {}
    assert invalidate_stale_difficulty({"duration_minutes": 30}, {**old, "record_status": "confirmed"}, messages) == {"duration_minutes": 30}


def test_plan_change_accepts_a_new_user_score_from_same_turn():
    old = {**complete_plan(), "record_status": "draft", "duration_minutes": 10}
    messages = score_messages() + [SimpleNamespace(id=4, position=4, role="user", conversation_id=2,
                                                  content="时长改为5分钟以后我觉得2分难。")]
    data = {"difficulty_rating": 2, "difficulty_evidence": {"rating": {
        "message_id": 4, "quote": messages[-1].content, "score_text": "2"}}}
    values = {"duration_minutes": 5, **difficulty_values(data, messages)}
    changed = invalidate_stale_difficulty(values, old, messages)
    assert changed["difficulty_rating"] == 2


async def test_post_reply_can_anchor_card_without_committing_confirmation(goal_api):
    from test_extraction_freshness_0916 import seed_m2
    from sqlalchemy import update
    _, db, _ = goal_api
    await seed_m2(db)
    plan_table = schema.tables["module_two_record"]
    pending = (await db.execute(select(plan_table).where(plan_table.c.id == "m2-draft"))).mappings().one()
    await db.execute(insert(ConversationMessage), [
        {"id": 21, "conversation_id": 1, "position": 2, "role": "user", "content": "好的，就按这个计划试试。"},
        {"id": 22, "conversation_id": 1, "position": 3, "role": "assistant", "content": render_confirmation_summary("module_2", pending)}])
    _, state = await runtime_for(db, "chat-a")
    memory = {**state["memory"], "module_extraction_freshness": {"module_2": {
        "assistant_message_id": 22, "cycle_id": "m2-cycle"}}}
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(memory=memory))
    assert await advance_from_dialogue(db, session_id="chat-a", user_id="a", assistant_message_id=22,
                                       allow_transition=False) is None
    _, state = await runtime_for(db, "chat-a")
    assert state["current_module"] == "module_2"
    assert state["memory"]["dialogue_draft"]["assistant_message_id"] == 22
    assert state["memory"]["dialogue_draft"]["summary_verified"]
    assert (await db.execute(select(plan_table.c.record_status).where(plan_table.c.id == "m2-draft"))).scalar_one() == "draft"


async def test_changed_draft_preserves_selection_but_requires_current_plan_score(goal_api):
    from test_extraction_freshness_0916 import seed_m2
    _, db, _ = goal_api
    await seed_m2(db)
    await db.execute(insert(ConversationMessage), [
        {"id": 21, "conversation_id": 1, "position": 2, "role": "user", "content": "把散步时长改成20分钟。"},
        {"id": 22, "conversation_id": 1, "position": 3, "role": "assistant", "content": "你希望把时长调整为20分钟。"}])
    await db.commit()
    await persist_record(async_sessionmaker(db.bind, expire_on_commit=False), module="module_2", user_id="a",
        cycle_id="m2-cycle", data={"target_activity_duration_minutes": 20,
            "_source_session_id": "chat-a", "_source_assistant_message_id": 22})
    plans = schema.tables["module_two_record"]
    plan = (await db.execute(select(plans).where(plans.c.id == "m2-draft"))).mappings().one()
    assert plan["duration_minutes"] == 20 and plan["difficulty_rating"] is None
    assert plan["difficulty_evidence"]["previous_rating"]["value"] == 4
    progress = schema.tables["pa_cycle_progress"]
    steps = (await db.execute(select(progress.c.module_2_steps).where(progress.c.cycle_id == "m2-cycle"))).scalar_one()
    assert "activity_selected" in steps and "pa_card_completed" not in steps


async def test_legacy_candidate_retries_existing_messages_without_reasking_user(goal_api):
    _, db, _ = goal_api
    latest = await seed_goal_dialogue(db)
    payload = primary_payload()
    payload["goal_proposal"].pop("selection_status")
    diagnostics = {}
    assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=[], assistant_message_id=latest, diagnostics=diagnostics) is None
    assert diagnostics["goal_creation"]["reason_code"] == "selection_extraction_missing"
    # Same stored transcript and same assistant boundary; only extraction is
    # repaired. No user message or complete plan is required.
    payload = {"target_activity_content": "散步", "goal_proposal": {
        "selection_status": "selected", "selection_role": "core", "goal_kind": "primary",
        "selection_message_id": 102, "selection_quote": "我选择每天晚饭后散步",
        "activity_quote": "散步", "long_term_direction": None}}
    created = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=[], assistant_message_id=latest)
    assert created
    plan = (await db.execute(select(schema.tables["module_two_record"]).where(
        schema.tables["module_two_record"].c.id == created["plan_id"]))).mappings().one()
    assert plan["record_status"] == "draft"
    assert plan["schedule_text"] is None and plan["difficulty_rating"] is None
    details = (await db.execute(select(schema.tables["pa_goal_details"]).where(
        schema.tables["pa_goal_details"].c.goal_id == created["goal_id"]))).mappings().one()
    assert details["long_term_direction"] is None
    assert details["source_message_id"] == 102


@pytest.mark.parametrize("role", ["trial", "secondary"])
async def test_trial_and_secondary_candidates_never_create_a_core_goal(goal_api, role):
    _, db, _ = goal_api
    latest = await seed_goal_dialogue(db)
    data = primary_payload()
    data["goal_proposal"]["selection_role"] = role
    assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=data,
        completed_steps=["activity_selected"], assistant_message_id=latest) is None


async def test_trial_context_is_sourced_idempotent_and_does_not_advance(goal_api):
    _, db, _ = goal_api
    await seed_goal_dialogue(db)
    conversation, state = await runtime_for(db, "new-chat-a")
    messages = await evidence_messages(db, conversation.id, "a")
    raw = {"trial": {"state": "trial_active", "source_role": "trial", "activity_content": "散步",
                     "message_id": 102, "quote": "我选择每天晚饭后散步"},
           "secondary_activities": [{"activity_content": "散步", "message_id": 102,
                                      "quote": "我选择每天晚饭后散步", "location": "捏造地点"}]}
    for _ in range(2):
        result = await save_m2_activity_context(db, conversation=conversation, state=state, raw=raw, messages=messages)
        _, state = await runtime_for(db, "new-chat-a")
    assert result["trial"]["state"] == "trial_active"
    assert len(result["secondary_activities"]) == 1
    assert "location" not in result["secondary_activities"][0]
    assert state["active_goal_id"] is None and state["current_module"] == "module_2"
    assert "experience_count" not in result["trial"]
    raw["trial"]["message_id"] = 103  # assistant must not supply the decision
    raw["secondary_activities"] = []
    assert await save_m2_activity_context(db, conversation=conversation, state=state, raw=raw, messages=messages) is None
    await db.execute(insert(ConversationMessage), {"id": 104, "conversation_id": 2, "position": 4,
        "role": "user", "content": "暂时不做临时体验了。"})
    messages = await evidence_messages(db, conversation.id, "a")
    cleared = await save_m2_activity_context(db, conversation=conversation, state=state,
        raw={"trial": {"state": "none", "message_id": 104, "quote": "暂时不做临时体验了。"}}, messages=messages)
    assert cleared["trial"]["state"] == "none"
    assert "activity_content" not in cleared["trial"]


async def test_migration_is_read_only_then_idempotent_and_preserves_existing_rows(tmp_path):
    path = str(tmp_path / "m2.db")
    url = "sqlite+aiosqlite:///" + path
    engine = create_async_engine(url)
    private = MetaData()
    for table in schema.sorted_tables:
        table.to_metadata(private)
    details = private.tables["pa_goal_details"]
    details.append_constraint(CheckConstraint("goal_kind != 'primary' OR long_term_direction IS NOT NULL"))
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE pa_goals (id VARCHAR(36) PRIMARY KEY)"))
        await connection.execute(text("CREATE TABLE module_two_record (id VARCHAR(36) PRIMARY KEY, record_status VARCHAR(24))"))
        await connection.run_sync(lambda c: details.create(c))
        await connection.execute(text("INSERT INTO pa_goals (id) VALUES ('old')"))
        await connection.execute(text("INSERT INTO module_two_record (id, record_status) VALUES ('plan-old', 'confirmed')"))
        await connection.execute(insert(details), {"goal_id": "old", "goal_kind": "primary", "long_term_direction": "保持精力"})
    await migrate(url, expected_database=path)
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda c: {v["name"] for v in inspect(c).get_columns("module_two_record")})
        assert "difficulty_rating" not in names
    await migrate(url, expected_database=path, apply=True)
    await migrate(url, expected_database=path, apply=True)
    async with engine.begin() as connection:
        old = (await connection.execute(text("SELECT record_status, difficulty_rating FROM module_two_record WHERE id='plan-old'"))).one()
        assert tuple(old) == ("confirmed", None)
        assert (await connection.execute(text("SELECT long_term_direction FROM pa_goal_details WHERE goal_id='old'"))).scalar_one() == "保持精力"
        await connection.execute(text("UPDATE pa_goal_details SET long_term_direction=NULL WHERE goal_id='old'"))
    await engine.dispose()
