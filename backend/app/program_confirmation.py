"""Shared transaction-level confirmation; callers own authentication, locking and commit."""
import hashlib
import json
import re
from fastapi import HTTPException
from sqlalchemy import select, update, insert, func
from .database_v2_schema import metadata as schema
from .models import ConversationMessage
from .v2_repository import (now, confirm_plan, continue_reviewed_cycle, start_cycle,
    append_plan_draft, PLAN_WRITABLE_FIELDS, V2Conflict)
from .v2_workflow import TABLES, module_extraction_is_current
from .workflow_contract import MODULE_STEP_KEYS


def record_hash(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def confirmation_memory(memory):
    """Invalidate operation markers without discarding durable dialogue facts."""
    transient = {"dialogue_draft", "module_extraction_freshness", "pa_card",
        "current_transition_evidence", "last_module", "current_module", "next_module",
        "phase", "current_phase", "current_step", "module_steps"}
    return {key:value for key,value in (memory or {}).items() if key not in transient}


async def draft(db, state, user_id):
    table = schema.tables[TABLES[state["current_module"]]]
    if state["current_module"] == "module_1":
        scope = table.c.user_id == user_id
    elif state["current_module"] == "module_4":
        scope = table.c.cycle_id == state["active_cycle_id"]
    else:
        scope = table.c.goal_id == state["active_goal_id"]
        if state["current_module"] == "module_3":
            cycles = schema.tables["pa_cycles"]
            plan = (await db.execute(select(cycles.c.module_two_record_id).where(
                cycles.c.id == state["active_cycle_id"], cycles.c.goal_id == state["active_goal_id"]))).scalar_one_or_none()
            scope = scope & (table.c.module_two_record_id == plan)
    rows = (await db.execute(select(table).where(scope, table.c.record_status == "draft")
        .order_by(table.c.created_at.desc(), table.c.id.desc()))).mappings().all()
    if state["current_module"] == "module_3":
        from .m3_contract import contract_for
        return next((row for row in rows if contract_for(row).get("cycle_id") == state["active_cycle_id"]), None)
    return rows[0] if rows else None


async def current_m1_missing(db, pending, conversation_id, session_id):
    """An old ready draft must not survive a failed/newer conversation turn."""
    from .m1_contract import missing_m1_fields, contract_for
    missing = missing_m1_fields(pending, session_id)
    latest = (await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
    if latest is None or contract_for(pending).get("assistant_message_id") != latest:
        return list(dict.fromkeys([*missing, "m1_evidence_refresh"]))
    return missing


async def confirmation_readiness(
    db, *, conversation, state, user_id, session_id, pending=None,
    extraction_assistant_message_id=None, confirmation_user_message_id=None,
):
    """Evaluate the one authoritative readiness contract for confirmation.

    The chat precommit path, the program API and the reply guard must agree on
    the same evidence.  Keeping this query/evaluation in one place prevents a
    UI from reporting a record as confirmable while the transactional commit
    rejects it (or vice versa).  The result is JSON-safe and deliberately
    includes the freshness/version inputs used by the evaluator so callers
    can expose a useful diagnostic without reimplementing the checks.
    """
    module = state["current_module"]
    if pending is None:
        pending = await draft(db, state, user_id)
    extraction_fresh = None
    if module in {"module_2", "module_3", "module_4"}:
        extraction_fresh = await module_extraction_is_current(
            db, conversation_id=conversation.id, state=state, module=module,
            assistant_message_id=extraction_assistant_message_id,
            following_user_message_id=confirmation_user_message_id,
        )
    memory = state.get("memory") or {}
    marker = memory.get("dialogue_draft") if isinstance(memory, dict) else None
    marker = marker if isinstance(marker, dict) else {}
    actual_fingerprint = None
    if module == "module_2" and pending:
        # Keep this local import to avoid dialogue_confirmation ↔
        # program_confirmation import cycles while modules initialise.
        from .dialogue_confirmation import fingerprint
        actual_fingerprint = fingerprint(pending)
    m4_missing = None
    if module == "module_4" and pending:
        from .m4_contract import missing_fields
        m4_missing = missing_fields(pending, session_id=session_id,
                                     cycle_id=state["active_cycle_id"])
    from .workflow_readiness import evaluate_readiness
    readiness = evaluate_readiness(
        module, pending,
        extraction_fresh=extraction_fresh,
        summary_verified=marker.get("summary_verified") if module == "module_2" else None,
        expected_fingerprint=marker.get("fingerprint") if module == "module_2" else None,
        actual_fingerprint=actual_fingerprint,
        evidence_session_id=marker.get("session_id") if module == "module_2" else None,
        session_id=session_id,
        evidence_cycle_id=marker.get("cycle_id") if module == "module_2" else None,
        cycle_id=state.get("active_cycle_id"),
        m4_missing_fields=m4_missing,
        require_summary=module == "module_2",
        require_fingerprint=module == "module_2",
    )
    # A structurally complete draft is still only a draft until the router has
    # published the verified summary and opened the confirmation boundary.
    # Expose that phase gate here so the program API and reply guard cannot
    # accidentally promise a transition from a merely complete extraction.
    from .workflow_readiness import CONFIRMATION_WINDOW_CLOSED, REVIEW_ACTION_MISSING
    if module in {"module_2", "module_4"} and state.get("last_transition_reason") != "awaiting_record_confirmation":
        readiness["ready"] = False
        readiness["reasons"].append({
            "code": CONFIRMATION_WINDOW_CLOSED,
            "message": "当前模块尚未进入可确认状态",
        })
    review_action = None
    if module == "module_4" and pending:
        review_details = schema.tables["pa_review_details"]
        detail = (await db.execute(select(review_details).where(
            review_details.c.review_id == pending["id"]))).mappings().one_or_none()
        review_action = detail["action"] if detail else None
        allowed_actions = {1: {"continue"}, 3: {"adjust"},
                           4: {"end", "pause"}, 2: {"replace_keep", "replace_pause"}}
        valid_source = False
        quote = detail.get("source_quote") if detail else None
        if detail and detail["source_message_id"] and isinstance(quote, str) and quote.strip():
            source = (await db.execute(select(ConversationMessage).where(
                ConversationMessage.id == detail["source_message_id"],
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "user"))).scalar_one_or_none()
            valid_source = bool(source and quote in source.content)
        pause_word = r"暂停|先停|停一阵|暂时不做|先放一放"
        action_semantics_ok = review_action not in {"pause", "replace_pause"} or bool(
            detail and isinstance(quote, str) and re.search(pause_word, quote))
        if (review_action not in allowed_actions.get(pending.get("review_decision"), set())
                or not valid_source or not action_semantics_ok):
            readiness["ready"] = False
            readiness["reasons"].append({
                "code": REVIEW_ACTION_MISSING,
                "message": "复盘方向缺少聊天中的真实决定证据",
            })
    readiness["review_action"] = review_action
    if module == "module_3":
        from .m3_contract import contract_for
        evidence = contract_for(pending).get("evidence") or {}
        readiness["recording_status"] = (pending or {}).get("recording_status", "unknown")
        readiness["recording_decision_message_id"] = (evidence.get("decision") or {}).get("message_id")
    readiness["confirmation_window_open"] = readiness["ready"] if module == "module_3" else state.get("last_transition_reason") == "awaiting_record_confirmation"
    return {
        **readiness,
        "marker": marker,
        "extraction_fresh": extraction_fresh,
        "expected_fingerprint": marker.get("fingerprint"),
        "actual_fingerprint": actual_fingerprint,
        "evidence_session_id": marker.get("session_id"),
        "evidence_cycle_id": marker.get("cycle_id"),
    }



async def validate_confirmation(
    db, *, conversation, state, user_id, session_id, payload,
    extraction_assistant_message_id=None, confirmation_user_message_id=None,
):
    pending = await draft(db, state, user_id)
    if state["row_version"] != payload.row_version or not pending or pending["id"] != payload.record_id or record_hash(pending) != payload.record_hash:
        raise HTTPException(409, "记录已更新，请重新查看后确认")
    if state["current_module"] != "module_3" and state["last_transition_reason"] != "awaiting_record_confirmation":
        raise HTTPException(409, "当前模块的必要讨论尚未完成")
    module, cycle_id, goal_id = state["current_module"], state["active_cycle_id"], state["active_goal_id"]
    table = schema.tables[TABLES[module]]
    if module == "module_1":
        from .m1_contract import missing_m1_fields
        if await current_m1_missing(db, pending, conversation.id, session_id):
            raise HTTPException(409, "M1 的经历/低披露选择、理解或目标意愿证据尚未齐全，请继续讨论后刷新")
    # Use one deterministic readiness result for M2/M3/M4.  The existing
    # module-specific checks below remain for M1 and the M4 review action, but
    # all record-shape/evidence failures now carry stable reason codes.
    if module in {"module_2", "module_3", "module_4"}:
        readiness = await confirmation_readiness(
            db, conversation=conversation, state=state, user_id=user_id,
            session_id=session_id, pending=pending,
            extraction_assistant_message_id=extraction_assistant_message_id,
            confirmation_user_message_id=confirmation_user_message_id,
        )
        if not readiness["ready"]:
            codes = [item["code"] for item in readiness["reasons"]]
            messages = [item["message"] for item in readiness["reasons"]]
            raise HTTPException(409, {"message": "；".join(dict.fromkeys(messages)),
                                      "reason_codes": codes,
                                      "reasons": readiness["reasons"]})
    if module == "module_4":
        from .m4_contract import missing_fields
        if missing_fields(pending, session_id=session_id, cycle_id=cycle_id):
            raise HTTPException(409, "本轮执行、ABC核对、BA理解或后续方向的证据尚未完整，请继续讨论")
    review_action = None
    if module == "module_4":
        review_details = schema.tables["pa_review_details"]
        review_action = (await db.execute(select(review_details.c.action).where(review_details.c.review_id == pending["id"]))).scalar_one_or_none()
        allowed_actions = {1: {"continue"}, 3: {"adjust"}, 4: {"end", "pause"}, 2: {"replace_keep", "replace_pause"}}
        if review_action not in allowed_actions.get(pending["review_decision"], set()):
            raise HTTPException(409, "请在聊天中明确下一步决定，再核对复盘记录")
    return pending, review_action


async def commit_confirmation(db, *, conversation, state, user_id, pending, message, review_action, snapshot_hash, source="dialogue", boundary_message_id=None):
    """No synthetic message, commit, or store lock here; preserve caller atomicity."""
    module, cycle_id, goal_id = state["current_module"], state["active_cycle_id"], state["active_goal_id"]
    table = schema.tables[TABLES[module]]
    profiles, rt = schema.tables["user_profile"], schema.tables["conversation_runtime_states"]
    if message.role != "user" or message.conversation_id != conversation.id or conversation.subject_id != user_id:
        raise V2Conflict("确认必须来自本用户、本段聊天的真实发言")
    next_module, next_cycle, next_goal, flow = module, cycle_id, goal_id, "active"
    common = {"record_status": "confirmed", "confirmation_message_id": message.id, "updated_at": now()}
    if module == "module_1":
        await db.execute(update(table).where(table.c.id == pending["id"]).values(**common,
            confirmation_status="confirmed", goal_setting_willingness="willing", willingness_message_id=message.id))
        m1 = schema.tables["user_module_one_state"]
        await db.execute(update(m1).where(m1.c.user_id == user_id).values(status="completed",
            confirmed_formulation_id=pending["id"], completion_source="user_confirmed", evidence_status="available",
            row_version=m1.c.row_version + 1, updated_at=now()))
        await db.execute(update(profiles).where(profiles.c.uuid == user_id).values(module1_done_flag=True, updated_at=now()))
        next_module = "module_2"
    elif module == "module_2":
        await confirm_plan(db, user_id=user_id, goal_id=goal_id, cycle_id=cycle_id,
                           plan_id=pending["id"], message_id=message.id)
        progress = schema.tables["pa_cycle_progress"]
        await db.execute(update(progress).where(progress.c.cycle_id == cycle_id).values(
            module_2_steps=list(MODULE_STEP_KEYS["module_2"]),
            row_version=progress.c.row_version + 1, updated_at=now()))
        next_module = "module_3"
    elif module == "module_3":
        from .m3_contract import contract_for, missing_fields
        decision = (contract_for(pending).get("evidence") or {}).get("decision") or {}
        if (missing_fields(pending, session_id=conversation.session_id, cycle_id=cycle_id)
                or decision.get("message_id") != message.id
                or not isinstance(decision.get("quote"), str)
                or decision["quote"] not in (message.content or "")):
            raise V2Conflict("记录决定缺少本用户、本周期的真实接受或拒绝来源")
        # Acceptance/refusal is a business decision; the attitude field retains
        # its original meaning. A refusal never fabricates a recording plan.
        await db.execute(update(table).where(table.c.id == pending["id"]).values(**common))
        cycles = schema.tables["pa_cycles"]
        await db.execute(update(cycles).where(cycles.c.id == cycle_id, cycles.c.goal_id == goal_id).values(
            module_three_record_id=pending["id"], status="waiting_execution"))
        progress = schema.tables["pa_cycle_progress"]
        await db.execute(update(progress).where(progress.c.cycle_id == cycle_id).values(
            module_3_steps=list(MODULE_STEP_KEYS["module_3"]),
            row_version=progress.c.row_version + 1, updated_at=now()))
        next_module, flow = "module_3", "waiting_execution"
    else:
        # The column identifies the actual ABC acknowledgement, not
        # this separate final web confirmation (which is audit-logged).
        common["confirmation_message_id"] = pending["confirmation_message_id"]
        await db.execute(update(table).where(table.c.id == pending["id"]).values(**common,
            confirmed_at=pending["confirmed_at"] or now()))
        cycles, goals = schema.tables["pa_cycles"], schema.tables["pa_goals"]
        await db.execute(update(cycles).where(cycles.c.id == cycle_id, cycles.c.goal_id == goal_id).values(
            status="completed", completed_at=now()))
        decision = pending["review_decision"]
        if decision == 1:
            next_cycle = await continue_reviewed_cycle(db, user_id=user_id, goal_id=goal_id,
                cycle_id=cycle_id, conversation_id=conversation.id)
            successor_status = (await db.execute(select(cycles.c.status).where(
                cycles.c.id == next_cycle, cycles.c.goal_id == goal_id))).scalar_one()
            next_module = "module_3"
            flow = "waiting_execution" if successor_status == "waiting_execution" else "active"
        elif decision == 3:
            next_cycle = await start_cycle(db, user_id=user_id, goal_id=goal_id, conversation_id=conversation.id)
            # Start a new editable version, never overwrite historical plans.
            source_cycle = (await db.execute(select(cycles).where(cycles.c.id == cycle_id,
                cycles.c.goal_id == goal_id))).mappings().one()
            plans = schema.tables["module_two_record"]
            baseline = (await db.execute(select(plans).where(
                plans.c.id == source_cycle["module_two_record_id"], plans.c.goal_id == goal_id,
                plans.c.record_status == "confirmed"))).mappings().one_or_none()
            if not baseline:
                raise V2Conflict("调整计划缺少已确认的原版本")
            new_plan_id = await append_plan_draft(db, user_id=user_id, goal_id=goal_id,
                fields={key: baseline[key] for key in PLAN_WRITABLE_FIELDS})
            context_table = schema.tables["pa_plan_details"]
            baseline_context = (await db.execute(select(context_table).where(context_table.c.plan_id == baseline["id"]))).mappings().one_or_none()
            if baseline_context:
                await db.execute(insert(context_table), {"plan_id": new_plan_id,
                    **{k: baseline_context[k] for k in ("schedule_kind", "review_cadence", "difficulty", "resources")}})
            progress = schema.tables["pa_cycle_progress"]
            previous = (await db.execute(select(progress).where(progress.c.cycle_id == cycle_id))).mappings().one_or_none()
            retained = {"pa_concept_understood", "values_or_intention_explored", "activity_selected"}
            await db.execute(update(progress).where(progress.c.cycle_id == next_cycle).values(
                module_2_steps=[step for step in (previous["module_2_steps"] or []) if step in retained] if previous else []))
            next_module = "module_2"
        elif decision == 2:
            # Selection of a replacement is explicit; do not abandon other active goals.
            await db.execute(update(goals).where(goals.c.id == goal_id).values(
                status="paused", status_reason="user_requested_replacement_pause") if review_action == "replace_pause" else
                update(goals).where(goals.c.id == goal_id).values(status="active", status_reason="user_kept_goal_while_replacing_focus"))
            next_goal, next_cycle, next_module = None, None, "module_2"
        else:
            paused = review_action == "pause"
            await db.execute(update(goals).where(goals.c.id == goal_id).values(
                status="paused" if paused else "completed", closed_at=None if paused else now(),
                status_reason="user_requested_pause" if paused else "user_requested_end"))
            flow = "paused" if paused else "completed"
        from .models_business import InteractionStatus
        interaction = (await db.execute(select(InteractionStatus).where(InteractionStatus.user_id == user_id))).scalar_one_or_none()
        if not interaction:
            interaction = InteractionStatus(user_id=user_id)
            db.add(interaction)
        interaction.full_m2_m3_m4_cycle_count = int(interaction.full_m2_m3_m4_cycle_count or 0) + 1
        interaction.has_entered_closure_or_transition = True
    if cycle_id:
        # Other chats may be continuing this SAME cycle. They must not
        # keep stale module pointers or advance a completed cycle.
        from .models import Conversation
        others = select(rt.c.conversation_id, rt.c.memory).join(Conversation, Conversation.id == rt.c.conversation_id).where(
            rt.c.active_cycle_id == cycle_id, Conversation.subject_id == user_id,
            rt.c.conversation_id != conversation.id)
        other_rows = (await db.execute(others)).mappings().all()
        other_ids = [row["conversation_id"] for row in other_rows]
        if other_ids:
            for other in other_rows:
                await db.execute(update(rt).where(rt.c.conversation_id == other["conversation_id"]).values(
                    current_module=next_module if module != "module_4" else "module_4",
                    flow_status=flow if module != "module_4" else "completed",
                    memory=confirmation_memory(other["memory"]),
                    row_version=rt.c.row_version + 1, last_transition_reason="cycle_updated_in_other_chat"))
            await db.execute(update(Conversation).where(Conversation.id.in_(other_ids)).values(revision=Conversation.revision + 1))
    await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
        current_module=next_module, active_goal_id=next_goal, active_cycle_id=next_cycle, flow_status=flow,
        memory=confirmation_memory(state["memory"]), row_version=state["row_version"] + 1, last_transition_reason="user_confirmed_record"))
    await db.execute(insert(schema.tables["ai_decision_logs"]), {"conversation_id": conversation.id,
        "turn_id": str(boundary_message_id or message.id), "goal_id": goal_id, "cycle_id": cycle_id, "module_name": module,
        "decision_type": "user_confirmation", "decision_value": {"record_id": pending["id"],
            "confirmed_snapshot_hash": snapshot_hash, "next_module": next_module,
            "next_cycle_id": next_cycle, "source": source},
        "evidence_message_ids": [message.id]})
    conversation.revision += 1

    return next_module, next_cycle
