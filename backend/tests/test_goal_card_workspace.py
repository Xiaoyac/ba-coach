"""M2 UI drafts remain source-bound and never confirm a clinical plan."""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select, update

from app.database_v2_schema import metadata as schema
from app.goal_card_workspace import (bind_submission, latest_card, open_card,
    pause_card, read_card_row, record_card_update, sync_core_card)
from app.identity import require_subject_id
from app.models import Conversation, ConversationMessage
from app.session import get_session_store
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api  # noqa: F401


@pytest.fixture(autouse=True)
def enable_card_feature(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(goal_card_ui_enabled=True, database_schema_version="v2"))


async def setup_card(db, *, kind="primary", session="chat-a", user="a"):
    conversation, state = await runtime_for(db, session)
    await db.execute(update(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == conversation.id).values(current_module="module_2"))
    source = ConversationMessage(conversation_id=conversation.id, position=0, role="user", content="我愿意试试身体活动。")
    db.add(source); await db.flush()
    conversation, state = await runtime_for(db, session)
    card = await open_card(db, conversation, state, kind, source.id)
    await db.commit()
    return conversation, state, source, card


async def test_m2_alone_does_not_open_card_and_stage_open_is_idempotent(goal_api):
    client, db, _ = goal_api
    await db.execute(update(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == 1).values(current_module="module_2"))
    assert (await client.get("/api/program/chat-a/goal-card")).json() == {"enabled": True, "card": None}
    conversation, state, source, card = await setup_card(db)
    assert await open_card(db, conversation, state, "primary", source.id) == card
    assert (await client.get("/api/program/chat-a/goal-card")).json()["card"] == card
    assert await db.scalar(select(func.count()).select_from(schema.tables["goal_card_workspaces"])) == 1


@pytest.mark.parametrize("sandbox", [True, "true"])
async def test_owned_sandbox_reads_disable_panel_but_writes_stay_blocked(goal_api, sandbox):
    client, db, _ = goal_api
    _, _, _, card = await setup_card(db)
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(update(rt).where(rt.c.conversation_id.in_([1, 3])).values(memory={"sandbox_mode": sandbox}))
    await db.commit()
    assert (await client.get("/api/program/chat-a/goal-card")).json() == {"enabled": False, "card": None}
    assert (await client.get("/api/program/chat-b/goal-card")).status_code == 404
    result = await client.put("/api/program/chat-a/goal-card", json={"card_id": card["id"], "revision": 1, "fields": {}})
    assert result.status_code == 409


async def test_partial_empty_and_zero_fields_preserved_without_business_write(goal_api):
    client, db, _ = goal_api
    _, before, _, card = await setup_card(db)
    payload = {"card_id": card["id"], "revision": card["revision"], "fields": {
        "activity_content": "", "difficulty_rating": 0, "duration_minutes": None,
        "schedule_text": "", "potential_barriers": ""}}
    response = await client.put("/api/program/chat-a/goal-card", json=payload)
    assert response.status_code == 200, response.text
    result = response.json(); new = result["card"]
    assert new["revision"] == 2 and new["phase"] == "discussing"
    assert new["fields"]["difficulty_rating"] == 0 and new["fields"]["duration_minutes"] is None
    assert new["fields"]["activity_content"] == "" and "执行难度（0–10）：0" in result["submission_text"]
    assert "不是最终确认" in result["submission_text"]
    assert card["id"] not in result["submission_text"]
    assert await db.scalar(select(func.count()).select_from(schema.tables["module_two_record"])) == 0
    assert await db.scalar(select(func.count()).select_from(schema.tables["pa_cycles"])) == 0
    assert await db.scalar(select(func.count()).select_from(schema.tables["pa_goals"])) == 3
    _, after = await runtime_for(db, "chat-a")
    assert all(after[k] == before[k] for k in ("current_module", "active_cycle_id", "active_goal_id", "row_version"))
    logs = (await db.execute(select(schema.tables["ai_decision_logs"].c.decision_value).where(
        schema.tables["ai_decision_logs"].c.decision_type == "goal_card_revision").order_by(schema.tables["ai_decision_logs"].c.id))).scalars().all()
    assert [x["revision"] for x in logs] == [1, 2] and logs[0]["fields"] == {}
    assert logs[1]["fields"]["difficulty_rating"] == 0


async def test_ownership_auth_feature_and_stale_version_guards(goal_api, monkeypatch):
    client, db, app = goal_api
    _, _, _, card = await setup_card(db)
    body = {"card_id": card["id"], "revision": 1, "fields": {"activity_content": "散步"}}
    assert (await client.put("/api/program/chat-b/goal-card", json=body)).status_code == 404
    assert (await client.get("/api/program/chat-b/goal-card")).status_code == 404
    assert (await client.put("/api/program/new-chat-a/goal-card", json=body)).status_code == 409
    assert (await client.put("/api/program/chat-a/goal-card", json=body)).status_code == 200
    assert (await client.put("/api/program/chat-a/goal-card", json=body)).status_code == 409
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(goal_card_ui_enabled=False))
    assert (await client.get("/api/program/chat-a/goal-card")).json() == {"enabled": False, "card": None}
    assert (await client.put("/api/program/chat-a/goal-card", json=body)).status_code == 409
    del app.dependency_overrides[require_subject_id]
    assert (await client.get("/api/program/chat-a/goal-card")).status_code == 401


@pytest.mark.parametrize("fields", [{"difficulty_rating": True}, {"difficulty_rating": 11},
    {"duration_minutes": -1}, {"record_status": "confirmed"}, {"activity_content": "x" * 256}])
async def test_invalid_types_and_privileged_fields_rejected(goal_api, fields):
    client, db, _ = goal_api
    _, _, _, card = await setup_card(db)
    response = await client.put("/api/program/chat-a/goal-card", json={"card_id": card["id"], "revision": 1, "fields": fields})
    assert response.status_code == 422


async def test_edit_invalidates_review_and_cannot_race_live_generation(goal_api):
    client, db, app = goal_api
    _, _, _, card = await setup_card(db)
    row = await read_card_row(db, 1, "a")
    row = await record_card_update(db, row, {"phase": "ready", "review": {"revision": 2, "is_pa": True},
        "display_text": "旧卡", "display_user_message_id": 123, "display_assistant_message_id": 124}, reason="test_review")
    await db.commit()
    body = {"card_id": card["id"], "revision": 2, "fields": {"activity_content": "改为骑车"}}
    store = app.dependency_overrides[get_session_store]()
    lock = await store.get_turn_lock("chat-a")
    async with lock:
        assert (await client.put("/api/program/chat-a/goal-card", json=body)).status_code == 409
    response = await client.put("/api/program/chat-a/goal-card", json=body)
    assert response.status_code == 200, response.text
    row = await read_card_row(db, 1, "a")
    assert row["phase"] == "discussing" and row["review"] is None
    assert row["display_text"] is None and row["display_assistant_message_id"] is None


async def test_submission_binds_only_exact_current_owned_user_text(goal_api):
    client, db, _ = goal_api
    conversation, _, _, card = await setup_card(db)
    body = {"card_id": card["id"], "revision": 1, "fields": {"activity_content": "散步"}}
    result = (await client.put("/api/program/chat-a/goal-card", json=body)).json()
    other = ConversationMessage(conversation_id=1, position=1, role="user", content=result["submission_text"] + "还有别的")
    db.add(other); await db.flush()
    assert await bind_submission(db, conversation, other.id) is None
    exact = ConversationMessage(conversation_id=1, position=2, role="user", content=result["submission_text"])
    db.add(exact); await db.flush()
    assert (await bind_submission(db, conversation, exact.id))["id"] == card["id"]
    assert (await read_card_row(db, 1, "a"))["submission_message_id"] == exact.id
    # Editing makes the old canonical text unusable even if pasted again.
    body["revision"] = 2; body["fields"]["activity_content"] = "骑车"
    assert (await client.put("/api/program/chat-a/goal-card", json=body)).status_code == 200
    repeated = ConversationMessage(conversation_id=1, position=3, role="user", content=result["submission_text"])
    db.add(repeated); await db.flush()
    assert await bind_submission(db, conversation, repeated.id) is None


async def test_pause_retained_on_get_and_form_cannot_restart_it(goal_api):
    client, db, _ = goal_api
    conversation, state, source, card = await setup_card(db)
    paused = await pause_card(db, conversation, state, source.id); await db.commit()
    assert paused["phase"] == "paused"
    assert (await client.get("/api/program/chat-a/goal-card")).json()["card"]["phase"] == "paused"
    response = await client.put("/api/program/chat-a/goal-card", json={"card_id": card["id"], "revision": paused["revision"], "fields": {}})
    assert response.status_code == 409


async def test_secondary_archive_owned_and_no_goal_or_cycle_created(goal_api):
    client, db, _ = goal_api
    _, _, _, card = await setup_card(db, kind="secondary")
    row = await read_card_row(db, 1, "a")
    await record_card_update(db, row, {"fields": {"activity_content": "周末骑车"}, "phase": "confirmed"}, reason="test_confirm")
    await db.commit()
    result = (await client.get("/api/program/goals/overview")).json()
    assert len(result["formulation_cards"]) == 1 and result["formulation_cards"][0]["id"] == card["id"]
    assert len(result["goals"]) == 2 and not result["activity_records"]
    assert await db.scalar(select(func.count()).select_from(schema.tables["pa_cycles"])) == 0


async def test_sync_core_preserves_user_fields_but_invalidates_old_review(goal_api):
    client, db, _ = goal_api
    conversation, state, _, card = await setup_card(db)
    result = (await client.put("/api/program/chat-a/goal-card", json={"card_id": card["id"], "revision": 1,
        "fields": {"activity_content": "散步", "location": "家旁边", "difficulty_rating": 0}})).json()
    plans = schema.tables["module_two_record"]
    await db.execute(insert(plans), {"id": "draft-plan", "goal_id": "g1", "version_no": 1, "timezone": "Asia/Shanghai",
        "activity_content": "散步", "schedule_text": "明天下午"})
    record = dict((await db.execute(select(plans).where(plans.c.id == "draft-plan"))).mappings().one())
    state = {**state, "active_goal_id": "g1"}
    current = await sync_core_card(db, conversation, state, "discussing", record)
    assert current["fields"]["location"] == "家旁边" and current["fields"]["difficulty_rating"] == 0
    assert current["plan_id"] == "draft-plan" and current["fields"]["schedule_text"] == "明天下午"
    with pytest.raises(ValueError, match="plan_not_confirmed"):
        await sync_core_card(db, conversation, state, "confirmed", record)
    # A caller's dictionary is not authority over the stored plan's status.
    with pytest.raises(ValueError, match="plan_not_confirmed"):
        await sync_core_card(db, conversation, state, "confirmed", {
            **record, "record_status": "confirmed", "confirmation_status": "confirmed", "confirmation_message_id": 1})


async def test_migration_is_explicit_additive_and_idempotent(tmp_path):
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine
    from scripts.migrate_goal_cards_1003 import migrate
    path = str(tmp_path / "migration.sqlite")
    url = "sqlite+aiosqlite:///" + path
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: schema.tables["user_profile"].create(c))
    async def table_names():
        async with engine.connect() as connection:
            return set(await connection.run_sync(lambda c: inspect(c).get_table_names()))
    await migrate(url, path)
    assert await table_names() == {"user_profile"}
    await migrate(url, path, True)
    assert await table_names() == {"user_profile", "goal_card_workspaces"}
    await migrate(url, path, True)
    assert await table_names() == {"user_profile", "goal_card_workspaces"}
    with pytest.raises(ValueError, match="does not match"):
        await migrate(url, "wrong-database", True)
    await engine.dispose()


async def test_chat_deletion_removes_drafts_retains_only_secondary_archive_when_disabled(goal_api, monkeypatch):
    from app.v2_deletion import detach_conversation
    client, db, _ = goal_api
    conversation, state, source, _ = await setup_card(db)
    secondary = await open_card(db, conversation, state, "secondary", source.id)
    row = await read_card_row(db, 1, "a", secondary["id"])
    await record_card_update(db, row, {"phase": "confirmed", "fields": {"activity_content": "周末骑车"}}, reason="test_confirm")
    await db.commit()
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(goal_card_ui_enabled=False, database_schema_version="v2"))
    await detach_conversation(db, conversation); await db.commit()
    cards = (await db.execute(select(schema.tables["goal_card_workspaces"]))).mappings().all()
    assert len(cards) == 1 and cards[0]["id"] == secondary["id"]
    assert cards[0]["conversation_id"] is None and cards[0]["phase"] == "confirmed"
    assert cards[0]["fields"]["activity_content"] == "周末骑车"
    assert cards[0]["submission_text"] is None and cards[0]["review"] is None
    assert await latest_card(db, 1, "a") is None
    from app.goal_card_workspace import confirmed_secondary_cards
    assert len(await confirmed_secondary_cards(db, "a")) == 1
    assert await confirmed_secondary_cards(db, "b") == []


async def test_account_purge_clears_all_cards_even_feature_disabled(goal_api, monkeypatch):
    from app.routes import admin_accounts
    from app.models_business import InteractionStatus, RiskMonitoring
    client, db, _ = goal_api
    for model in (InteractionStatus, RiskMonitoring):
        await db.run_sync(lambda session: model.__table__.create(session.connection(), checkfirst=True))
    await db.run_sync(lambda session: admin_accounts.push_schema.create_all(session.connection()))
    await setup_card(db)
    await setup_card(db, kind="secondary", session="chat-b", user="b")
    monkeypatch.setattr(admin_accounts, "get_settings", lambda: SimpleNamespace(database_schema_version="v2"))
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(goal_card_ui_enabled=False, database_schema_version="v2"))
    await admin_accounts._purge_account_business_data(db, profile_uuid="a")
    await db.commit()
    owners = (await db.execute(select(schema.tables["goal_card_workspaces"].c.user_id))).scalars().all()
    assert owners == ["b"]


async def test_deletion_before_workspace_migration_does_not_fail(goal_api):
    from app.v2_deletion import detach_conversation
    from app.goal_card_workspace import delete_user_cards
    _, db, _ = goal_api
    await db.run_sync(lambda session: schema.tables["goal_card_workspaces"].drop(session.connection()))
    await delete_user_cards(db, "a")
    await detach_conversation(db, await db.get(Conversation, 1)); await db.commit()
    assert await db.get(Conversation, 1) is None


async def test_selecting_existing_primary_after_secondary_keeps_version_and_makes_it_current(goal_api):
    _, db, _ = goal_api
    conversation, state, source, primary = await setup_card(db)
    secondary = await open_card(db, conversation, state, "secondary", source.id)
    assert (await latest_card(db, 1, "a"))["id"] == secondary["id"]
    selected = await open_card(db, conversation, state, "primary", source.id)
    assert selected == primary
    assert (await latest_card(db, 1, "a"))["id"] == primary["id"]
    logs = schema.tables["ai_decision_logs"]
    assert await db.scalar(select(logs.c.reason_summary).order_by(logs.c.id.desc()).limit(1)) == "card_reselected"


@pytest.mark.parametrize("record_status", ["draft", "confirmed"])
async def test_open_prefills_only_current_unconfirmed_plan(goal_api, record_status):
    _, db, _ = goal_api
    await db.execute(insert(schema.tables["module_two_record"]), {"id": "prefill", "goal_id": "g1",
        "version_no": 1, "timezone": "Asia/Shanghai", "activity_content": "散步", "schedule_text": "明天傍晚",
        "difficulty_rating": 0, "record_status": record_status,
        "confirmation_status": "confirmed" if record_status == "confirmed" else "unconfirmed",
        "confirmation_message_id": 77 if record_status == "confirmed" else None})
    await db.execute(insert(schema.tables["pa_cycles"]), {"id": "prefill-cycle", "goal_id": "g1", "ordinal": 1,
        "status": "planning", "module_two_record_id": "prefill" if record_status == "confirmed" else None})
    await db.execute(update(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == 1).values(
            active_goal_id="g1", active_cycle_id="prefill-cycle"))
    _, _, _, card = await setup_card(db)
    if record_status == "draft":
        assert card["fields"]["activity_content"] == "散步" and card["fields"]["difficulty_rating"] == 0
        assert card["plan_id"] == "prefill"
    else:
        assert card["fields"] == {} and card["plan_id"] is None
        assert card["goal_id"] is None


async def test_new_primary_binds_new_saved_draft_without_editing_old_confirmed_core(goal_api):
    _, db, _ = goal_api
    plans, cycles, goals, rt = (schema.tables[name] for name in
        ("module_two_record", "pa_cycles", "pa_goals", "conversation_runtime_states"))
    await db.execute(insert(plans), {"id": "old-confirmed", "goal_id": "g1", "version_no": 1,
        "timezone": "Asia/Shanghai", "activity_content": "旧核心散步", "schedule_text": "旧安排",
        "record_status": "confirmed", "confirmation_status": "confirmed", "confirmation_message_id": 77})
    await db.execute(update(goals).where(goals.c.id == "g1").values(current_plan_record_id="old-confirmed"))
    await db.execute(insert(cycles), {"id": "old-cycle", "goal_id": "g1", "ordinal": 1,
        "status": "waiting_execution", "module_two_record_id": "old-confirmed"})
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(active_goal_id="g1", active_cycle_id="old-cycle"))
    conversation, state, _, card = await setup_card(db)
    assert card["goal_id"] is None and card["fields"] == {}
    await db.execute(insert(plans), {"id": "new-draft", "goal_id": "g2", "version_no": 1,
        "timezone": "Asia/Shanghai", "activity_content": "新活动骑车"})
    await db.execute(insert(cycles), {"id": "new-cycle", "goal_id": "g2", "ordinal": 1, "status": "planning"})
    record = (await db.execute(select(plans).where(plans.c.id == "new-draft"))).mappings().one()
    result = await sync_core_card(db, conversation, {**state, "active_goal_id": "g2", "active_cycle_id": "new-cycle"}, "discussing", record)
    assert result["id"] == card["id"] and result["goal_id"] == "g2" and result["plan_id"] == "new-draft"
    old = (await db.execute(select(plans).where(plans.c.id == "old-confirmed"))).mappings().one()
    assert old["record_status"] == "confirmed" and old["activity_content"] == "旧核心散步"
    assert await db.scalar(select(goals.c.current_plan_record_id).where(goals.c.id == "g1")) == "old-confirmed"
