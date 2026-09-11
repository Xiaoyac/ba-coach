"""V2 workflow authority, scoped by user and execution cycle."""
import json
from sqlalchemy import select, update, insert, func
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage
from .v2_repository import new_id, now, owned_goal, active_memories
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
    # Never let a late router overwrite a goal selection or confirmed transition.
    if state["current_module"] != module:
        return state["current_module"], state["active_cycle_id"]
    if state["flow_status"] == "completed":
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
    await db.execute(update(table).where(key).values(**{column: merged, "row_version": row["row_version"] + 1,
                                                     "updated_at": now()}))
    ready = set(allowed).issubset(merged) and (requested_target != module or module == "module_2")
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
                           "evidence_status": "router_inferred", "revoked_steps": sorted(revoked),
                           "revocation_evidence": revocation_evidence},
        "evidence_message_ids": [assistant_message_id] if assistant_message_id else [],
    })
    # Model decisions propose completion; authenticated user confirmation commits it.
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


async def persist_record(maker, *, module, user_id, data, cycle_id):
    table = schema.tables[TABLES[module]]
    async with maker() as db:
        profiles = schema.tables["user_profile"]
        await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
        values = {}
        forbidden = {"id", "user_id", "goal_id", "cycle_id", "version_no", "record_status",
                     "confirmation_status", "confirmation_message_id", "chain_confirmation_status"}
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
        if module == "module_1":
            scope = table.c.user_id == user_id
            defaults = {"user_id": user_id}
        else:
            cycles, goals = schema.tables["pa_cycles"], schema.tables["pa_goals"]
            cycle = (await db.execute(select(cycles).join(goals, goals.c.id == cycles.c.goal_id).where(
                cycles.c.id == cycle_id, goals.c.user_id == user_id))).mappings().one_or_none()
            if not cycle:
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
        if existing:
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
        await db.commit()
        return record_id


async def clinical_context(maker, user_id, session_id):
    lines = []
    async with maker() as db:
        _, state = await runtime_for(db, session_id) if session_id else (None, None)
        one = schema.tables["module_one_record"]
        m1 = schema.tables["user_module_one_state"]
        completion = (await db.execute(select(m1).where(m1.c.user_id == user_id))).mappings().one_or_none()
        if completion and completion["confirmed_formulation_id"]:
            record = (await db.execute(select(one).where(one.c.id == completion["confirmed_formulation_id"], one.c.user_id == user_id))).mappings().one_or_none()
            if record:
                lines.append("已确认的问题理解：" + (record["functional_chain_summary"] or record["chief_complaint"] or ""))
        elif completion and completion["completion_source"] == "legacy_imported":
            lines.append("用户已在旧系统完成 M1，继续目标设定，不要求重做或补确认。旧理解记录缺少确认依据，不可冒充已确认事实。")
        if state and state["active_goal_id"]:
            goal = await owned_goal(db, user_id, state["active_goal_id"])
            lines.append("本段聊天明确选择的目标：" + goal["title"])
            cycles = schema.tables["pa_cycles"]
            cycle = (await db.execute(select(cycles).where(cycles.c.id == state["active_cycle_id"], cycles.c.goal_id == goal["id"]))).mappings().one_or_none()
            if cycle and cycle["module_two_record_id"]:
                plans = schema.tables["module_two_record"]
                plan = (await db.execute(select(plans).where(plans.c.id == cycle["module_two_record_id"], plans.c.goal_id == goal["id"]))).mappings().one_or_none()
                if plan:
                    lines.append("本周期已确认计划（不得混用其他目标）：" + json.dumps({k: plan[k] for k in
                        ("activity_content", "schedule_text", "location", "duration_minutes", "companion", "potential_barriers", "barrier_coping_plan")}, ensure_ascii=False))
        elif state and state["current_module"] != "module_1":
            lines.append("本聊天尚未明确选择目标。请用户通过网页目标入口选择已有目标或新建目标，不自行采用最新计划。")
        for memory in await active_memories(db, user_id=user_id):
            lines.append(f"长期记忆（{memory['confirmation_status']} / {memory['source_kind']}）：{memory['content']}")
    return lines
