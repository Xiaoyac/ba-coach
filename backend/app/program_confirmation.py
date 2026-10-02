"""Shared transaction-level confirmation; callers own authentication, locking and commit."""
import hashlib
import json
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select, update, insert
from .database_v2_schema import metadata as schema
from .models import ConversationMessage
from .v2_repository import now, confirm_plan, V2Conflict
from .v2_workflow import TABLES, module_extraction_is_current
from .workflow_contract import MODULE_STEP_KEYS


def record_hash(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def confirmation_memory(memory):
    """Invalidate operation markers without discarding durable dialogue facts."""
    transient = {"dialogue_draft", "module_extraction_freshness", "pa_card",
        "current_transition_evidence", "last_module", "current_module", "next_module",
        "phase", "current_phase", "current_step", "module_steps", "fresh_m1"}
    return {key:value for key,value in (memory or {}).items() if key not in transient}


async def draft(db, state, user_id):
    table = schema.tables[TABLES[state["current_module"]]]
    if state["current_module"] == "module_1":
        from .v2_workflow import m1_draft
        return await m1_draft(db, user_id, state=state)
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
    from .workflow_readiness import CONFIRMATION_WINDOW_CLOSED
    if module in {"module_2", "module_4"} and state.get("last_transition_reason") != "awaiting_record_confirmation":
        readiness["ready"] = False
        readiness["reasons"].append({
            "code": CONFIRMATION_WINDOW_CLOSED,
            "message": "当前模块尚未进入可确认状态",
        })
    review_action = None
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
            raise HTTPException(409, "本轮执行、ABC核对、BA理解或复盘收尾的证据尚未完整，请继续讨论")
    review_action = None
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
            completed_steps=list(MODULE_STEP_KEYS["module_1"]),
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
            status="completed", completed_at=now(), updated_at=now()))
        # Close only this attempt. M2 owns the user's next-goal choice.
        next_goal, next_cycle, next_module = None, None, "module_2"
        from .models_business import InteractionStatus
        interaction = (await db.execute(select(InteractionStatus).where(InteractionStatus.user_id == user_id))).scalar_one_or_none()
        if not interaction:
            interaction = InteractionStatus(user_id=user_id)
            db.add(interaction)
        interaction.full_m2_m3_m4_cycle_count = int(interaction.full_m2_m3_m4_cycle_count or 0) + 1
        interaction.has_entered_closure_or_transition = True
    next_memory = confirmation_memory(state["memory"])
    if module == "module_4":
        from .m4_contract import contract_for
        next_memory["last_reviewed_cycle"] = {
            "goal_id": goal_id, "cycle_id": cycle_id,
            "review_id": pending["id"],
            "after_message_id": ((contract_for(pending).get("evidence") or {}).get("review_summary_quote") or {}).get("message_id")
                or contract_for(pending).get("assistant_message_id"),
        }
    if cycle_id:
        # Other chats may be continuing this SAME cycle. They must not
        # keep stale module pointers or advance a completed cycle.
        from .models import Conversation
        others = select(rt.c.conversation_id, rt.c.memory, Conversation.subject_id).join(Conversation, Conversation.id == rt.c.conversation_id).where(
            rt.c.active_cycle_id == cycle_id, Conversation.subject_id == user_id,
            rt.c.conversation_id != conversation.id).with_for_update()
        other_rows = (await db.execute(others)).mappings().all()
        other_ids = []
        from .routing_modes import effective_routing_mode, ROUTER_ONLY
        for other in other_rows:
            owned = SimpleNamespace(id=other["conversation_id"], subject_id=other["subject_id"])
            if await effective_routing_mode(db, conversation=owned, state=other, user_id=user_id, lock=True) == ROUTER_ONLY:
                continue  # Shared business facts do not own this chat's Router stage.
            other_ids.append(other["conversation_id"])
            await db.execute(update(rt).where(rt.c.conversation_id == other["conversation_id"]).values(
                current_module=next_module if module != "module_4" else "module_4",
                flow_status=flow if module != "module_4" else "completed",
                memory=confirmation_memory(other["memory"]),
                row_version=rt.c.row_version + 1, last_transition_reason="cycle_updated_in_other_chat"))
        if other_ids:
            await db.execute(update(Conversation).where(Conversation.id.in_(other_ids)).values(revision=Conversation.revision + 1))
    await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
        current_module=next_module, active_goal_id=next_goal, active_cycle_id=next_cycle, flow_status=flow,
        memory=next_memory, row_version=state["row_version"] + 1, last_transition_reason="user_confirmed_record"))
    await db.execute(insert(schema.tables["ai_decision_logs"]), {"conversation_id": conversation.id,
        "turn_id": str(boundary_message_id or message.id), "goal_id": goal_id, "cycle_id": cycle_id, "module_name": module,
        "decision_type": "user_confirmation", "decision_value": {"record_id": pending["id"],
            "confirmed_snapshot_hash": snapshot_hash, "next_module": next_module,
            "next_cycle_id": next_cycle, "source": source},
        "evidence_message_ids": [message.id]})
    conversation.revision += 1

    return next_module, next_cycle
