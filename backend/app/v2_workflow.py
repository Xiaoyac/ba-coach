"""V2 workflow authority, scoped by user and execution cycle."""
import json
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


async def module_extraction_is_current(db, *, conversation_id, state, module, assistant_message_id=None):
    """Whether M2/M4's source-validated extraction belongs to the latest turn."""
    if module not in {"module_2", "module_3", "module_4"}:
        return True
    freshness = (state["memory"] or {}).get("module_extraction_freshness", {}).get(module, {})
    source_message_id = freshness.get("assistant_message_id")
    if source_message_id is None or freshness.get("cycle_id") != state["active_cycle_id"]:
        return False
    latest = (await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conversation_id).order_by(
            ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
    return latest == source_message_id and (assistant_message_id is None or assistant_message_id == source_message_id)


async def record_steps(db, *, session_id, user_id, module, requested_target, steps, assistant_message_id,
                       revoked_steps=(), revocation_evidence=None):
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
        return state["current_module"], state["active_cycle_id"]
    # Never let a late router overwrite a goal selection or confirmed transition.
    if state["current_module"] != module:
        return state["current_module"], state["active_cycle_id"]
    if state["flow_status"] in {"completed", "paused"}:
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
        from .m1_contract import contract_for, missing_m1_fields
        records = schema.tables["module_one_record"]
        pending = (await db.execute(select(records).where(records.c.user_id == user_id,
            records.c.record_status == "draft").order_by(records.c.created_at.desc()).limit(1))).mappings().one_or_none()
        contract = contract_for(pending)
        fresh = (contract.get("session_id") == session_id and assistant_message_id is not None
                 and contract.get("assistant_message_id") == assistant_message_id)
        # Unlike router's accumulated suggestions, M1 evidence is a full current
        # snapshot. A failed extraction or a correction cannot reuse old gates.
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
    await db.execute(update(table).where(key).values(**{column: merged, "row_version": row["row_version"] + 1,
                                                     "updated_at": now()}))
    ready = not m1_missing and set(allowed).issubset(merged) and (requested_target != module or module in {"module_1", "module_2", "module_3", "module_4"})
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
        last_transition_reason="awaiting_record_confirmation" if ready else "discussion_required",
        row_version=rt.c.row_version + 1))
    await db.execute(insert(schema.tables["ai_decision_logs"]), {
        "conversation_id": conversation.id, "turn_id": str(assistant_message_id),
        "goal_id": state["active_goal_id"], "cycle_id": state["active_cycle_id"],
        "module_name": module, "decision_type": "step_completion",
        "decision_value": {"completed_steps": merged, "requested_target": requested_target,
                           "applied_target": module, "confirmation_required": ready,
                           "evidence_status": "extraction_quote_verified" if module == "module_1" else (
                               "current_extraction_verified" if extraction_fresh else "extraction_refresh_required"),
                           "m1_missing_fields": m1_missing if module == "module_1" else None,
                           "m4_contract_version": contract.get("version") if module == "module_4" else None,
                           "m4_missing_fields": contract.get("missing_fields", ["m4_evidence_refresh"]) if module == "module_4" else None,
                           "revoked_steps": sorted(revoked),
                           "revocation_evidence": revocation_evidence},
        "evidence_message_ids": [assistant_message_id] if assistant_message_id else [],
    })
    # Completion proposals alone cannot commit; bind a real user reply to evidence.
    from .dialogue_confirmation import advance_from_dialogue
    advanced = await advance_from_dialogue(db, session_id=session_id, user_id=user_id,
        assistant_message_id=assistant_message_id)
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
                                           completed_steps, assistant_message_id):
    """Atomically bind a new goal only after Router and extraction agree.

    The web client cannot call this path. Router evidence alone is insufficient
    because an assistant may merely list candidates; extracted activity content
    alone is also insufficient because it may come from an unchosen suggestion.
    """
    if not {"activity_selected", "values_or_intention_explored"}.issubset(completed_steps):
        return None
    values = record_values("module_2", data)
    activity = values.get("activity_content")
    if not isinstance(activity, str) or not activity.strip():
        return None
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id or not state:
        return None
    profiles, runtime = schema.tables["user_profile"], schema.tables["conversation_runtime_states"]
    await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
    state = (await db.execute(select(runtime).where(
        runtime.c.conversation_id == conversation.id).with_for_update())).mappings().one()
    if (state["current_module"] != "module_2" or state["flow_status"] == "completed"
            or state["active_goal_id"] or state["active_cycle_id"]
            or state["memory"].get("sandbox_mode") == "true"):
        return None

    from .goal_contract import evidence_messages, proposal_evidence, save_goal_details, save_plan_context
    messages = await evidence_messages(db, conversation.id, user_id)
    if not messages or messages[-1].id != assistant_message_id:
        return None
    evidence = proposal_evidence(data.get("goal_proposal"), messages, activity)
    if not evidence:
        return None
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
                           "creation_rule": "router_extractor_and_user_quote_agree", "goal_kind": evidence["goal_kind"]},
        "reason_summary": "用户在 M2 对话中明确选择具体活动，由 Agent 创建目标草稿。",
        "evidence_message_ids": [user_message_id] if user_message_id else [],
    })
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
        if module == "module_4":
            from .m4_contract import normalize, cycle_messages
            # No trusted source session -> no model-controlled confirmation evidence.
            if not messages or source_state is None:
                return None
            messages = await cycle_messages(db, messages)
            values = normalize(data, messages, session_id=data["_source_session_id"], cycle_id=cycle_id,
                assistant_message_id=data["_source_assistant_message_id"], existing=existing)
            progress, rt = schema.tables["pa_cycle_progress"], schema.tables["conversation_runtime_states"]
            await db.execute(update(progress).where(progress.c.cycle_id == cycle_id).values(
                module_4_scenario=values["scenario_type"], module_4_steps=values["phase_c"]["_m4_contract"]["completed_steps"],
                row_version=progress.c.row_version + 1, updated_at=now()))
            await db.execute(update(rt).where(rt.c.active_cycle_id == cycle_id, rt.c.current_module == "module_4").values(
                last_transition_reason="m4_snapshot_updated", row_version=rt.c.row_version + 1))
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
            await save_review_followup(db, record_id, data.get("review_followup"), messages, values.get("review_decision"))
        if module == "module_2" and messages:
            from .goal_contract import save_plan_context, proposal_evidence, save_goal_details
            await save_plan_context(db, record_id, data.get("plan_context"), messages)
            evidence = proposal_evidence(data.get("goal_proposal"), messages, values.get("activity_content", ""))
            if evidence:
                await save_goal_details(db, defaults["goal_id"], evidence)
        if source_state is not None and set(values).intersection(FRESHNESS_FIELDS[module]):
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
            from .m1_contract import contract_for
            pending = (await db.execute(select(one).where(one.c.user_id == user_id,
                one.c.record_status == "draft").order_by(one.c.created_at.desc()).limit(1))).mappings().one_or_none()
            contract = contract_for(pending)
            if contract.get("session_id") == session_id:
                lines.append("M1 当前契约状态（草稿非已确认事实；用户本轮纠正优先）：" + json.dumps({
                    "path": contract.get("path"), "milestones": contract.get("milestones"),
                    "missing_fields": contract.get("missing_fields")}, ensure_ascii=False))
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
