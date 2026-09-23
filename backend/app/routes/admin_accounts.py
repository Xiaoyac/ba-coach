"""Administrator-only account directory and role delegation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..account_identity import audit_identity, normalize_display_name, split_display_id
from ..db import get_db
from ..config import get_settings
from ..identity import CallerIdentity, require_admin
from ..models import (
    AccountHandle,
    AccountSettings,
    AIExecutionEvent,
    AssessmentEntry,
    ActivityLog,
    Conversation,
    ConversationMessage,
    ConversationModuleProgress,
    ConversationRuntimeState,
    ProfileExtension,
    UserAccount,
)
from ..database_v2_schema import metadata as v2_schema
from ..graph import wait_for_pending_routing
from ..session import SessionStore, get_session_store
from ..models_business import (
    InteractionStatus,
    ModuleFourRecord,
    ModuleOneRecord,
    ModuleThreeRecord,
    ModuleTwoRecord,
    RiskMonitoring,
    UserProfile,
)
from ..push_schema import metadata as push_schema
from ..schemas import AdminAccountItem, AdminAccountList, AdminRoleGrant

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/accounts", tags=["admin-accounts"])


def _item(row) -> AdminAccountItem:
    account, handle, settings, nickname = row
    return AdminAccountItem(
        id=account.id,
        username=account.username,
        nickname=nickname,
        tag=handle.tag if handle else None,
        display_id=handle.full_username if handle else None,
        role=settings.role if settings else "user",
        created_at=account.created_at,
        last_login_at=account.last_login_at,
    )


def _directory_query():
    return (
        select(UserAccount, AccountHandle, AccountSettings, UserProfile.nickname)
        .outerjoin(AccountHandle, AccountHandle.account_id == UserAccount.id)
        .outerjoin(AccountSettings, AccountSettings.account_id == UserAccount.id)
        .outerjoin(UserProfile, UserProfile.uuid == UserAccount.profile_uuid)
    )


@router.get("", response_model=AdminAccountList)
async def list_admin_accounts(
    query: str | None = Query(default=None, max_length=64),
    _caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAccountList:
    statement = _directory_query()
    if query and query.strip():
        parsed = split_display_id(query)
        if parsed is not None:
            normalized_base, tag = parsed
            statement = statement.where(
                AccountHandle.normalized_base == normalized_base,
                AccountHandle.tag == tag,
            )
        else:
            needle = normalize_display_name(query)
            statement = statement.where(
                or_(
                    UserAccount.username.contains(needle, autoescape=True),
                    AccountHandle.normalized_base.contains(needle, autoescape=True),
                    AccountHandle.tag.contains(needle, autoescape=True),
                    UserProfile.nickname.contains(query.strip(), autoescape=True),
                )
            )
    rows = (
        await db.execute(statement.order_by(UserAccount.created_at.desc()).limit(100))
    ).all()
    return AdminAccountList(accounts=[_item(row) for row in rows])


@router.patch("/{account_id}/role", response_model=AdminAccountItem)
async def grant_admin_role(
    account_id: int,
    _payload: AdminRoleGrant,
    caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAccountItem:
    target = (
        await db.execute(select(UserAccount).where(UserAccount.id == account_id))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="找不到该账号"
        )

    settings = (
        await db.execute(
            select(AccountSettings).where(AccountSettings.account_id == target.id)
        )
    ).scalar_one_or_none()
    if settings is None:
        settings = AccountSettings(account_id=target.id, role="admin")
        db.add(settings)
    else:
        settings.role = "admin"
    await db.commit()

    actor_name = await audit_identity(db, caller.account)
    target_name = await audit_identity(db, target)
    logger.warning("administrator %s granted admin role to %s", actor_name, target_name)

    row = (await db.execute(_directory_query().where(UserAccount.id == target.id))).one()
    return _item(row)


async def _purge_account_business_data(db: AsyncSession, *, profile_uuid: str) -> None:
    """Delete all subject-scoped V2/business rows before the login account.

    ``user_accounts.profile_uuid`` intentionally has no FK to the externally
    owned business schema.  Deleting only the login row would therefore leave
    conversations, targets and clinical records behind.  IDs are collected
    first so the child tables can be deleted in FK order, including goals that
    were shared between chats owned by the same account.
    """
    tables = v2_schema.tables
    if get_settings().database_schema_version != "v2":
        # Local/legacy installations do not have the V2 tables.  Keep the
        # same deletion contract there using the externally-owned ORM models.
        conversation_ids = list((await db.execute(select(Conversation.id).where(
            Conversation.subject_id == profile_uuid))).scalars())
        entry_ids = list((await db.execute(select(AssessmentEntry.id).where(
            AssessmentEntry.subject_id == profile_uuid))).scalars())
        telemetry_filters = [AIExecutionEvent.subject_id == profile_uuid]
        if conversation_ids:
            telemetry_filters.append(AIExecutionEvent.conversation_id.in_(conversation_ids))
        await db.execute(delete(AIExecutionEvent).where(or_(*telemetry_filters)))
        if conversation_ids:
            await db.execute(delete(ConversationModuleProgress).where(
                ConversationModuleProgress.conversation_id.in_(conversation_ids)))
            await db.execute(delete(ConversationRuntimeState).where(
                ConversationRuntimeState.conversation_id.in_(conversation_ids)))
            await db.execute(delete(ConversationMessage).where(
                ConversationMessage.conversation_id.in_(conversation_ids)))
            await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
        for model in (ModuleFourRecord, ModuleThreeRecord, ModuleTwoRecord,
                      ModuleOneRecord, InteractionStatus, RiskMonitoring):
            await db.execute(delete(model).where(model.user_id == profile_uuid))
        if entry_ids:
            await db.execute(delete(ActivityLog).where(ActivityLog.entry_id.in_(entry_ids)))
        await db.execute(delete(AssessmentEntry).where(AssessmentEntry.subject_id == profile_uuid))
        await db.execute(delete(ProfileExtension).where(ProfileExtension.profile_uuid == profile_uuid))
        await db.execute(delete(UserProfile).where(UserProfile.uuid == profile_uuid))
        return
    goal_ids = list((await db.execute(select(tables["pa_goals"].c.id).where(
        tables["pa_goals"].c.user_id == profile_uuid))).scalars())
    cycle_ids = list((await db.execute(select(tables["pa_cycles"].c.id).where(
        tables["pa_cycles"].c.goal_id.in_(goal_ids)))).scalars()) if goal_ids else []
    plan_ids = list((await db.execute(select(tables["module_two_record"].c.id).where(
        tables["module_two_record"].c.goal_id.in_(goal_ids)))).scalars()) if goal_ids else []
    review_ids = list((await db.execute(select(tables["module_four_record"].c.id).where(
        tables["module_four_record"].c.cycle_id.in_(cycle_ids)))).scalars()) if cycle_ids else []

    conversations = list((await db.execute(select(Conversation.id, Conversation.session_id).where(
        Conversation.subject_id == profile_uuid))).all())
    conversation_ids = [row.id for row in conversations]
    message_ids = list((await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id.in_(conversation_ids)))).scalars()) if conversation_ids else []
    entry_ids = list((await db.execute(select(AssessmentEntry.id).where(
        AssessmentEntry.subject_id == profile_uuid))).scalars())

    # Logs and review details point at rows that are removed below.  Remove
    # them first; telemetry is account-scoped and must not outlive the account.
    logs = tables["ai_decision_logs"]
    log_filters = []
    if conversation_ids:
        log_filters.append(logs.c.conversation_id.in_(conversation_ids))
    if goal_ids:
        log_filters.append(logs.c.goal_id.in_(goal_ids))
    if cycle_ids:
        log_filters.append(logs.c.cycle_id.in_(cycle_ids))
    if log_filters:
        await db.execute(delete(logs).where(or_(*log_filters)))
    review_details = tables["pa_review_details"]
    review_filters = []
    if review_ids:
        review_filters.append(review_details.c.review_id.in_(review_ids))
    if message_ids:
        review_filters.append(review_details.c.source_message_id.in_(message_ids))
    if review_filters:
        await db.execute(delete(review_details).where(or_(*review_filters)))

    telemetry_filters = [AIExecutionEvent.subject_id == profile_uuid]
    if conversation_ids:
        telemetry_filters.append(AIExecutionEvent.conversation_id.in_(conversation_ids))
    if message_ids:
        telemetry_filters.append(AIExecutionEvent.assistant_message_id.in_(message_ids))
    if conversations:
        telemetry_filters.append(AIExecutionEvent.session_id.in_([row.session_id for row in conversations]))
    await db.execute(delete(AIExecutionEvent).where(or_(*telemetry_filters)))
    if conversation_ids:
        await db.execute(delete(ConversationModuleProgress).where(
            ConversationModuleProgress.conversation_id.in_(conversation_ids)))
        await db.execute(delete(ConversationRuntimeState).where(
            ConversationRuntimeState.conversation_id.in_(conversation_ids)))
        await db.execute(delete(ConversationMessage).where(
            ConversationMessage.conversation_id.in_(conversation_ids)))
        await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))

    if review_ids:
        await db.execute(delete(tables["module_four_record"]).where(
            tables["module_four_record"].c.id.in_(review_ids)))
    if cycle_ids:
        await db.execute(delete(tables["pa_cycle_progress"]).where(
            tables["pa_cycle_progress"].c.cycle_id.in_(cycle_ids)))
        await db.execute(delete(tables["clinical_record_cycle_links"]).where(
            tables["clinical_record_cycle_links"].c.cycle_id.in_(cycle_ids)))
    if plan_ids:
        await db.execute(delete(tables["pa_plan_details"]).where(
            tables["pa_plan_details"].c.plan_id.in_(plan_ids)))
    if goal_ids:
        await db.execute(delete(tables["module_three_record"]).where(
            tables["module_three_record"].c.goal_id.in_(goal_ids)))
        await db.execute(delete(tables["module_two_record"]).where(
            tables["module_two_record"].c.goal_id.in_(goal_ids)))
        await db.execute(delete(tables["pa_goal_details"]).where(
            tables["pa_goal_details"].c.goal_id.in_(goal_ids)))
        await db.execute(delete(tables["pa_activity_events"]).where(
            or_(tables["pa_activity_events"].c.user_id == profile_uuid,
                tables["pa_activity_events"].c.goal_id.in_(goal_ids))))
        # Self-referential goal links must be cleared before deleting rows.
        await db.execute(update(tables["pa_goals"]).where(
            tables["pa_goals"].c.id.in_(goal_ids)).values(
                replaced_by_goal_id=None, current_plan_record_id=None,
                module_one_record_id=None))
        await db.execute(delete(tables["pa_cycles"]).where(
            tables["pa_cycles"].c.id.in_(cycle_ids)))
        await db.execute(delete(tables["pa_goals"]).where(
            tables["pa_goals"].c.id.in_(goal_ids)))

    await db.execute(delete(tables["pa_activity_events"]).where(
        tables["pa_activity_events"].c.user_id == profile_uuid))
    for name in ("ba_memory", "user_activity_constraints"):
        table = tables[name]
        await db.execute(update(table).where(table.c.user_id == profile_uuid).values(
            supersedes_id=None))

    for name in ("user_activity_constraints", "user_preferences",
                 "user_supporters", "ba_memory",
                 "user_module_one_state", "module_one_record"):
        table = tables[name]
        await db.execute(delete(table).where(table.c.user_id == profile_uuid))
    await db.execute(delete(InteractionStatus).where(InteractionStatus.user_id == profile_uuid))
    await db.execute(delete(RiskMonitoring).where(RiskMonitoring.user_id == profile_uuid))
    for table in (push_schema.tables["pa_push_devices"],
                  push_schema.tables["pa_push_deliveries"],
                  push_schema.tables["pa_push_checks"]):
        await db.execute(delete(table).where(table.c.user_id == profile_uuid))
    if entry_ids:
        await db.execute(delete(ActivityLog).where(ActivityLog.entry_id.in_(entry_ids)))
    await db.execute(delete(AssessmentEntry).where(AssessmentEntry.subject_id == profile_uuid))
    await db.execute(delete(ProfileExtension).where(ProfileExtension.profile_uuid == profile_uuid))
    # user_profile is owned by the legacy business schema, but this account is
    # its sole identity owner. Remove the profile after all subject rows.
    await db.execute(delete(tables["user_profile"]).where(
        tables["user_profile"].c.uuid == profile_uuid))


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_account(
    account_id: int,
    caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    store: SessionStore = Depends(get_session_store),
) -> None:
    """Permanently remove another account and its subject-scoped records."""
    target = (await db.execute(select(UserAccount).where(
        UserAccount.id == account_id).with_for_update())).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到该账号")
    if target.id == caller.account.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能删除当前登录的管理员账号")

    # A delayed router can otherwise append a message after the purge. Wait
    # for each known session, then reset its in-memory copy after the commit.
    sessions = list((await db.execute(select(Conversation.session_id).where(
        Conversation.subject_id == target.profile_uuid))).scalars())
    for session_id in sessions:
        await wait_for_pending_routing(session_id)

    actor_name = await audit_identity(db, caller.account)
    target_name = await audit_identity(db, target)
    await _purge_account_business_data(db, profile_uuid=target.profile_uuid)
    await db.delete(target)
    await db.commit()
    for session_id in sessions:
        await store.reset(session_id)
    logger.warning("administrator %s permanently deleted account %s", actor_name, target_name)
