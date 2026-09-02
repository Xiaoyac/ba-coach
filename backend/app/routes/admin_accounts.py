"""Administrator-only account directory and role delegation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..account_identity import audit_identity, normalize_display_name, split_display_id
from ..db import get_db
from ..identity import CallerIdentity, require_admin
from ..models import AccountHandle, AccountSettings, UserAccount
from ..models_business import UserProfile
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
