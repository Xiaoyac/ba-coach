"""Durable conversation workflow, PA-cycle links, and interaction analytics.

The externally-owned business tables cannot be altered.  This module therefore
keeps conversation-scoped state in app-owned sidecar tables and treats
``ConversationRuntimeState.module`` as the only authoritative module pointer.
``user_profile.current_module`` remains a legacy column but is neither read nor
written as runtime state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import (
    ClinicalRecordCycleLink,
    Conversation,
    ConversationMessage,
    ConversationModuleProgress,
    ConversationRuntimeState,
    PACycle,
)
from .models_business import InteractionStatus, UserProfile


from .workflow_contract import MODULE_STEP_KEYS


def normalise_completed_steps(module: str, raw: object) -> list[str]:
    allowed = MODULE_STEP_KEYS.get(module, ())
    values = raw if isinstance(raw, list) else []
    selected = {str(value) for value in values}
    return [key for key in allowed if key in selected]


def required_steps_complete(module: str, completed: list[str]) -> bool:
    return module in MODULE_STEP_KEYS and set(MODULE_STEP_KEYS[module]).issubset(completed)


async def _conversation(db: AsyncSession, session_id: str) -> Conversation | None:
    return (
        await db.execute(select(Conversation).where(Conversation.session_id == session_id))
    ).scalar_one_or_none()


async def _progress(
    db: AsyncSession, conversation_id: int, *, create: bool
) -> ConversationModuleProgress | None:
    row = await db.get(ConversationModuleProgress, conversation_id)
    if row is None and create:
        row = ConversationModuleProgress(conversation_id=conversation_id)
        db.add(row)
        await db.flush()
    return row


async def load_conversation_workflow(
    sessionmaker: async_sessionmaker[AsyncSession], *, session_id: str
) -> tuple[dict[str, list[str]], str | None]:
    """Return explicit completed steps and the active PA cycle."""
    from .v2_profile import enabled
    if enabled():
        from .v2_workflow import load_workflow
        return await load_workflow(sessionmaker, session_id)
    async with sessionmaker() as db:
        conversation = await _conversation(db, session_id)
        if conversation is None:
            return ({module: [] for module in MODULE_STEP_KEYS}, None)
        row = await _progress(db, conversation.id, create=False)
        if row is None:
            return ({module: [] for module in MODULE_STEP_KEYS}, None)
        return (
            {
                module: normalise_completed_steps(module, getattr(row, f"{module}_steps"))
                for module in MODULE_STEP_KEYS
            },
            row.active_cycle_id,
        )


async def _new_cycle(
    db: AsyncSession, *, conversation: Conversation, subject_id: str
) -> PACycle:
    maximum = (
        await db.execute(
            select(func.max(PACycle.ordinal)).where(
                PACycle.conversation_id == conversation.id
            )
        )
    ).scalar_one()
    cycle = PACycle(
        conversation_id=conversation.id,
        subject_id=subject_id,
        ordinal=int(maximum or 0) + 1,
    )
    db.add(cycle)
    await db.flush()
    return cycle


async def apply_router_decision(
    db: AsyncSession,
    *,
    session_id: str,
    subject_id: str,
    current_module: str,
    target_module: str,
    completed_steps: list[str],
) -> str | None:
    """Persist cumulative steps and advance/rotate the conversation PA cycle.

    This intentionally does not commit; the caller publishes it in the same
    transaction as the durable router result.
    """
    conversation = await _conversation(db, session_id)
    if conversation is None or conversation.subject_id != subject_id:
        return None
    allowed_moves = {"module_1": {"module_1","module_2"}, "module_2": {"module_2","module_3"},
                     "module_3": {"module_3","module_4"}, "module_4": {"module_4","module_2"}}
    if target_module not in allowed_moves.get(current_module, set()):
        raise ValueError("Invalid workflow transition")
    progress = await _progress(db, conversation.id, create=True)
    assert progress is not None

    column = f"{current_module}_steps"
    previous = normalise_completed_steps(current_module, getattr(progress, column))
    merged = normalise_completed_steps(current_module, previous + completed_steps)
    if current_module != target_module and not required_steps_complete(current_module, merged):
        raise ValueError("Cannot advance with incomplete required steps")
    setattr(progress, column, merged)

    starts_first_cycle = current_module == "module_1" and target_module == "module_2"
    starts_next_cycle = current_module == "module_4" and target_module == "module_2"
    needs_legacy_cycle = target_module in {"module_2", "module_3", "module_4"}

    if starts_next_cycle and progress.active_cycle_id:
        old = await db.get(PACycle, progress.active_cycle_id)
        if old is not None:
            old.status = "completed"
            old.closed_at = datetime.now(timezone.utc)
        progress.module_2_steps = []
        progress.module_3_steps = []
        progress.module_4_steps = []
        progress.active_cycle_id = None

    if (starts_first_cycle or starts_next_cycle or needs_legacy_cycle) and not progress.active_cycle_id:
        cycle = await _new_cycle(db, conversation=conversation, subject_id=subject_id)
        progress.active_cycle_id = cycle.id

    profile = (
        await db.execute(select(UserProfile).where(UserProfile.uuid == subject_id))
    ).scalar_one_or_none()
    if profile is not None and current_module == "module_1" and target_module == "module_2":
        # The only user-level workflow fact: module one has ever been passed.
        # The current module is conversation-level and is never projected here.
        profile.module1_done_flag = True

    return progress.active_cycle_id


async def link_cycle_record(
    db: AsyncSession, *, cycle_id: str, module: str, record_id: str
) -> None:
    existing = (
        await db.execute(
            select(ClinicalRecordCycleLink).where(
                ClinicalRecordCycleLink.cycle_id == cycle_id,
                ClinicalRecordCycleLink.module == module,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            ClinicalRecordCycleLink(
                cycle_id=cycle_id, module=module, record_id=record_id
            )
        )


async def linked_record_id(
    db: AsyncSession, *, cycle_id: str, module: str
) -> str | None:
    return (
        await db.execute(
            select(ClinicalRecordCycleLink.record_id).where(
                ClinicalRecordCycleLink.cycle_id == cycle_id,
                ClinicalRecordCycleLink.module == module,
            )
        )
    ).scalar_one_or_none()


async def current_cycle_for_session(
    db: AsyncSession, *, session_id: str
) -> str | None:
    from .v2_profile import enabled
    if enabled():
        from .v2_workflow import current_cycle
        return await current_cycle(db, session_id)
    conversation = await _conversation(db, session_id)
    if conversation is None:
        return None
    progress = await _progress(db, conversation.id, create=False)
    return progress.active_cycle_id if progress else None


async def record_interaction_turn(
    db: AsyncSession, *, conversation: Conversation, module: str | None
) -> None:
    """Update deterministic fields in the externally-owned analytics row."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = (
        await db.execute(
            select(InteractionStatus).where(
                InteractionStatus.user_id == conversation.subject_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = InteractionStatus(user_id=conversation.subject_id)
        db.add(row)
        await db.flush()

    row.last_active_at = now
    row.consecutive_inactive_days = 0
    row.total_interaction_count = int(row.total_interaction_count or 0) + 1

    counts = dict(row.easy_stuck_modules or {})
    if module:
        counts[module] = int(counts.get(module, 0)) + 1
        row.easy_stuck_modules = counts
        row.longest_stay_module = max(counts, key=counts.get)

    first_user_at = (
        await db.execute(
            select(func.min(ConversationMessage.created_at))
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(
                Conversation.subject_id == conversation.subject_id,
                ConversationMessage.role == "user",
            )
        )
    ).scalar_one()
    if first_user_at:
        first = first_user_at.replace(tzinfo=None) if first_user_at.tzinfo else first_user_at
        weeks = max((now - first).total_seconds() / (7 * 86400), 1.0)
        row.avg_weekly_interaction_frequency = Decimal(
            str(round(row.total_interaction_count / weeks, 2))
        )


async def record_interaction_transition(
    db: AsyncSession,
    *,
    subject_id: str,
    current_module: str,
    target_module: str,
) -> None:
    row = (
        await db.execute(
            select(InteractionStatus).where(InteractionStatus.user_id == subject_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = InteractionStatus(user_id=subject_id)
        db.add(row)
        await db.flush()
    if current_module == "module_4" and target_module == "module_2":
        row.full_m2_m3_m4_cycle_count = int(row.full_m2_m3_m4_cycle_count or 0) + 1
        row.has_entered_closure_or_transition = True


async def derived_current_module(db: AsyncSession, *, subject_id: str) -> str | None:
    """Latest conversation state for profile display; never a second source."""
    module = (
        await db.execute(
            select(ConversationRuntimeState.module)
            .join(Conversation, Conversation.id == ConversationRuntimeState.conversation_id)
            .where(Conversation.subject_id == subject_id)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if module is None:
        from .v2_profile import enabled
        if enabled():
            return None
        return (
            await db.execute(
                select(UserProfile.current_module).where(UserProfile.uuid == subject_id)
            )
        ).scalar_one_or_none()
    return {
        "module_1": "模块一",
        "module_2": "模块二",
        "module_3": "模块三",
        "module_4": "模块四",
    }.get(module, module)
