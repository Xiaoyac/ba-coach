import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.goal_contract import save_review_followup
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
@pytest.mark.parametrize("feedback_verified", [False, True])
async def test_m4_waiting_ends_only_after_verified_execution_feedback(goal_api, feedback_verified):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    cycles = schema.tables["pa_cycles"]
    rt = schema.tables["conversation_runtime_states"]
    reviews = schema.tables["module_four_record"]
    seeded = (await db.execute(select(reviews).where(reviews.c.id == "review-draft"))).mappings().one()
    payload = _rebuild_seeded_m4_payload(seeded)
    if not feedback_verified:
        payload["m4_contract"] = {}
    await db.execute(update(cycles).where(cycles.c.id == "reviewed-cycle").values(status="waiting_execution"))
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(flow_status="waiting_execution"))
    await db.commit()
    await persist_record(async_sessionmaker(db.bind, expire_on_commit=False), module="module_4",
        user_id="a", data=payload, cycle_id="reviewed-cycle")
    cycle_status = (await db.execute(select(cycles.c.status).where(cycles.c.id == "reviewed-cycle"))).scalar_one()
    flow = (await db.execute(select(rt.c.flow_status).where(rt.c.conversation_id == 1))).scalar_one()
    assert cycle_status == ("reviewing" if feedback_verified else "waiting_execution")
    assert flow == ("active" if feedback_verified else "waiting_execution")


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


@pytest.mark.asyncio
async def test_m4_review_action_requires_real_source_quote(goal_api):
    client, db, _ = goal_api
    await seed_review(db, decision=1)
    details = schema.tables["pa_review_details"]
    await db.execute(update(details).where(details.c.review_id == "review-draft").values(
        source_quote="伪造的决定"))
    await db.commit()
    response = await client.post("/api/program/chat-a/confirm", json=await confirmation(client))
    assert response.status_code == 409
    assert "review_action_missing" in response.json()["detail"]["reason_codes"]


async def _cycle_messages(db):
    return list((await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == 1).order_by(
            ConversationMessage.position, ConversationMessage.id))).scalars())


@pytest.mark.asyncio
async def test_review_followup_keeps_same_decision_when_summary_is_added_later(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "assistant",
        "content": "补充复盘总结：按你的决定继续观察。"})
    await db.commit()
    messages = await _cycle_messages(db)
    evidence = {"message_id": 18, "quote": "我决定继续按这个计划。"}
    await save_review_followup(
        db, "review-draft", {"action": "continue", "source_quote": evidence["quote"]},
        messages, 1, decision_evidence=evidence,
    )
    row = (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one()
    assert row["source_message_id"] == 18
    assert row["source_quote"] == evidence["quote"]


@pytest.mark.asyncio
async def test_review_followup_keeps_verified_decision_when_later_snapshot_omits_followup(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "assistant",
        "content": "补充复盘总结：按你的决定继续观察。"})
    await db.commit()
    messages = await _cycle_messages(db)

    # A later extraction may contain only the closing summary.  It must not
    # erase the already verified decision when no user turn revoked or changed
    # it; the old source remains the auditable authority for this cycle.
    await save_review_followup(db, "review-draft", None, messages, 1)
    row = (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one()
    assert row["action"] == "continue"
    assert row["source_message_id"] == 18
    assert row["source_quote"] == "我决定继续按这个计划。"


@pytest.mark.asyncio
async def test_review_followup_omission_accepts_same_decision_evidence(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    messages = await _cycle_messages(db)
    evidence = {"message_id": 18, "quote": "我决定继续按这个计划。"}

    # A durable M4 snapshot may carry the verified pointer even though the
    # optional follow-up object is absent.  The pointer is accepted when it
    # resolves to the already persisted user source.
    await save_review_followup(
        db, "review-draft", None, messages, 1, decision_evidence=evidence,
    )
    row = (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one()
    assert row["action"] == "continue"
    assert row["source_message_id"] == evidence["message_id"]
    assert row["source_quote"] == evidence["quote"]


@pytest.mark.asyncio
async def test_review_followup_omission_accepts_same_source_with_different_literal_span(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    messages = await _cycle_messages(db)

    # m4_contract.decision_quote and review_followup.source_quote can select
    # different literal spans from the same user turn.  Source identity and
    # literal containment are the safety boundary; span equality is not.
    await save_review_followup(
        db, "review-draft", None, messages, 1,
        decision_evidence={"message_id": 18, "quote": "继续按这个计划"},
    )
    row = (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one()
    assert row["source_message_id"] == 18
    assert row["source_quote"] == "我决定继续按这个计划。"


@pytest.mark.asyncio
async def test_review_followup_omission_rejects_conflicting_decision_evidence(goal_api):
    """An omitted follow-up cannot hide a newly extracted, conflicting source."""
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    conflict = "我决定调整计划。"
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "user",
        "content": conflict,
    })
    await db.commit()
    messages = await _cycle_messages(db)

    # ``raw`` is absent, but the M4 contract points at a different user
    # decision.  Keeping the old continue row would make the durable record
    # contradict the evidence that was used for this extraction.
    await save_review_followup(
        db, "review-draft", None, messages, 1,
        decision_evidence={"message_id": 27, "quote": conflict},
    )
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
async def test_review_followup_omission_clears_when_new_decision_is_selected(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    quote = "我决定调整计划。"
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "user",
        "content": quote,
    })
    await db.commit()
    messages = await _cycle_messages(db)

    # A new action is a replacement decision even when the follow-up extractor
    # omitted its object.  The action number also conflicts with the persisted
    # continue row, so the old row must be removed.
    await save_review_followup(
        db, "review-draft", None, messages, 3,
        decision_evidence={"message_id": 27, "quote": quote},
    )
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
async def test_review_followup_omission_clears_after_withdrawal(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    withdrawal = "我撤回刚才的决定，先不决定。"
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "user",
        "content": withdrawal,
    })
    await db.commit()
    messages = await _cycle_messages(db)

    # Even with no new evidence pointer, a later user withdrawal invalidates
    # the previously verified decision and must not be treated as an omission.
    await save_review_followup(db, "review-draft", None, messages, 1)
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
async def test_review_followup_omission_clears_unreported_direction_change(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=3)
    quote = "我决定继续按这个计划。"
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "user",
        "content": quote,
    })
    await db.commit()
    messages = await _cycle_messages(db)

    # Even if both the follow-up object and decision field are omitted, an
    # explicit later direction invalidates the old adjustment row.  This is a
    # fail-closed guard for partial extraction rather than a new decision
    # classifier.
    await save_review_followup(db, "review-draft", None, messages, None)
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_id,evidence_quote,followup_quote", [
    (18, "我决定继续按这个计划。", "我决定调整计划。"),
    (20, "复盘：本次已完成，下一步按决定执行。", "复盘：本次已完成，下一步按决定执行。"),
])
async def test_review_followup_rejects_mismatched_or_wrong_role_decision_source(
    goal_api, evidence_id, evidence_quote, followup_quote,
):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    messages = await _cycle_messages(db)
    await save_review_followup(
        db, "review-draft", {"action": "continue", "source_quote": followup_quote},
        messages, 1, decision_evidence={"message_id": evidence_id, "quote": evidence_quote},
    )
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
async def test_review_followup_ignores_decision_from_another_conversation_cycle(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    await db.execute(insert(ConversationMessage), {
        "id": 28, "conversation_id": 2, "position": 11, "role": "user",
        "content": "我决定继续按这个计划。"})
    await db.commit()
    # _cycle_messages is deliberately conversation-scoped; the foreign row
    # must not become evidence for this cycle even when the quote is identical.
    messages = await _cycle_messages(db)
    evidence = {"message_id": 28, "quote": "我决定继续按这个计划。"}
    await save_review_followup(
        db, "review-draft", {"action": "continue", "source_quote": evidence["quote"]},
        messages, 1, decision_evidence=evidence,
    )
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
async def test_review_followup_does_not_reuse_decision_after_withdrawal_or_change(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "user",
        "content": "我撤回刚才的决定，先不决定。"})
    await db.commit()
    messages = await _cycle_messages(db)
    evidence = {"message_id": 18, "quote": "我决定继续按这个计划。"}
    await save_review_followup(
        db, "review-draft", {"action": "continue", "source_quote": evidence["quote"]},
        messages, 1, decision_evidence=evidence,
    )
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
async def test_review_followup_rejects_continue_when_user_reopens_same_plan(goal_api):
    """A same-value change/re-discussion is still a new direction.

    The old production failure treated this user turn as ``continue`` because
    the requested duration was already five minutes.  The assistant's later
    suggestion to keep the plan cannot replace the user's explicit source.
    """
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    quote = "我想保留同一个目标，但把每次时长改成五分钟，重新讨论这份计划。"
    await db.execute(insert(ConversationMessage), {
        "id": 27, "conversation_id": 1, "position": 11, "role": "user",
        "content": quote,
    })
    await db.execute(insert(ConversationMessage), {
        "id": 28, "conversation_id": 1, "position": 12, "role": "assistant",
        "content": "时长本来就是五分钟，我们按原计划继续，可以吗？",
    })
    await db.commit()
    messages = await _cycle_messages(db)
    await save_review_followup(
        db, "review-draft", {"action": "continue", "source_quote": quote},
        messages, 1, decision_evidence={"message_id": 27, "quote": quote},
    )
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None


@pytest.mark.asyncio
async def test_review_followup_never_uses_assistant_proposal_as_decision(goal_api):
    _, db, _ = goal_api
    await seed_review(db, decision=1)
    proposal = "按原计划继续，可以吗？"
    await db.execute(insert(ConversationMessage), {
        "id": 28, "conversation_id": 1, "position": 11, "role": "assistant",
        "content": proposal,
    })
    await db.commit()
    messages = await _cycle_messages(db)
    await save_review_followup(
        db, "review-draft", {"action": "continue", "source_quote": proposal},
        messages, 1, decision_evidence={"message_id": 28, "quote": proposal},
    )
    assert (await db.execute(select(schema.tables["pa_review_details"]).where(
        schema.tables["pa_review_details"].c.review_id == "review-draft"))).mappings().one_or_none() is None
