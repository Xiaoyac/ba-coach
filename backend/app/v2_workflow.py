"""V2 workflow authority, scoped by user and execution cycle."""
import json
import re
from datetime import datetime
from sqlalchemy import select, update, insert, func
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage
from .v2_repository import new_id, now, owned_goal, active_memories, create_goal, start_cycle
from .workflow_contract import MODULE_STEP_KEYS


async def runtime_for(db, session_id):
    conversation = (await db.execute(select(Conversation).where(Conversation.session_id == session_id))).scalar_one_or_none()
    if not conversation:
        return None, None
    rt = schema.tables["conversation_runtime_states"]
    state = (await db.execute(select(rt).where(rt.c.conversation_id == conversation.id))).mappings().one_or_none()
    return conversation, state


async def load_workflow(maker, session_id):
    async with maker() as db:
        conversation, state = await runtime_for(db, session_id)
        steps = {module: [] for module in MODULE_STEP_KEYS}
        if not conversation or not state:
            return steps, None
        m1 = schema.tables["user_module_one_state"]
        one = (await db.execute(select(m1).where(m1.c.user_id == conversation.subject_id))).mappings().one_or_none()
        if one:
            steps["module_1"] = one["completed_steps"]
        progress = schema.tables["pa_cycle_progress"]
        row = (await db.execute(select(progress).where(progress.c.cycle_id == state["active_cycle_id"]))).mappings().one_or_none()
        if row:
            for n in (2, 3, 4):
                steps[f"module_{n}"] = row[f"module_{n}_steps"]
        return steps, state["active_cycle_id"]


async def current_cycle(db, session_id):
    _, state = await runtime_for(db, session_id)
    return state["active_cycle_id"] if state else None


async def module_extraction_is_current(
    db, *, conversation_id, state, module, assistant_message_id=None,
    following_user_message_id=None,
):
    """Whether M2/M4's source-validated extraction belongs to the latest turn."""
    if module not in {"module_2", "module_3", "module_4"}:
        return True
    freshness = (state["memory"] or {}).get("module_extraction_freshness", {}).get(module, {})
    source_message_id = freshness.get("assistant_message_id")
    if source_message_id is None or freshness.get("cycle_id") != state["active_cycle_id"]:
        return False
    latest_row = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id).order_by(
        ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1))).scalar_one_or_none()
    if latest_row is None:
        return False
    if latest_row.id == source_message_id:
        return assistant_message_id is None or assistant_message_id == source_message_id
    # A confirmation command is submitted by the user immediately after the
    # assistant card that produced the fresh extraction.  At that point the
    # latest transcript row is necessarily the user message, so the ordinary
    # "latest == assistant" check would reject a valid pre-generation commit.
    if following_user_message_id is None or latest_row.id != following_user_message_id:
        return False
    source_row = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.id == source_message_id,
        ConversationMessage.conversation_id == conversation_id,
        ConversationMessage.role == "assistant"))).scalar_one_or_none()
    return bool(source_row and latest_row.role == "user"
                and latest_row.position == source_row.position + 1
                and (assistant_message_id is None or assistant_message_id == source_message_id))


async def record_steps(db, *, session_id, user_id, module, requested_target, steps, assistant_message_id,
                       revoked_steps=(), revocation_evidence=None, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(policy="verified_contract", block_reasons=[])
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id or not state:
        raise ValueError("Conversation not found")
    profiles, rt = schema.tables["user_profile"], schema.tables["conversation_runtime_states"]
    await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
    state = (await db.execute(select(rt).where(rt.c.conversation_id == conversation.id).with_for_update())).mappings().one()
    if state["memory"].get("sandbox_mode") == "true":
        return requested_target, None
    latest_id = (await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conversation.id).order_by(
            ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
    if latest_id != assistant_message_id:
        diagnostics["block_reasons"] = ["已有更新轮次，本次路由结果已过期"]
        return state["current_module"], state["active_cycle_id"]
    # Never let a late router overwrite a goal selection or confirmed transition.
    if state["current_module"] != module:
        diagnostics["block_reasons"] = ["对话阶段已被更新，本次路由结果已过期"]
        return state["current_module"], state["active_cycle_id"]
    if state["flow_status"] in {"completed", "paused"}:
        diagnostics["block_reasons"] = ["当前流程已结束或暂停"]
        return module, state["active_cycle_id"]
    table = schema.tables["user_module_one_state"] if module == "module_1" else schema.tables["pa_cycle_progress"]
    key = table.c.user_id == user_id if module == "module_1" else table.c.cycle_id == state["active_cycle_id"]
    row = (await db.execute(select(table).where(key).with_for_update())).mappings().one_or_none()
    if row is None:
        if module != "module_1":
            return module, state["active_cycle_id"]
        await db.execute(insert(table), {"user_id": user_id, "completed_steps": []})
        row = {"completed_steps": [], "row_version": 0}
    column = "completed_steps" if module == "module_1" else module + "_steps"
    allowed = MODULE_STEP_KEYS[module]
    revoked = set(revoked_steps) & set(allowed) if revocation_evidence else set()
    merged = [s for s in allowed if s in (set(row[column] or []) | set(steps)) - revoked]
    m1_missing = []
    extraction_fresh = True
    if module == "module_1":
        from .m1_contract import contract_for, missing_m1_fields, reconcile_router_completion
        records = schema.tables["module_one_record"]
        pending = (await db.execute(select(records).where(records.c.user_id == user_id,
            records.c.record_status == "draft").order_by(records.c.created_at.desc()).limit(1))).mappings().one_or_none()
        contract = contract_for(pending)
        fresh = (contract.get("session_id") == session_id and assistant_message_id is not None
                 and contract.get("assistant_message_id") == assistant_message_id)
        if fresh and requested_target == "module_2" and set(allowed).issubset(steps) and not revoked:
            messages = (await db.execute(select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id).order_by(
                ConversationMessage.position, ConversationMessage.id))).scalars().all()
            turns = [(message.role, message.content) for message in messages if message.content]
            reviewed = reconcile_router_completion(contract, turns, session_id=session_id,
                                                   assistant_message_id=assistant_message_id)
            if reviewed:
                contract = reviewed
                values = {"event_experience": {**pending["event_experience"], "_m1_contract": contract},
                          "ba_explanation_status": "explained", "goal_setting_willingness": "willing"}
                await db.execute(update(records).where(records.c.id == pending["id"]).values(**values, updated_at=now()))
                pending = {**pending, **values}
                diagnostics["policy"] = "router_evidence_reconciled"
        # Router steps remain a full current snapshot.  The persistence layer
        # may carry forward individually revalidated M1 evidence, but a failed
        # extraction or a correction still leaves this gate stale/closed.
        merged = [s for s in allowed if s in contract.get("completed_steps", []) and s not in revoked] if fresh else []
        m1_missing = missing_m1_fields(pending, session_id) if fresh else ["m1_evidence_refresh"]
    if module in {"module_2", "module_3", "module_4"}:
        # The draft is intentionally accumulated, but its readiness is not:
        # every confirmation-capable turn needs a source-validated extraction
        # from that exact assistant turn.  If extraction failed after a user
        # correction, the old draft remains visible but cannot carry old
        # router steps or confirmation readiness forward.
        extraction_fresh = await module_extraction_is_current(db, conversation_id=conversation.id,
            state=state, module=module, assistant_message_id=assistant_message_id)
        if not extraction_fresh:
            merged = []
    if module == "module_4":
        from .m4_contract import contract_for, missing_fields
        reviews, followups = schema.tables["module_four_record"], schema.tables["pa_review_details"]
        pending = (await db.execute(select(reviews).where(reviews.c.cycle_id == state["active_cycle_id"],
            reviews.c.record_status == "draft"))).mappings().one_or_none()
        contract = contract_for(pending)
        fresh_contract = (extraction_fresh and contract.get("assistant_message_id") == assistant_message_id
                          and contract.get("session_id") == session_id and contract.get("cycle_id") == state["active_cycle_id"])
        merged = [s for s in allowed if s in contract.get("completed_steps", []) and s not in revoked] if fresh_contract else []
        decision_exists = (await db.execute(select(followups.c.review_id).join(reviews).where(
            reviews.c.cycle_id == state["active_cycle_id"], reviews.c.record_status == "draft"))).first()
        if not extraction_fresh or not decision_exists:
            merged = [step for step in merged if step != "review_decision_made"]
    # Publish the same structured readiness reasons used by the confirmation
    # endpoint.  Router step evidence remains useful for conversation flow,
    # but it no longer silently substitutes for the record's required shape.
    readiness = None
    if module in {"module_2", "module_3", "module_4"}:
        from .workflow_readiness import evaluate_readiness
        readiness_record = pending if module == "module_4" else None
        if readiness_record is None:
            records = schema.tables[TABLES[module]]
            scope = records.c.goal_id == state["active_goal_id"]
            readiness_record = (await db.execute(select(records).where(
                scope, records.c.record_status == "draft").order_by(
                records.c.created_at.desc()).limit(1))).mappings().one_or_none()
        readiness = evaluate_readiness(module, readiness_record,
            extraction_fresh=extraction_fresh,
            m4_missing_fields=(contract.get("missing_fields") if module == "module_4" and isinstance(contract, dict) else None))
        diagnostics["reason_codes"] = [item["code"] for item in readiness["reasons"]]
        diagnostics["readiness"] = readiness
    await db.execute(update(table).where(key).values(**{column: merged, "row_version": row["row_version"] + 1,
                                                     "updated_at": now()}))
    # Readiness to SHOW/confirm a version is not the confirmation itself.
    # Requiring pa_card_completed / recording_plan_agreed here creates a
    # cycle: a user cannot confirm until the router already calls it confirmed.
    # The commit service records those final steps only after a real consent.
    preparation = set(allowed)
    if module == "module_2":
        preparation.discard("pa_card_completed")
    elif module == "module_3":
        preparation = set()  # the complete record describes the proposed agreement
    ready = (not m1_missing and preparation.issubset(merged)
             and (readiness is None or readiness["ready"])
             and (requested_target != module or module in {"module_1", "module_2", "module_3", "module_4"}))
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
        last_transition_reason="awaiting_record_confirmation" if ready else "discussion_required",
        row_version=rt.c.row_version + 1))
    # Publish the final outcome, rather than recording the pre-confirmation
    # module as applied_target even when confirmation subsequently succeeded.
    from .dialogue_confirmation import advance_from_dialogue
    advanced = await advance_from_dialogue(db, session_id=session_id, user_id=user_id,
        assistant_message_id=assistant_message_id)
    applied_target = advanced[0] if advanced else module
    if requested_target != applied_target:
        if module == "module_1":
            labels = {"m1_milestone_1": "具体经历或低披露选择的证据尚不完整",
                      "m1_milestone_2": "经历总结认可或缓解方法的证据尚不完整",
                      "ba_understanding": "BA 理解证据尚未通过核验",
                      "goal_setting_consent": "目标设定同意尚未通过核验",
                      "m1_evidence_refresh": "最新一轮证据尚未成功刷新"}
            diagnostics["block_reasons"] = [labels.get(key, key) for key in m1_missing]
            if contract.get("education_missing_topics"):
                diagnostics["block_reasons"].append("待补充的 BA 内容：" + "；".join(contract["education_missing_topics"]))
            if any(issue.get("reason") == "before_new_education" for issue in contract.get("validation_issues", [])):
                diagnostics["block_reasons"].append("引用的理解回答早于后续补充解释，需要核对补充后的回答")
        if not diagnostics["block_reasons"]:
            diagnostics["block_reasons"] = ["当前讨论步骤或用户确认的原话尚未通过核验"]
    await db.execute(insert(schema.tables["ai_decision_logs"]), {
        "conversation_id": conversation.id, "turn_id": str(assistant_message_id),
        "goal_id": state["active_goal_id"], "cycle_id": state["active_cycle_id"],
        "module_name": module, "decision_type": "step_completion",
        "decision_value": {"completed_steps": merged, "requested_target": requested_target,
                           "applied_target": applied_target, "confirmation_required": ready and not bool(advanced),
                           "routing_policy": diagnostics["policy"],
                           "block_reasons": diagnostics["block_reasons"],
                           "reason_codes": diagnostics.get("reason_codes", []),
                           "readiness": diagnostics.get("readiness"),
                           "evidence_status": "extraction_quote_verified" if module == "module_1" else (
                               "current_extraction_verified" if extraction_fresh else "extraction_refresh_required"),
                           "m1_missing_fields": m1_missing if module == "module_1" else None,
                           "m4_contract_version": contract.get("version") if module == "module_4" else None,
                           "m4_missing_fields": contract.get("missing_fields", ["m4_evidence_refresh"]) if module == "module_4" else None,
                           "revoked_steps": sorted(revoked),
                           "revocation_evidence": revocation_evidence},
        "evidence_message_ids": [assistant_message_id] if assistant_message_id else [],
    })
    if advanced:
        return advanced
    return module, state["active_cycle_id"]


FIELD_MAP = {
    "module_1": {"abc_event": "event_experience", "ai_depression_cycle_summary": "functional_chain_summary"},
    "module_2": {"target_activity_content": "activity_content", "target_activity_time": "scheduled_start_at",
                 "target_activity_location": "location", "target_activity_duration_minutes": "duration_minutes",
                 "target_activity_companion": "companion"},
    "module_3": {"ai_record_requirement": "record_requirement", "user_acceptance_feeling": "acceptance_feeling",
                 "difficulty_feedback_mechanism": "feedback_mechanism"},
    "module_4": {"ai_abc_chain_summary": "abc_chain_summary"},
}
TABLES = {"module_1": "module_one_record", "module_2": "module_two_record",
          "module_3": "module_three_record", "module_4": "module_four_record"}

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _duration_number(token):
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        return (_CN_DIGITS.get(left, 1) * 10) + _CN_DIGITS.get(right, 0)
    return _CN_DIGITS.get(token)


def _synchronize_duration_text(values, existing):
    """Prevent a draft's numeric duration and narrative schedule diverging."""
    from collections.abc import Mapping

    if not isinstance(values, dict) or not isinstance(existing, Mapping):
        return values
    new_duration = values.get("duration_minutes")
    old_duration = existing.get("duration_minutes")
    schedule = values.get("schedule_text") or existing.get("schedule_text")
    if type(new_duration) is not int or type(old_duration) is not int or new_duration == old_duration:
        return values
    if not isinstance(schedule, str) or not schedule.strip():
        return values
    import re as _re
    matches = list(_re.finditer(r"(\d+|[零〇一二两三四五六七八九十]+)\s*分钟", schedule))
    if len(matches) == 1 and _duration_number(matches[0].group(1)) == new_duration:
        # A complete new schedule already agrees with its numeric duration.
        return values
    if len(matches) == 1 and _duration_number(matches[0].group(1)) == old_duration:
        match = matches[0]
        synchronized = schedule[:match.start()] + f"{new_duration}分钟" + schedule[match.end():]
        values["schedule_text"] = synchronized
    elif matches:
        # Several or ambiguous duration statements cannot be safely rewritten;
        # keep the last committed pair together until the user restates it.
        values["duration_minutes"] = old_duration
        values["schedule_text"] = existing.get("schedule_text")
    return values

# Side-channel extraction (activity observations, goal metadata, or a review
# follow-up without its record fields) must never make an old ready draft look
# current.  These are the fields that make the draft itself substantive.
FRESHNESS_FIELDS = {
    "module_3": {"record_requirement", "negotiated_record_plan", "acceptance_feeling", "feedback_mechanism"},
    "module_2": {"activity_content", "schedule_text", "scheduled_start_at", "location",
                 "duration_minutes", "frequency_rule", "companion", "potential_barriers",
                 "barrier_coping_plan"},
    "module_4": {"execution_result", "phase_a", "phase_b", "phase_c", "abc_chain_summary",
                 "core_difficulty_type", "difficulty_description", "ba_reeducation_content",
                 "next_coping_strategy", "review_decision", "review_summary"},
}


def record_values(module, data):
    """Map validated extractor output onto writable V2 columns."""
    table = schema.tables[TABLES[module]]
    forbidden = {"id", "user_id", "goal_id", "cycle_id", "version_no", "record_status",
                 "confirmation_status", "confirmation_message_id", "chain_confirmation_status",
                 "confirmed_at", "created_at", "updated_at"}
    values = {}
    for key, value in data.items():
        target = FIELD_MAP[module].get(key, key)
        if target in table.c and target not in forbidden:
            values[target] = value
    if module == "module_2" and isinstance(values.get("core_values"), str):
        values["core_values"] = [values["core_values"]]
    if module == "module_2" and isinstance(values.get("scheduled_start_at"), str):
        # Extractors emit ISO text while the DB column is a datetime.  Parse
        # it before an unverified confirmation turn can reach the update; a
        # valid card marker may still replace this value with its snapshot.
        try:
            values["scheduled_start_at"] = datetime.fromisoformat(
                values["scheduled_start_at"].replace("Z", "+00:00"))
        except ValueError:
            values["scheduled_start_at"] = None
    if module == "module_3" and isinstance(values.get("negotiated_record_plan"), str):
        values["negotiated_record_plan"] = {"schema_version": 1, "text": values["negotiated_record_plan"]}
    for key in ("event_experience", "phase_a", "phase_b", "phase_c"):
        if isinstance(values.get(key), dict):
            values[key] = {**values[key], "schema_version": 1}
    if module == "module_1" and isinstance(data.get("m1_contract"), dict):
        contract = data["m1_contract"]
        event = values.get("event_experience")
        values["event_experience"] = {**(event if isinstance(event, dict) else {}),
                                     "schema_version": 2, "_m1_contract": contract}
        values["ba_explanation_status"] = "explained" if "ba_education_completed" in contract["completed_steps"] else "not_recorded"
        values["goal_setting_willingness"] = "willing" if "goal_setting_consent" in contract["completed_steps"] else "unknown"
    return values


async def create_goal_from_agent_dialogue(db, *, session_id, user_id, data,
                                           completed_steps, assistant_message_id,
                                           diagnostics=None):
    """Atomically bind a new goal after source-validated user selection.

    The web client cannot call this path. Router evidence alone is insufficient
    because an assistant may merely list candidates; extracted activity content
    alone is also insufficient because it may come from an unchosen suggestion.
    """
    def blocked(code, message, *, detail=None):
        if diagnostics is not None:
            entry = {"status": "blocked", "reason_code": code, "message": message}
            if detail is not None:
                entry["detail"] = detail
            diagnostics["goal_creation"] = entry
        return None

    values = record_values("module_2", data)
    activity = values.get("activity_content")
    if not isinstance(activity, str) or not activity.strip():
        return blocked("activity_missing", "没有识别到可创建目标的具体活动")
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id or not state:
        return blocked("conversation_or_state_missing", "会话、用户或流程状态不存在")
    profiles, runtime = schema.tables["user_profile"], schema.tables["conversation_runtime_states"]
    await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
    state = (await db.execute(select(runtime).where(
        runtime.c.conversation_id == conversation.id).with_for_update())).mappings().one()
    if (state["current_module"] != "module_2" or state["flow_status"] == "completed"
            or state["active_goal_id"] or state["active_cycle_id"]
            or state["memory"].get("sandbox_mode") == "true"):
        if state["memory"].get("sandbox_mode") == "true":
            return blocked("sandbox_blocked", "沙盒会话不写入目标")
        return blocked("goal_creation_window_closed", "当前流程已有目标、已结束或不在目标设定阶段")

    from .goal_contract import (evidence_messages, proposal_evidence, recover_goal_proposal,
                                save_goal_details, save_plan_context)
    messages = await evidence_messages(db, conversation.id, user_id)
    if not messages or messages[-1].id != assistant_message_id:
        return blocked("assistant_evidence_stale", "当前助手消息不是会话最新证据，拒绝用旧轮次创建目标")
    proposal_present = "goal_proposal" in data
    proposal = data.get("goal_proposal") if proposal_present else None
    evidence = proposal_evidence(proposal, messages, activity)
    # A complete plan with an omitted proposal object is an extractor
    # omission, not proof that the user never chose the activity.  Recover
    # only from an explicit choice in the same transcript.  Keep an explicit
    # null/invalid proposal fail-closed: callers that deliberately supplied a
    # proposal have asked for validation.  An empty object is treated like an
    # omitted extractor side-channel because the graph preserves empty JSON
    # objects while filtering null values.
    from .plan_contract import missing_plan_fields, recover_one_time_frequency
    required_plan = {
        "activity_content": values.get("activity_content"),
        "schedule_text": values.get("schedule_text"),
        "location": values.get("location"),
        "duration_minutes": values.get("duration_minutes"),
        "frequency_rule": values.get("frequency_rule"),
        "potential_barriers": values.get("potential_barriers"),
        "barrier_coping_plan": values.get("barrier_coping_plan"),
    }
    # The extractor can omit a one-off frequency even when the user stated it
    # explicitly. Recover only that source-bound shape before readiness; a
    # bare date such as “明天下午六点” remains incomplete and cannot create a
    # goal by inference.
    required_plan = recover_one_time_frequency(required_plan, messages)
    if required_plan.get("frequency_rule") is not None and values.get("frequency_rule") is None:
        values["frequency_rule"] = required_plan["frequency_rule"]
    proposal_omitted = (not proposal_present or proposal == {})
    # A non-empty proposal can also be unusable when the extractor combines
    # an assistant suggestion with the user's selected activity. Recover only
    # in that narrow case: if its activity quote is source-grounded, an invalid
    # selection/direction quote remains fail-closed and must be corrected by a
    # new user turn. This repairs the live shape ``和朋友玩鬼抓人`` for a user
    # who chose ``和马哥...`` without accepting an invented activity.
    proposal_activity = proposal.get("activity_quote") if isinstance(proposal, dict) else None
    proposal_activity_grounded = bool(
        isinstance(proposal_activity, str) and proposal_activity.strip()
        and any(proposal_activity.strip() in (message.content or "")
                for message in messages if message.role in {"user", "assistant"})
    )
    selection_text = proposal.get("selection_quote") if isinstance(proposal, dict) else None
    selection_is_user_detail = bool(
        isinstance(selection_text, str) and selection_text.strip()
        and any(selection_text.strip() in (message.content or "") for message in messages if message.role == "user")
        and not re.search(r"我(?:选择|选|决定|打算|愿意|想)|那就|就按", selection_text)
        and (len(selection_text.strip()) >= 8 or re.search(
            r"今天|明天|后天|上午|中午|下午|晚上|点|分钟|小时|地点|时间|频率", selection_text))
    )
    proposal_direction_invalid = bool(
        isinstance(proposal, dict)
        and proposal.get("goal_kind") == "primary"
        and not (
            isinstance(proposal.get("long_term_direction"), str)
            and proposal.get("long_term_direction", "").strip()
            and isinstance(proposal.get("direction_quote"), str)
            and proposal.get("direction_quote", "").strip()
            and any(
                proposal["direction_quote"].strip() in (message.content or "")
                for message in messages if message.role == "user"
            )
        )
    )
    recoverable_proposal = (
        proposal_omitted or not proposal_activity_grounded
        or selection_is_user_detail or proposal_direction_invalid
    )
    if (evidence is None and recoverable_proposal
            and not missing_plan_fields(required_plan)):
        recovered = recover_goal_proposal(messages, activity)
        if recovered and isinstance(proposal, dict) and proposal.get("goal_kind") == "primary":
            # Preserve a source-validated long-term direction from the raw
            # extractor proposal while replacing only its unusable activity
            # quote.  A short-choice recovery must not silently demote a
            # primary goal to a secondary one.
            direction = proposal.get("long_term_direction")
            direction_quote = proposal.get("direction_quote")
            if (isinstance(direction, str) and direction.strip()
                    and isinstance(direction_quote, str) and direction_quote.strip()
                    and any(direction_quote.strip() in (message.content or "")
                            for message in messages if message.role == "user")):
                recovered = {**recovered, "goal_kind": "primary",
                             "long_term_direction": direction if direction in direction_quote else direction_quote,
                             "direction_quote": direction_quote}
        evidence = proposal_evidence(recovered, messages, activity) if recovered else None
    if not evidence:
        return blocked("proposal_evidence_missing", "没有找到用户明确选择该活动的可核对证据")
    # The extractor may decorate the chosen activity with an assistant's
    # wording. Persist the source-backed user choice, not an expanded activity
    # the user never selected. Schedule/location keep their separate fields.
    activity = evidence.get("source_activity") or activity
    values["activity_content"] = activity
    # Router step output is advisory evidence, not the only way a goal may be
    # created. A complete, source-validated goal proposal can arrive while a
    # router response omits one step (as happened in live DeepSeek runs). Do
    # not create from a mere suggestion: proposal_evidence still requires a
    # user's explicit choice and literal source quote.
    router_complete = {"activity_selected", "values_or_intention_explored"}.issubset(completed_steps)
    creation_reason = "router_and_extractor_evidence"
    if not router_complete:
        creation_reason = "extractor_evidence_fallback_router_step_missing"
    goal_id = await create_goal(db, user_id=user_id, title=activity,
                                conversation_id=conversation.id)
    await save_goal_details(db, goal_id, evidence)
    cycle_id = await start_cycle(db, user_id=user_id, goal_id=goal_id,
                                 conversation_id=conversation.id)
    plans = schema.tables["module_two_record"]
    plan_id = new_id()
    await db.execute(insert(plans), {"id": plan_id, "goal_id": goal_id, "version_no": 1,
                                    "timezone": "Asia/Shanghai", **values})
    await save_plan_context(db, plan_id, data.get("plan_context"), messages)
    freshness = dict((state["memory"] or {}).get("module_extraction_freshness") or {})
    freshness["module_2"] = {
        "assistant_message_id": assistant_message_id,
        "cycle_id": cycle_id,
    }
    await db.execute(update(runtime).where(runtime.c.conversation_id == conversation.id).values(
        active_goal_id=goal_id, active_cycle_id=cycle_id,
        last_transition_reason="agent_created_goal_from_dialogue",
        memory={**(state["memory"] or {}), "module_extraction_freshness": freshness},
        row_version=state["row_version"] + 1, updated_at=now()))

    assistant_position = select(ConversationMessage.position).where(
        ConversationMessage.id == assistant_message_id).scalar_subquery()
    user_message_id = (await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == "user",
        ConversationMessage.position == assistant_position - 1,
    ))).scalar_one_or_none()
    await db.execute(insert(schema.tables["ai_decision_logs"]), {
        "conversation_id": conversation.id,
        "turn_id": str(assistant_message_id),
        "goal_id": goal_id,
        "cycle_id": cycle_id,
        "module_name": "module_2",
        "decision_type": "agent_goal_created",
        "decision_value": {"activity_content": activity,
                           "router_completed_steps": list(completed_steps),
                           "creation_rule": creation_reason, "goal_kind": evidence["goal_kind"]},
        "reason_summary": "用户在 M2 对话中明确选择具体活动，由 Agent 创建目标草稿。",
        "evidence_message_ids": [user_message_id] if user_message_id else [],
    })
    if diagnostics is not None:
        diagnostics["goal_creation"] = {
            "status": "created", "reason_code": creation_reason,
            "goal_id": goal_id, "cycle_id": cycle_id,
        }
    return {"goal_id": goal_id, "cycle_id": cycle_id, "plan_id": plan_id}


async def persist_record(maker, *, module, user_id, data, cycle_id):
    table = schema.tables[TABLES[module]]
    async with maker() as db:
        profiles = schema.tables["user_profile"]
        await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
        values = record_values(module, data)
        messages, source_state = [], None
        if module in {"module_2", "module_3", "module_4"} and data.get("_source_session_id"):
            from .goal_contract import evidence_messages, capture_activities
            conversation, source_state = await runtime_for(db, data["_source_session_id"])
            if (not conversation or conversation.subject_id != user_id or not source_state
                or source_state["active_cycle_id"] != cycle_id or source_state["current_module"] != module
                or (source_state["memory"] or {}).get("sandbox_mode") == "true" or source_state["flow_status"] in {"completed", "paused"}):
                return None
            messages = await evidence_messages(db, conversation.id, user_id)
            if not messages or messages[-1].id != data.get("_source_assistant_message_id"):
                return None
            if module in {"module_2", "module_4"}:
                await capture_activities(db, user_id=user_id, conversation=conversation, state=source_state,
                    raw=data.get("activity_observations"), messages=messages, corrections=data.get("activity_corrections"))
        if module == "module_1":
            scope = table.c.user_id == user_id
            defaults = {"user_id": user_id}
        else:
            cycles, goals = schema.tables["pa_cycles"], schema.tables["pa_goals"]
            cycle = (await db.execute(select(cycles).join(goals, goals.c.id == cycles.c.goal_id).where(
                cycles.c.id == cycle_id, goals.c.user_id == user_id))).mappings().one_or_none()
            if not cycle or cycle["status"] in {"completed", "cancelled"}:
                return None
            # Late extraction must not amend a confirmed execution plan, or
            # recreate the unique review row after that cycle has been closed.
            if module in {"module_2", "module_3"} and cycle["status"] != "planning":
                return None
            if module == "module_4":
                scope, defaults = table.c.cycle_id == cycle_id, {"cycle_id": cycle_id}
            else:
                scope, defaults = table.c.goal_id == cycle["goal_id"], {"goal_id": cycle["goal_id"]}
                if module == "module_2":
                    defaults["timezone"] = "Asia/Shanghai"
                if module == "module_3":
                    if not cycle["module_two_record_id"]:
                        return None
                    defaults["module_two_record_id"] = cycle["module_two_record_id"]
                    scope = scope & (table.c.module_two_record_id == cycle["module_two_record_id"])
        existing = (await db.execute(select(table).where(scope, table.c.record_status == "draft")
            .order_by(table.c.created_at.desc()).limit(1))).mappings().one_or_none()
        if module == "module_1" and existing and isinstance(data.get("m1_contract"), dict):
            # M1 facts remain a fresh snapshot, but already authenticated
            # education/understanding/consent evidence must not disappear just
            # because a later assistant turn omitted one topic.  Reconcile
            # only against the same session's owned transcript and the exact
            # current assistant boundary; merge_verified_evidence itself
            # validates every old role/turn/literal reference and withdrawals.
            from .m1_contract import contract_for, merge_verified_evidence
            contract = data["m1_contract"]
            source_session = contract.get("session_id")
            conversation, source_state = await runtime_for(db, source_session) if source_session else (None, None)
            assistant_id = contract.get("assistant_message_id")
            rows = []
            if conversation and conversation.subject_id == user_id:
                rows = list((await db.execute(select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conversation.id
                ).order_by(ConversationMessage.position, ConversationMessage.id))).scalars())
            if (rows and assistant_id is not None and rows[-1].id == assistant_id):
                turns = [(row.role, row.content) for row in rows if row.content]
                merged_contract = merge_verified_evidence(
                    contract_for(existing), contract, turns, session_id=source_session)
                if merged_contract is not contract:
                    data = {**data, "m1_contract": merged_contract}
                    values["event_experience"] = {
                        **(values.get("event_experience") if isinstance(values.get("event_experience"), dict) else {}),
                        "schema_version": 2, "_m1_contract": merged_contract,
                    }
                    values["ba_explanation_status"] = (
                        "explained" if "ba_education_completed" in merged_contract.get("completed_steps", [])
                        else "not_recorded")
                    values["goal_setting_willingness"] = (
                        "willing" if "goal_setting_consent" in merged_contract.get("completed_steps", [])
                        else "unknown")
        if module == "module_2" and existing:
            values = _synchronize_duration_text(values, existing)
        # A short affirmative turn confirms the card just displayed. The
        # extractor still runs over the full transcript, but it may rewrite
        # narrative fields or re-parse an already-known date. Preserve the
        # executable plan fields only when the immediately preceding assistant
        # card has a complete, verified structured marker. An affirmative
        # utterance cannot freeze an empty draft or a card that was never
        # verified as containing the contract.
        if module in {"module_2", "module_3"} and existing and messages:
            latest_user = next((m for m in reversed(messages) if m.role == "user"), None)
            if latest_user:
                from .dialogue_confirmation import (affirmative,
                    confirmation_fields_complete, confirmation_marker_matches)
                clean = re.sub(r"[\s，。！!,.、~～]", "", latest_user.content)
                previous_assistant = next((m for m in reversed(messages)
                    if m.position < latest_user.position and m.role == "assistant"), None)
                marker = (source_state["memory"] or {}).get("dialogue_draft", {}) if source_state else {}
                confirmation_only = (
                    affirmative(latest_user.content)
                    and not re.search(r"改成|换成|调整|修改|但是|不过|如果|除非|重新安排|不做了|先不", clean)
                    and previous_assistant is not None)
                if module == "module_3":
                    # M3 must have a complete, verified recording card before
                    # a confirmation-only extraction may preserve it.  This
                    # prevents an empty JSON plan or an unverified summary
                    # from being frozen by a short "好的" turn.
                    confirmation_only = confirmation_only and confirmation_fields_complete(module, existing) and confirmation_marker_matches(
                        module, marker, existing, cycle_id=cycle_id,
                        preceding_assistant_id=previous_assistant.id)
                else:
                    # Keep the established M2 compatibility path: its plan
                    # fields were historically preserved across an affirmative
                    # turn. The final M2 dialogue gate still validates the
                    # structured marker and executable card before committing.
                    confirmation_only = confirmation_only and confirmation_fields_complete(module, existing)
                if confirmation_only:
                    fields = (("pa_understanding_status", "pa_willingness_status", "core_values",
                               "core_values_impact", "activity_content", "schedule_text",
                               "scheduled_start_at", "timezone", "location", "duration_minutes",
                               "frequency_rule", "companion", "potential_barriers",
                               "barrier_coping_plan") if module == "module_2" else
                              ("record_requirement", "negotiated_record_plan", "feedback_mechanism"))
                    for field in fields:
                        if field in existing:
                            values[field] = existing[field]
        if module == "module_4":
            from .m4_contract import normalize, cycle_messages
            # No trusted source session -> no model-controlled confirmation evidence.
            if not messages or source_state is None:
                return None
            messages = await cycle_messages(db, messages)
            values = normalize(data, messages, session_id=data["_source_session_id"], cycle_id=cycle_id,
                assistant_message_id=data["_source_assistant_message_id"], existing=existing,
                cycle_status=cycle["status"])
            progress, rt = schema.tables["pa_cycle_progress"], schema.tables["conversation_runtime_states"]
            await db.execute(update(progress).where(progress.c.cycle_id == cycle_id).values(
                module_4_scenario=values["scenario_type"], module_4_steps=values["phase_c"]["_m4_contract"]["completed_steps"],
                row_version=progress.c.row_version + 1, updated_at=now()))
            await db.execute(update(rt).where(rt.c.active_cycle_id == cycle_id, rt.c.current_module == "module_4").values(
                last_transition_reason="m4_snapshot_updated", row_version=rt.c.row_version + 1))
            if values.get("scenario_type") in {"A", "B", "C"} and values.get("execution_result"):
                # Waiting ends when this cycle has verified execution feedback,
                # including an honest report of not starting. Review completion
                # is still governed by the separate ABC/education/decision gates.
                await db.execute(update(cycles).where(cycles.c.id == cycle_id,
                    cycles.c.status == "waiting_execution").values(status="reviewing"))
                await db.execute(update(rt).where(rt.c.active_cycle_id == cycle_id,
                    rt.c.current_module == "module_4", rt.c.flow_status == "waiting_execution").values(
                        flow_status="active"))
        if existing:
            if module == "module_1" and "m1_contract" in data:
                m1, rt = schema.tables["user_module_one_state"], schema.tables["conversation_runtime_states"]
                await db.execute(update(m1).where(m1.c.user_id == user_id,
                    m1.c.status != "completed").values(completed_steps=data["m1_contract"]["completed_steps"],
                    row_version=m1.c.row_version + 1, updated_at=now()))
                owned_conversations = select(Conversation.id).where(Conversation.subject_id == user_id)
                await db.execute(update(rt).where(rt.c.conversation_id.in_(owned_conversations),
                    rt.c.current_module == "module_1").values(last_transition_reason="m1_snapshot_updated",
                    row_version=rt.c.row_version + 1))
            if module == "module_2" and any(existing[k] != v for k, v in values.items()):
                # A changed plan invalidates the old card-completion evidence.
                progress, rt = schema.tables["pa_cycle_progress"], schema.tables["conversation_runtime_states"]
                row = (await db.execute(select(progress).where(progress.c.cycle_id == cycle_id))).mappings().one_or_none()
                if row:
                    await db.execute(update(progress).where(progress.c.cycle_id == cycle_id).values(
                        module_2_steps=[s for s in row["module_2_steps"] if s not in {"activity_selected", "pa_card_completed"}],
                        row_version=progress.c.row_version + 1, updated_at=now()))
                await db.execute(update(rt).where(rt.c.active_cycle_id == cycle_id, rt.c.current_module == "module_2").values(
                    last_transition_reason="plan_changed_requires_review", row_version=rt.c.row_version + 1))
                await db.execute(insert(schema.tables["ai_decision_logs"]), {
                    "turn_id": "plan-edit-" + existing["id"],
                    "goal_id": cycle["goal_id"], "cycle_id": cycle_id, "module_name": module,
                    "decision_type": "plan_evidence_invalidated", "decision_value": {
                        "record_id": existing["id"], "changed_fields": sorted(values),
                        "revoked_steps": ["activity_selected", "pa_card_completed"]}})
            await db.execute(update(table).where(table.c.id == existing["id"]).values(**values, updated_at=now()))
            record_id = existing["id"]
        else:
            record_id = new_id()
            if "version_no" in table.c:
                version_scope = table.c.user_id == user_id if module == "module_1" else table.c.goal_id == defaults["goal_id"]
                defaults["version_no"] = int((await db.execute(select(func.max(table.c.version_no)).where(version_scope))).scalar_one() or 0) + 1
            await db.execute(insert(table), {"id": record_id, **defaults, **values})
        if module == "module_4" and messages:
            from .goal_contract import save_review_followup
            await save_review_followup(db, record_id, data.get("review_followup"), messages,
                values.get("review_decision"), decision_evidence=values.get("phase_c", {}).get(
                    "_m4_contract", {}).get("evidence", {}).get("decision_quote"))
        if module == "module_2" and messages:
            from .goal_contract import save_plan_context, proposal_evidence, save_goal_details
            await save_plan_context(db, record_id, data.get("plan_context"), messages)
            evidence = proposal_evidence(data.get("goal_proposal"), messages, values.get("activity_content", ""))
            if evidence:
                await save_goal_details(db, defaults["goal_id"], evidence)
        if (source_state is not None and module in FRESHNESS_FIELDS
                and set(values).intersection(FRESHNESS_FIELDS[module])):
            # This marker is the durable bridge between the source-validated
            # extraction above and record_steps.  It is deliberately written
            # only in this transaction, after all record/follow-up writes
            # have succeeded; an exception leaves the previous turn's marker
            # stale, which makes the later readiness calculation fail closed.
            rt = schema.tables["conversation_runtime_states"]
            freshness = dict((source_state["memory"] or {}).get("module_extraction_freshness") or {})
            freshness[module] = {
                "assistant_message_id": data["_source_assistant_message_id"],
                "cycle_id": cycle_id,
            }
            await db.execute(update(rt).where(rt.c.conversation_id == source_state["conversation_id"]).values(
                memory={**(source_state["memory"] or {}), "module_extraction_freshness": freshness},
                row_version=rt.c.row_version + 1, updated_at=now()))
        await db.commit()
        return record_id


async def clinical_context(maker, user_id, session_id):
    lines = []
    async with maker() as db:
        context_conversation, state = await runtime_for(db, session_id) if session_id else (None, None)
        one = schema.tables["module_one_record"]
        m1 = schema.tables["user_module_one_state"]
        completion = (await db.execute(select(m1).where(m1.c.user_id == user_id))).mappings().one_or_none()
        if state and state["current_module"] == "module_1":
            from .m1_contract import contract_for, dialogue_status
            pending = (await db.execute(select(one).where(one.c.user_id == user_id,
                one.c.record_status == "draft").order_by(one.c.created_at.desc()).limit(1))).mappings().one_or_none()
            contract = contract_for(pending)
            if contract.get("session_id") == session_id:
                lines.append("M1 当前契约状态（优先尊重已核对证据；用户本轮纠正优先）：" + json.dumps(
                    dialogue_status(contract), ensure_ascii=False))
                lines.append("M1对话节奏：已经明确的事实、总结认可、BA理解和目标意愿不要反复索取。"
                    "后台证据引用校验失败不代表用户没回答，先查看对话原文。只有真实未谈到的内容才补问；"
                    "如果缺的是教育内容，就补充相应解释，不重新询问同意。已记录目标意愿时不要再问是否愿意。"
                    "不要提前宣称已切换模块，也不要为推进而在M1讨论具体活动。")
                lines.append("M1 待核对的事实草稿（不是已经确认的事实，最新用户纠正优先）：" + json.dumps({
                    k: pending[k] for k in ("chief_complaint", "trigger_situation", "coping_behavior",
                        "coping_consequence", "functional_chain_summary", "attempted_relief_methods")}, ensure_ascii=False))
        if completion and completion["confirmed_formulation_id"]:
            record = (await db.execute(select(one).where(one.c.id == completion["confirmed_formulation_id"], one.c.user_id == user_id))).mappings().one_or_none()
            if record:
                lines.append("已确认的问题理解：" + (record["functional_chain_summary"] or record["chief_complaint"] or ""))
        elif completion and completion["completion_source"] == "legacy_imported":
            lines.append("用户已在旧系统完成 M1，继续目标设定，不要求重做或补确认。旧理解记录缺少确认依据，不可冒充已确认事实。")
        if state and state["active_goal_id"]:
            goal = await owned_goal(db, user_id, state["active_goal_id"])
            lines.append("本段聊天明确选择的目标：" + goal["title"])
            from .goal_contract import public_goal_details, public_activities
            details = (await public_goal_details(db, user_id)).get(goal["id"], {"goal_kind": "unclassified", "long_term_direction": None})
            lines.append("目标分类（分类与执行频率独立，历史未分类不得推测）：" + json.dumps(details, ensure_ascii=False))
            activities = await public_activities(db, user_id, conversation_id=context_conversation.id)
            if activities:
                lines.append("本聊天用户自述活动（idea只是想法，不是目标；未关联记录不能算作当前目标完成）：" + json.dumps(activities[:12], ensure_ascii=False))
            cycles = schema.tables["pa_cycles"]
            cycle = (await db.execute(select(cycles).where(cycles.c.id == state["active_cycle_id"], cycles.c.goal_id == goal["id"]))).mappings().one_or_none()
            if cycle and cycle["module_two_record_id"]:
                plans = schema.tables["module_two_record"]
                plan = (await db.execute(select(plans).where(plans.c.id == cycle["module_two_record_id"], plans.c.goal_id == goal["id"]))).mappings().one_or_none()
                if plan:
                    lines.append("本周期已确认计划（不得混用其他目标）：" + json.dumps({k: plan[k] for k in
                        ("activity_content", "schedule_text", "location", "duration_minutes", "companion", "potential_barriers", "barrier_coping_plan")}, ensure_ascii=False))
            if state["current_module"] == "module_4" and cycle:
                from .m4_contract import contract_for
                reviews = schema.tables["module_four_record"]
                review = (await db.execute(select(reviews).where(reviews.c.cycle_id == cycle["id"]))).mappings().one_or_none()
                if review:
                    contract = contract_for(review)
                    lines.append("当前周期M4核对状态（草稿不是已确认事实，最新纠正优先；不得重复用旧周期证据）：" + json.dumps({
                        "scenario_type": review["scenario_type"], "chain_confirmation_status": review["chain_confirmation_status"],
                        "missing_fields": contract.get("missing_fields", ["m4_evidence_refresh"]),
                        "completed_steps": contract.get("completed_steps", [])}, ensure_ascii=False))
        elif state and state["current_module"] != "module_1":
            lines.append("本聊天尚未绑定目标。可以与用户讨论新目标，但不得自行采用最新计划；只有用户明确选择具体活动后，才由 Agent 流程创建目标。")
        for memory in await active_memories(db, user_id=user_id):
            lines.append(f"长期记忆（{memory['confirmation_status']} / {memory['source_kind']}）：{memory['content']}")
    return lines
