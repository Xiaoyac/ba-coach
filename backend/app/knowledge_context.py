"""Task-scoped, read-only facts for the mediator; never a second user profile."""
from sqlalchemy import select

from .database_v2_schema import metadata
from .v2_workflow import runtime_for


# One-way aliases for existing storage columns; no duplicate business fields.
# schedule_text deliberately remains source text, not a structured time.
FIELD_ALIASES = {
    "activity_content": "target_activity_content",
    "duration_minutes": "target_activity_duration_minutes",
    "functional_chain_summary": "action_state_relationship_summary",
}
TASK_FIELDS = {
    "general": {},
    "m1_question_purpose": {"module_one_record": ("chief_complaint",)},
    "m1_relationship": {"module_one_record": (
        "coping_behavior", "coping_consequence", "trigger_situation", "distress_frequency")},
    "m1_ba_personalization": {"module_one_record": (
        "functional_chain_summary", "coping_behavior", "coping_consequence")},
    "m2_activity": {"module_two_record": ("activity_content", "pa_understanding_status")},
    "m2_values": {"module_two_record": (
        "pa_willingness_status", "core_values", "core_values_impact")},
    "m2_burden": {"module_two_record": (
        "activity_content", "potential_barriers", "barrier_coping_plan", "difficulty_rating")},
    "m3_recording_purpose": {"module_three_record": ("record_requirement", "recording_status"),
        "module_two_record": ("activity_content",)},
    "m3_recording_concern": {"module_three_record": (
        "record_requirement", "recording_status", "acceptance_status", "acceptance_feeling", "negotiated_record_plan")},
    "m3_reminder": {"module_three_record": (
        "reminder_enabled", "reminder_rule", "feedback_mechanism")},
    "m4_abc": {"module_four_record": ("phase_a", "phase_b", "phase_c")},
    "m4_education": {"module_four_record": (
        "abc_chain_summary", "scenario_type", "phase_a", "phase_b", "phase_c", "ba_reeducation_content")},
    "m4_difficulty": {"module_four_record": (
        "difficulty_description", "phase_a", "phase_b", "phase_c", "next_coping_strategy")},
}
KNOWLEDGE_TASKS = tuple(TASK_FIELDS)


def valid_knowledge_task(task, module):
    return task in TASK_FIELDS and (task == "general" or module == f"module_{task[1]}")


async def read_recording_state(db, user_id, runtime):
    """Only the recording arrangement currently committed to the owned cycle."""
    result = {"recording_status":"unknown", "recording_decision_scope":None}
    cycles, goals, records = (metadata.tables[name] for name in
        ("pa_cycles", "pa_goals", "module_three_record"))
    row = (await db.execute(select(records).join(cycles,
        (cycles.c.module_three_record_id == records.c.id)
        & (cycles.c.module_two_record_id == records.c.module_two_record_id)
        & (cycles.c.goal_id == records.c.goal_id)).join(goals, goals.c.id == cycles.c.goal_id).where(
        cycles.c.id == runtime.get("active_cycle_id"), goals.c.id == runtime.get("active_goal_id"),
        goals.c.user_id == user_id, records.c.record_status == "confirmed"))).mappings().one_or_none()
    if row:
        from .m3_contract import contract_for
        decision = (contract_for(row).get("evidence") or {}).get("decision") or {}
        result.update(recording_status=row["recording_status"], recording_decision_scope=decision.get("scope"))
    return result


async def read_previous_recording_arrangement(db, user_id, runtime, *, conversation_id=None):
    """Sourced historical context only; never grants consent to the active cycle.

    A new goal or plan deliberately has no current recording arrangement. Keep
    that business state intact while allowing M3 to acknowledge a previously
    expressed preference, including a refusal of just the activity record.
    """
    from .evidence_quotes import literal_span
    from .goal_scope import goal_filter, isolated
    from .m3_contract import RECORDING_SCOPES, contract_for
    from .models import Conversation, ConversationMessage

    cycles, goals, records, plans = (metadata.tables[name] for name in
        ("pa_cycles", "pa_goals", "module_three_record", "module_two_record"))
    is_isolated = await isolated(db, user_id)
    if is_isolated and conversation_id is None:
        return None
    if conversation_id is not None and not await db.scalar(select(Conversation.id).where(
            Conversation.id == conversation_id, Conversation.subject_id == user_id)):
        return None
    owned_goals = await goal_filter(db, user_id, conversation_id)
    current = (await db.execute(select(cycles).join(goals,
        goals.c.id == cycles.c.goal_id).join(plans,
        (plans.c.id == cycles.c.module_two_record_id) & (plans.c.goal_id == goals.c.id)).where(
        cycles.c.id == runtime.get("active_cycle_id"), goals.c.id == runtime.get("active_goal_id"),
        owned_goals))).mappings().one_or_none()
    if not current:
        return None

    query = (select(records, cycles.c.id.label("historical_cycle_id"),
        plans.c.activity_content.label("historical_activity"),
        ConversationMessage.id.label("decision_message_id"),
        ConversationMessage.content.label("decision_content"),
        ConversationMessage.created_at.label("decision_created_at"),
        ConversationMessage.position.label("decision_position"),
        Conversation.id.label("decision_conversation_id"),
        Conversation.session_id.label("decision_session_id"))
        .select_from(records)
        .join(cycles, (cycles.c.module_three_record_id == records.c.id)
              & (cycles.c.module_two_record_id == records.c.module_two_record_id)
              & (cycles.c.goal_id == records.c.goal_id))
        .join(goals, goals.c.id == records.c.goal_id)
        .join(plans, (plans.c.id == records.c.module_two_record_id)
              & (plans.c.goal_id == records.c.goal_id))
        .join(ConversationMessage, ConversationMessage.id == records.c.confirmation_message_id)
        .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .where(owned_goals, Conversation.subject_id == user_id,
               ConversationMessage.role == "user", records.c.record_status == "confirmed",
               records.c.recording_status.in_(["accepted", "declined"]),
               cycles.c.id != current["id"])
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc(),
                  cycles.c.created_at.desc(), cycles.c.id.desc()))
    if is_isolated:
        # An administrator's other test conversation is not personal history,
        # even when both conversations use the same account/profile.
        query = query.where(Conversation.id == conversation_id)
    if current["module_three_record_id"]:
        # A deliberately inherited same-plan arrangement is already current.
        query = query.where(records.c.id != current["module_three_record_id"])
    for row in (await db.execute(query)).mappings():
        contract = contract_for(row)
        evidence = contract.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        decision = evidence.get("decision")
        decision = decision if isinstance(decision, dict) else {}
        status = row["recording_status"]
        if (contract.get("verified") is not True
                or contract.get("recording_status") != status
                or contract.get("session_id") != row["decision_session_id"]
                or contract.get("cycle_id") != row["historical_cycle_id"]
                or type(decision.get("message_id")) is not int
                or decision["message_id"] != row["decision_message_id"]
                or decision.get("role") != "user" or decision.get("status") != status
                or decision.get("scope") not in RECORDING_SCOPES
                or not isinstance(decision.get("quote"), str)):
            continue
        quote = literal_span(decision["quote"], row["decision_content"])
        if not quote:
            continue

        # An accepted historical plan is optional context. Recheck its source
        # separately rather than trusting a free-text field or a later proposal.
        plan_text = None
        plan_evidence = evidence.get("plan")
        saved_plan = row["negotiated_record_plan"]
        if (status == "accepted" and isinstance(plan_evidence, dict)
                and isinstance(saved_plan, dict)
                and type(plan_evidence.get("message_id")) is int
                and isinstance(plan_evidence.get("quote"), str)
                and saved_plan.get("text") == plan_evidence["quote"]):
            plan_message = (await db.execute(select(ConversationMessage).where(
                ConversationMessage.id == plan_evidence["message_id"],
                ConversationMessage.conversation_id == row["decision_conversation_id"],
                ConversationMessage.role.in_(["user", "assistant"]),
                ConversationMessage.position <= row["decision_position"]))).scalar_one_or_none()
            if plan_message:
                plan_text = literal_span(plan_evidence["quote"], plan_message.content)
        return {"scope": "historical", "applies_to_current_cycle": False,
            "record_id": row["id"], "goal_id": row["goal_id"],
            "cycle_id": row["historical_cycle_id"], "plan_id": row["module_two_record_id"],
            "activity_content": row["historical_activity"],
            "recording_status": status, "recording_decision_scope": decision["scope"],
            "decision_quote": quote, "recording_plan": plan_text,
            "source_message_id": row["decision_message_id"],
            "source_created_at": row["decision_created_at"].isoformat()}
    return None


async def load_recording_state(maker, user_id, session_id):
    async with maker() as db:
        conversation, runtime = await runtime_for(db, session_id)
        if not conversation or conversation.subject_id != user_id or not runtime:
            return {"recording_status":"unknown", "recording_decision_scope":None}
        return await read_recording_state(db, user_id, runtime)


async def assemble_knowledge_context(maker, user_id, session_id, *, module=None, task="general"):
    """Read only a declared task's fields, scoped to the owned active cycle.

    Tasks select a data projection, never a coaching step or completion rule.
    General explanations need no personal reads; invalid tasks fail closed.
    """
    if not valid_knowledge_task(task, module):
        raise ValueError("Invalid knowledge context task")
    result = {"task": task, "goal_id": None, "cycle_id": None, "facts": []}
    if task == "general":
        return result
    requested = TASK_FIELDS[task]
    async with maker() as db:
        conversation, state = await runtime_for(db, session_id)
        if not conversation or conversation.subject_id != user_id or not state:
            return result
        goal_id, cycle_id = state["active_goal_id"], state["active_cycle_id"]
        result.update(goal_id=goal_id, cycle_id=cycle_id)

        def fact(table, row):
            if not row:
                return
            values = {FIELD_ALIASES.get(k, k): row[k] for k in requested[table] if k in row}
            if table == "module_three_record" and "reminder_enabled" in values:
                # The legacy recording row defaults false and is not synced
                # to browser subscriptions. It cannot establish current user
                # opt-out/authorization. Preserve its meaning as a snapshot.
                values["reminder_enabled"] = None
                values["reminder_authorization_status"] = "not_loaded"
            if table == "module_three_record" and "recording_status" in requested[table]:
                from .m3_contract import contract_for
                decision = (contract_for(row).get("evidence") or {}).get("decision")
                if isinstance(decision, dict):
                    # Preserve what was refused; declining the activity table
                    # must not imply cancelling the separate daily summary.
                    values["recording_decision"] = {key:decision.get(key)
                        for key in ("scope", "quote", "message_id")}
            result["facts"].append({
                "source": table, "record_id": str(row.get("id") or row.get("user_id")),
                "version": row.get("version_no", row.get("row_version")),
                "confirmation": row.get("chain_confirmation_status", row.get("confirmation_status")),
                "status": row.get("record_status", row.get("status")),
                "source_kind": row.get("source_kind", row.get("source_type")),
                "source_message_id": row.get("source_message_id", row.get("confirmation_message_id")),
                "goal_id": row.get("goal_id"), "cycle_id": row.get("cycle_id"),
                "values": values,
            })

        if "module_one_record" in requested:
            table = metadata.tables["module_one_record"]
            # Preserve the actual confirmation state, including recent drafts.
            from .v2_workflow import fresh_m1, m1_draft
            from .goal_scope import isolated, m1_record
            if await isolated(db, user_id):
                row = await m1_record(db, user_id, conversation.id)
            elif fresh_m1(state):
                row = await m1_draft(db, user_id, state=state, session_id=session_id)
            else:
                row = (await db.execute(select(table).where(
                    table.c.user_id == user_id, table.c.record_status != "superseded"
                ).order_by(table.c.version_no.desc()).limit(1))).mappings().one_or_none()
            fact(table.name, row)
            return result

        cycles, goals = metadata.tables["pa_cycles"], metadata.tables["pa_goals"]
        cycle = (await db.execute(select(cycles).join(goals, goals.c.id == cycles.c.goal_id).where(
            cycles.c.id == cycle_id, cycles.c.goal_id == goal_id, goals.c.user_id == user_id
        ))).mappings().one_or_none()
        if not cycle:
            return result

        if "module_two_record" in requested:
            table = metadata.tables["module_two_record"]
            scope = table.c.id == cycle["module_two_record_id"]
            if module == "module_2" and cycle["status"] == "planning":
                scope = table.c.record_status == "draft"
            row = (await db.execute(select(table).where(
                table.c.goal_id == goal_id, scope
            ).order_by(table.c.version_no.desc()).limit(1))).mappings().one_or_none()
            fact(table.name, row)

        if "module_three_record" in requested:
            table = metadata.tables["module_three_record"]
            scope = table.c.id == cycle["module_three_record_id"]
            if not cycle["module_three_record_id"]:
                scope = table.c.record_status == "draft"
            row = (await db.execute(select(table).where(
                table.c.goal_id == goal_id,
                table.c.module_two_record_id == cycle["module_two_record_id"], scope
            ).order_by(table.c.version_no.desc()).limit(1))).mappings().one_or_none()
            fact(table.name, row)

        if "module_four_record" in requested:
            table = metadata.tables["module_four_record"]
            row = (await db.execute(select(table).where(
                table.c.cycle_id == cycle_id))).mappings().one_or_none()
            from .m4_contract import previous_attempt
            if row and await previous_attempt(db, row, conversation.id):
                row = None  # an earlier attempt's review, not this one's facts
            fact(table.name, row)
        return result
