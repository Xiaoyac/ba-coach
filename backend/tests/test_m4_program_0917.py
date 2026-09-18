import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.v2_workflow import persist_record, record_steps
from app.workflow_contract import MODULE_STEP_KEYS
from test_cycle_program_0914 import confirmation, seed_review
from test_goal_overview import goal_api


def _rebuild_seeded_m4_payload(review):
    contract = review["phase_c"]["_m4_contract"]
    quotes = {key: item["quote"] for key, item in contract["evidence"].items()}
    phase_c = {key: value for key, value in review["phase_c"].items()
               if key not in {"_m4_contract", "schema_version"}}
    return {
        "phase_a": review["phase_a"], "phase_b": review["phase_b"], "phase_c": phase_c,
        "ai_abc_chain_summary": review["abc_chain_summary"],
        "ba_reeducation_content": review["ba_reeducation_content"],
        "core_difficulty_type": review["core_difficulty_type"],
        "difficulty_description": review["difficulty_description"],
        "next_coping_strategy": review["next_coping_strategy"],
        "review_decision": review["review_decision"], "review_summary": review["review_summary"],
        "review_followup": {"action": "continue", "source_quote": quotes["decision_quote"]},
        "m4_contract": {**quotes, "emotion_improved": True, "chain_status": "confirmed",
                        "core_questions_resolved": True, "difficulty_status": "none"},
        "_source_session_id": "chat-a", "_source_assistant_message_id": 20,
    }


async def _persist_rebuilt_seed(db, *, route=True):
    reviews, details = schema.tables["module_four_record"], schema.tables["pa_review_details"]
    seeded = (await db.execute(select(reviews).where(reviews.c.id == "review-draft"))).mappings().one()
    payload = _rebuild_seeded_m4_payload(seeded)
    await db.execute(delete(details).where(details.c.review_id == "review-draft"))
    await db.execute(delete(reviews).where(reviews.c.id == "review-draft"))
    await db.commit()
    record_id = await persist_record(async_sessionmaker(db.bind, expire_on_commit=False), module="module_4",
                                     user_id="a", data=payload, cycle_id="reviewed-cycle")
    if route:
        await record_steps(db, session_id="chat-a", user_id="a", module="module_4", requested_target="module_4",
                           steps=list(MODULE_STEP_KEYS["module_4"]), assistant_message_id=20)
    else:
        # Simulate an already-ready draft left by a pre-upgrade client, before
        # a new correcting user turn. No new dialogue confirmation is run.
        rt = schema.tables["conversation_runtime_states"]
        await db.execute(update(rt).where(rt.c.conversation_id == 1).values(
            last_transition_reason="awaiting_record_confirmation"))
    await db.commit()
    return record_id, payload


@pytest.mark.asyncio
async def test_final_m4_confirmation_blocks_an_incomplete_evidence_snapshot(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=1)
    reviews = schema.tables["module_four_record"]
    await db.execute(update(reviews).where(reviews.c.id == "review-draft").values(
        phase_c={"schema_version": 1}))
    await db.commit()

    current = (await client.get("/api/program/chat-a")).json()
    assert not current["can_confirm"]
    assert "m4_evidence_refresh" in current["missing_fields"]
    response = await client.post("/api/program/chat-a/confirm", json={
        "record_id": "review-draft", "record_hash": current["record_hash"],
        "row_version": current["runtime"]["row_version"],
    })

    assert response.status_code == 409
    assert (await db.execute(select(func.max(ConversationMessage.position)).where(
        ConversationMessage.conversation_id == 1))).scalar_one() == 10
    assert (await db.execute(select(reviews.c.record_status).where(
        reviews.c.id == "review-draft"))).scalar_one() == "draft"


@pytest.mark.asyncio
async def test_confirmed_continue_preserves_abc_acknowledgement_and_current_plan(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=1)

    response = await client.post("/api/program/chat-a/confirm", json=await confirmation(client))

    assert response.status_code == 200, response.text
    runtime = response.json()["runtime"]
    cycles, reviews = schema.tables["pa_cycles"], schema.tables["module_four_record"]
    review = (await db.execute(select(reviews).where(reviews.c.id == "review-draft"))).mappings().one()
    successor = (await db.execute(select(cycles).where(cycles.c.id == runtime["active_cycle_id"]))).mappings().one()
    assert review["confirmation_message_id"] == 14
    assert successor["module_two_record_id"] == "cycle-plan"
    assert successor["module_three_record_id"] == "cycle-contract"


@pytest.mark.asyncio
async def test_confirmed_adjustment_creates_an_editable_next_plan_without_mutating_source(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=3)

    response = await client.post("/api/program/chat-a/confirm", json=await confirmation(client))

    assert response.status_code == 200, response.text
    runtime = response.json()["runtime"]
    cycles, plans = schema.tables["pa_cycles"], schema.tables["module_two_record"]
    successor = (await db.execute(select(cycles).where(cycles.c.id == runtime["active_cycle_id"]))).mappings().one()
    source = (await db.execute(select(plans).where(plans.c.id == "cycle-plan"))).mappings().one()
    draft = (await db.execute(select(plans).where(
        plans.c.goal_id == "g1", plans.c.record_status == "draft"))).mappings().one()
    assert successor["module_two_record_id"] is None
    assert source["record_status"] == "confirmed"
    assert draft["id"] != source["id"]
    assert draft["activity_content"] == source["activity_content"]


@pytest.mark.asyncio
async def test_m4_persist_then_step_gate_commits_dialogue_evidence(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=1)

    record_id, _ = await _persist_rebuilt_seed(db)
    current = (await client.get("/api/program/chat-a")).json()

    assert not current["can_confirm"]
    assert current["runtime"]["active_cycle_id"] != "reviewed-cycle"
    reviews, cycles = schema.tables["module_four_record"], schema.tables["pa_cycles"]
    review = (await db.execute(select(reviews).where(reviews.c.id == record_id))).mappings().one()
    next_cycle = (await db.execute(select(cycles).where(
        cycles.c.id == current["runtime"]["active_cycle_id"]))).mappings().one()
    assert review["confirmation_message_id"] == 14
    assert next_cycle["module_two_record_id"] == "cycle-plan"


@pytest.mark.asyncio
async def test_latest_invalid_m4_extraction_clears_persisted_readiness(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=1)
    record_id, _ = await _persist_rebuilt_seed(db, route=False)
    assert (await client.get("/api/program/chat-a")).json()["can_confirm"]

    db.add_all([
        ConversationMessage(id=21, conversation_id=1, position=11, role="user", content="更正：刚才的情况不对。"),
        ConversationMessage(id=22, conversation_id=1, position=12, role="assistant", content="我会重新核对。"),
    ])
    await db.commit()
    await persist_record(async_sessionmaker(db.bind, expire_on_commit=False), module="module_4", user_id="a",
                         cycle_id="reviewed-cycle", data={"m4_contract": {"chain_status": "confirmed",
                         "confirmation_message_id": 999999}, "_source_session_id": "chat-a",
                         "_source_assistant_message_id": 22})
    await record_steps(db, session_id="chat-a", user_id="a", module="module_4", requested_target="module_4",
                       steps=list(MODULE_STEP_KEYS["module_4"]), assistant_message_id=22)
    await db.commit()

    current = (await client.get("/api/program/chat-a")).json()
    assert not current["can_confirm"]
    assert "m4_milestone_1" in current["missing_fields"]
    assert (await client.post("/api/program/chat-a/confirm", json={
        "record_id": record_id, "record_hash": current["record_hash"],
        "row_version": current["runtime"]["row_version"],
    })).status_code == 409
