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
            fact(table.name, row)
        return result
