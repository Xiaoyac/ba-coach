"""M1 production API gates tested against an isolated in-memory database."""
import pytest
from sqlalchemy import insert, update, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database_v2_schema import metadata as schema
from app.m1_contract import VERSION
from app.models import ConversationMessage
from app.workflow_contract import MODULE_STEP_KEYS
from app.v2_workflow import record_steps, record_values, persist_record
from test_goal_overview import goal_api  # shared isolated authenticated harness


async def seed(db, *, missing=(), path="low_disclosure", source="chat-a", turn=20):
    await db.execute(insert(ConversationMessage), {"id": 20, "conversation_id": 1,
        "position": 1, "role": "assistant", "content": "验收中的最后一轮回复"})
    await db.execute(update(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == 1).values(
        current_module="module_1", last_transition_reason="awaiting_record_confirmation"))
    await db.execute(update(schema.tables["user_module_one_state"]).where(
        schema.tables["user_module_one_state"].c.user_id == "a").values(
        status="in_progress", completion_source="none", completed_steps=[]))
    contract = {"version": VERSION, "session_id": source, "path": path,
        "assistant_message_id": turn, "completed_steps": list(MODULE_STEP_KEYS["module_1"]),
        "missing_fields": list(missing), "milestones": {"m1_milestone_1": True,
            "m1_milestone_2": True, "m1_milestone_3": not missing}, "evidence": {"private": "not for UI"}}
    await db.execute(insert(schema.tables["module_one_record"]), {"id": "m1-draft", "user_id": "a",
        "version_no": 1, "event_experience": {"schema_version": 2, "_m1_contract": contract}})
    await db.commit()


async def confirmation(client):
    result = (await client.get("/api/program/chat-a")).json()
    return result, {"record_id": "m1-draft", "record_hash": result["record_hash"],
                    "row_version": result["runtime"]["row_version"]}


@pytest.mark.asyncio
async def test_low_disclosure_confirms_without_personal_summary_and_reuses_m1(goal_api):
    client, db, _ = goal_api
    await seed(db)
    result, payload = await confirmation(client)
    assert result["can_confirm"] is True
    assert "_m1_contract" not in str(result["draft"])
    assert result["m1_contract"]["path"] == "low_disclosure"
    response = await client.post("/api/program/chat-a/confirm", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["runtime"]["current_module"] == "module_2"
    assert response.json()["m1_reusable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["m1_milestone_1", "m1_milestone_2", "ba_understanding", "goal_setting_consent"])
async def test_api_does_not_trust_router_ready_without_contract(goal_api, missing):
    client, db, _ = goal_api
    await seed(db, missing=[missing])
    result, payload = await confirmation(client)
    assert result["can_confirm"] is False
    assert missing in result["missing_fields"]
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409


@pytest.mark.asyncio
async def test_other_chat_evidence_and_stale_hash_do_not_confirm(goal_api):
    client, db, _ = goal_api
    await seed(db, source="other-chat")
    result, payload = await confirmation(client)
    assert result["can_confirm"] is False
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409
    payload["record_hash"] = "0" * 64
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409


@pytest.mark.asyncio
async def test_late_or_failed_extraction_cannot_reuse_old_turn(goal_api):
    _, db, _ = goal_api
    await seed(db, turn=19)
    await record_steps(db, session_id="chat-a", user_id="a", module="module_1",
        requested_target="module_2", steps=list(MODULE_STEP_KEYS["module_1"]), assistant_message_id=20)
    state = (await db.execute(select(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == 1))).mappings().one()
    assert state["last_transition_reason"] == "discussion_required"
    steps = (await db.execute(select(schema.tables["user_module_one_state"].c.completed_steps).where(
        schema.tables["user_module_one_state"].c.user_id == "a"))).scalar_one()
    assert steps == []


@pytest.mark.asyncio
async def test_fresh_contract_authority_and_revocation(goal_api):
    _, db, _ = goal_api
    await seed(db)
    for revoked, reason in [((), "awaiting_record_confirmation"), (("goal_setting_consent",), "discussion_required")]:
        await record_steps(db, session_id="chat-a", user_id="a", module="module_1",
            requested_target="module_1", steps=[], assistant_message_id=20,
            revoked_steps=revoked, revocation_evidence="还不想设目标" if revoked else None)
        state = (await db.execute(select(schema.tables["conversation_runtime_states"]).where(
            schema.tables["conversation_runtime_states"].c.conversation_id == 1))).mappings().one()
        assert state["last_transition_reason"] == reason


def test_null_and_empty_methods_preserved_without_schema_changes():
    for methods in (None, []):
        values = record_values("module_1", {"attempted_relief_methods": methods,
            "abc_event": None, "m1_contract": {"completed_steps": [], "version": VERSION}})
        assert values["attempted_relief_methods"] == methods
        assert values["event_experience"]["_m1_contract"]["version"] == VERSION
        assert values["goal_setting_willingness"] == "unknown"
        assert "user_approval_level" not in values  # retained in metadata, no new column


@pytest.mark.asyncio
async def test_current_snapshot_clears_stale_summary_methods_and_readiness(goal_api):
    client, db, _ = goal_api
    await seed(db)
    await db.execute(insert(ConversationMessage), {"id": 21, "conversation_id": 1,
        "position": 2, "role": "assistant", "content": "这次我们重新核对经历。"})
    await db.commit()
    values = {"ai_depression_cycle_summary": None, "attempted_relief_methods": None,
        "abc_event": None, "m1_contract": {"version": VERSION, "session_id": "chat-a",
            "assistant_message_id": 21, "completed_steps": [], "missing_fields": ["m1_milestone_2"]}}
    await persist_record(async_sessionmaker(db.bind, expire_on_commit=False), module="module_1",
        user_id="a", data=values, cycle_id=None)
    result, _ = await confirmation(client)
    assert result["can_confirm"] is False
    assert result["draft"]["functional_chain_summary"] is None
    assert result["draft"]["attempted_relief_methods"] is None
    assert result["runtime"]["last_transition_reason"] == "m1_snapshot_updated"


@pytest.mark.asyncio
async def test_truncated_m1_extraction_is_not_salvaged_into_completion(provider):
    from app.clinical_extraction import extract_module_record_detailed
    provider.extraction_result = '{"chief_complaint":"test", "m1_contract": {"path":"personalized"'
    raw, _ = await extract_module_record_detailed(provider, module="module_1", transcript="user：test", max_tokens=1800)
    assert raw == {}


@pytest.mark.asyncio
async def test_new_message_blocks_old_ready_draft_even_if_background_failed(goal_api):
    client, db, _ = goal_api
    await seed(db)
    _, payload = await confirmation(client)
    await db.execute(insert(ConversationMessage), {"id": 21, "conversation_id": 1,
        "position": 2, "role": "user", "content": "我改主意了，先不设目标。"})
    await db.commit()
    result, _ = await confirmation(client)
    assert result["can_confirm"] is False
    assert "m1_evidence_refresh" in result["missing_fields"]
    assert (await client.post("/api/program/chat-a/confirm", json=payload)).status_code == 409
