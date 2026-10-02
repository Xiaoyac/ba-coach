"""Conversation-scoped routing policy, authorized from durable account roles."""
from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import insert, select, update

ROUTER_CODE = "router_code"
ROUTER_ONLY = "router_only"
MODULES = frozenset(f"module_{n}" for n in range(1, 5))


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


async def apply_router_only_decision(state, context, decision):
    """Commit only the conversational module, never business confirmations.

    A router decision is bound to the exact version and user-message boundary
    supplied to the model. Ownership, current role, mode and freshness are
    infrastructure checks; no completion/plan/step predicate can veto a valid
    module here. A failed or malformed model result simply keeps this module.
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
        target = decision.target_module if accepted else current
        memory = dict(actual["memory"] or {})
        # A mode change does not confirm or revoke any plan, recording contract
        # or review. Preserve their data and flow status exactly as persisted.
        await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
            current_module=target, last_transition_reason="router_only_selected" if accepted else "router_only_hold",
            row_version=actual["row_version"] + 1, updated_at=now()))
        conversation.revision += 1
        await db.execute(insert(schema.tables["ai_decision_logs"]), {
            "conversation_id": conversation.id, "turn_id": str(boundary),
            "goal_id": actual["active_goal_id"], "cycle_id": actual["active_cycle_id"],
            "module_name": current, "decision_type": "router_only_decision",
            "decision_value": {"routing_mode": ROUTER_ONLY, "from_module": current,
                "proposed_module": decision.target_module, "applied_module": target,
                "source_row_version": actual["row_version"], "error_code": decision.error_code,
                "business_confirmation_performed": False},
            "evidence_message_ids": [boundary],
        })
        await db.commit()
    await context.store.set_module(session_id, target)
    await context.store.set_memory(session_id, memory)
    clinical = await clinical_context(context.sessionmaker, user_id=user_id, session_id=session_id)
    steps, loaded_cycle = await load_workflow(context.sessionmaker, session_id)
    return {"current_module": target, "active_cycle_id": loaded_cycle,
        "routing_mode": ROUTER_ONLY, "memory": memory, "clinical_context": clinical, "module_steps": steps,
        "diagnostics": {"policy": ROUTER_ONLY, "block_reasons": []}}
