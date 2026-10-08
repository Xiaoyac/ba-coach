"""Conversation-scoped routing policy, authorized from durable account roles."""
from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import insert, select, update

ROUTER_CODE = "router_code"
ROUTER_ONLY = "router_only"
MODULES = frozenset(f"module_{n}" for n in range(1, 5))
EXECUTION_MODULES = frozenset({"module_3", "module_4"})


def configured_routing_mode(memory):
    """Normalize a stored preference. This helper does not authorize a bypass."""
    return ROUTER_ONLY if isinstance(memory, Mapping) and memory.get("routing_mode") == ROUTER_ONLY else ROUTER_CODE


async def effective_routing_mode(db, *, conversation, state, user_id, lock=False):
    """Call with an owned conversation and its freshly read runtime memory.

    Neither request metadata nor an in-memory routing flag grants permission.
    Members use the server's router-only policy; admins retain their saved mode.
    """
    if (conversation is None or conversation.subject_id != user_id or not isinstance(state, Mapping)
            or state.get("conversation_id", conversation.id) != conversation.id):
        return ROUTER_CODE
    memory = state.get("memory") or {}
    if memory.get("sandbox_mode") is True or str(memory.get("sandbox_mode")).lower() == "true":
        return ROUTER_CODE
    from .models import AccountSettings, UserAccount
    query = select(AccountSettings.role).join(UserAccount, UserAccount.id == AccountSettings.account_id).where(
        UserAccount.profile_uuid == user_id)
    if lock:
        query = query.with_for_update()
    role = await db.scalar(query)
    if role == "user":
        return ROUTER_ONLY
    return ROUTER_ONLY if role == "admin" and configured_routing_mode(memory) == ROUTER_ONLY else ROUTER_CODE


async def execution_plan_binding(db, *, conversation, state, user_id, lock=False):
    """Validate the current durable binding, never search other chats for a card.

    A deliberately bound, same-user continued cycle may have been confirmed in
    another conversation. Its original confirmation remains the source; this
    check neither copies the plan nor manufactures another confirmation.
    """
    from .database_v2_schema import metadata as schema
    from .models import Conversation, ConversationMessage
    from .pa_lifecycle import OPEN_CYCLES

    def blocked(code, reason):
        return {"ready": False, "reason_code": code, "reason": reason}

    async def row(table, *where):
        query = select(table).where(*where)
        if lock:
            query = query.with_for_update()
        return (await db.execute(query)).mappings().one_or_none()

    if (not conversation or conversation.subject_id != user_id or not state
            or state.get("conversation_id") != conversation.id):
        return blocked("conversation_not_owned", "无法核实本会话的目标绑定")
    if state.get("flow_status") in {"completed", "paused"}:
        return blocked("flow_not_active", "当前会话的执行流程已结束或暂停")
    if not state.get("active_goal_id") or not state.get("active_cycle_id"):
        return blocked("execution_binding_missing", "本会话尚未绑定当前目标与执行周期")
    goals, cycles, plans = (schema.tables[name] for name in (
        "pa_goals", "pa_cycles", "module_two_record"))
    from .goal_scope import goal_filter
    goal = await row(goals, goals.c.id == state["active_goal_id"], await goal_filter(db, user_id, conversation.id))
    if not goal:
        return blocked("goal_not_owned", "本会话绑定的目标不存在或不属于当前用户")
    if goal["status"] != "active":
        return blocked("goal_not_active", "当前绑定目标尚未确认，或已暂停、撤回、结束或被替换")
    cycle = await row(cycles, cycles.c.id == state["active_cycle_id"], cycles.c.goal_id == goal["id"])
    if not cycle:
        return blocked("cycle_not_bound", "当前执行周期未绑定本会话的目标")
    if cycle["status"] not in OPEN_CYCLES:
        return blocked("cycle_not_open", "当前执行周期已结束或撤回，历史计划不能作为本轮执行计划")
    if not cycle["module_two_record_id"]:
        return blocked("cycle_plan_missing", "当前执行周期尚未绑定已确认计划")
    if cycle["module_two_record_id"] != goal["current_plan_record_id"]:
        return blocked("plan_not_current", "当前周期绑定的计划不是该目标的有效确认版本")
    plan = await row(plans, plans.c.id == cycle["module_two_record_id"], plans.c.goal_id == goal["id"])
    if (not plan or plan["record_status"] != "confirmed"
            or plan["confirmation_status"] != "confirmed"):
        return blocked("plan_not_confirmed", "当前周期的计划尚未真实确认或已被后续版本替代")
    confirmation = select(ConversationMessage.id).join(Conversation,
        Conversation.id == ConversationMessage.conversation_id).where(
        ConversationMessage.id == plan["confirmation_message_id"],
        ConversationMessage.role == "user", Conversation.subject_id == user_id)
    if lock:
        confirmation = confirmation.with_for_update()
    if await db.scalar(confirmation) is None:
        return blocked("confirmation_source_invalid", "当前计划缺少属于本用户的真实确认消息来源")
    # Past scheduled times are valid inputs to a review. Expiry here means a
    # closed cycle, revoked goal or superseded plan, never a date comparison.
    return {"ready": True, "goal_id": goal["id"], "cycle_id": cycle["id"],
        "plan_id": plan["id"], "confirmation_message_id": plan["confirmation_message_id"],
        "reason_code": None, "reason": None}


def execution_binding_context(binding):
    """Keep an operational missing binding distinct from missing user answers."""
    return ("本轮执行计划核验未通过：" + binding["reason"]
            + "。尚未进入记录执行或复盘阶段；模块变化不等于确认、保存或执行。"
            "这不代表用户没有表达，不要重复索要已有信息；沿现有对话和真实卡片核对待保存或确认的事项，"
            "不得借用未绑定的其他会话或历史周期计划，也不得声称已经自动确认。")


async def apply_router_only_decision(state, context, decision, *, stage_owned_by_tools=False):
    """Commit only the conversational module, never business confirmations.

    A router decision is bound to the exact version and user-message boundary
    supplied to the model. Ownership, current role, mode and freshness are
    infrastructure checks. M3/M4 additionally require this conversation's
    current, owned, source-confirmed execution plan; no step/completion gate
    is added. Native card tools retain their existing stage-commit authority.
    """
    from .database_v2_schema import metadata as schema
    from .models import Conversation, ConversationMessage
    from .v2_repository import now
    from .v2_workflow import clinical_context, load_workflow

    user_id, session_id = state["subject_id"], state["session_id"]
    boundary = state.get("user_message_id")
    expected = state.get("routing_state") or {}
    if (context.settings.database_schema_version != "v2" or context.sessionmaker is None
            or not user_id or type(boundary) is not int or type(expected.get("row_version")) is not int):
        raise ValueError("router_only_durable_boundary_required")
    async with context.sessionmaker() as db:
        profiles, rt = schema.tables["user_profile"], schema.tables["conversation_runtime_states"]
        profile = await db.scalar(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
        if profile is None:
            raise ValueError("router_only_user_unavailable")
        conversation = (await db.execute(select(Conversation).where(
            Conversation.session_id == session_id, Conversation.subject_id == user_id)
            .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if conversation is None:
            raise ValueError("router_only_conversation_not_owned")
        actual = (await db.execute(select(rt).where(rt.c.conversation_id == conversation.id)
            .with_for_update())).mappings().one_or_none()
        if await effective_routing_mode(db, conversation=conversation, state=actual, user_id=user_id, lock=True) != ROUTER_ONLY:
            raise ValueError("router_only_mode_not_authorized")
        latest = (await db.execute(select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id).order_by(
            ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1)
            .with_for_update())).scalar_one_or_none()
        if (not latest or latest.id != boundary or latest.role != "user"
                or any(actual[key] != expected.get(key) for key in (
                    "current_module", "row_version", "active_goal_id", "active_cycle_id"))):
            raise ValueError("router_only_decision_superseded")
        current = actual["current_module"]
        accepted = not decision.error_code and decision.target_module in MODULES
        target = decision.target_module if accepted and not stage_owned_by_tools else current
        binding = await execution_plan_binding(db, conversation=conversation, state=actual,
            user_id=user_id, lock=True)
        blocked = (target in EXECUTION_MODULES or
            (accepted and decision.target_module in EXECUTION_MODULES)) and not binding["ready"]
        if blocked:
            # An erroneous proposal cannot push an unfinished M1 into M2.
            # Repair only an already-invalid execution/review stage.
            target = "module_2" if current in EXECUTION_MODULES else current
        reasons = [binding["reason"]] if blocked else []
        memory = dict(actual["memory"] or {})
        # A mode change does not confirm or revoke any plan, recording contract
        # or review. Preserve their data and flow status exactly as persisted.
        if not stage_owned_by_tools or target != current:
            await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
                current_module=target,
                last_transition_reason=("router_only_execution_binding_blocked" if blocked else
                    "router_only_selected" if accepted else "router_only_hold"),
                row_version=actual["row_version"] + 1, updated_at=now()))
            conversation.revision += 1
            await db.execute(insert(schema.tables["ai_decision_logs"]), {
                "conversation_id": conversation.id, "turn_id": str(boundary),
                "goal_id": actual["active_goal_id"], "cycle_id": actual["active_cycle_id"],
                "module_name": current, "decision_type": "router_only_decision",
                "decision_value": {"routing_mode": ROUTER_ONLY, "from_module": current,
                    "proposed_module": decision.target_module, "applied_module": target,
                    "source_row_version": actual["row_version"], "error_code": decision.error_code,
                    "business_confirmation_performed": False,
                    "execution_plan_binding": binding, "block_reasons": reasons,
                    "stage_owned_by_tools": stage_owned_by_tools},
                "evidence_message_ids": [boundary],
            })
        await db.commit()
    await context.store.set_module(session_id, target)
    await context.store.set_memory(session_id, memory)
    clinical = ([execution_binding_context(binding)] if blocked else
        await clinical_context(context.sessionmaker, user_id=user_id, session_id=session_id))
    steps, loaded_cycle = await load_workflow(context.sessionmaker, session_id)
    return {"current_module": target, "active_cycle_id": loaded_cycle,
        "routing_mode": ROUTER_ONLY, "memory": memory, "clinical_context": clinical, "module_steps": steps,
        "diagnostics": {"policy": ROUTER_ONLY, "block_reasons": reasons,
            "execution_plan_binding": binding}}
