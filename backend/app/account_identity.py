"""Independent login accounts and user-chosen nickname display IDs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AccountHandle, UserAccount


def normalize_account(username: str) -> str:
    """Login accounts are ASCII and case-insensitive."""
    return username.strip().casefold()


def normalize_display_name(nickname: str) -> str:
    """Nickname uniqueness is case-insensitive but preserves display casing."""
    return nickname.strip().casefold()


def split_display_id(value: str) -> tuple[str, str] | None:
    """Parse ``nickname#12345`` for administrator directory search only."""
    nickname, separator, tag = value.strip().rpartition("#")
    if not separator or not nickname or len(tag) != 5 or not tag.isascii() or not tag.isdigit():
        return None
    return normalize_display_name(nickname), tag


async def find_account_by_username(
    db: AsyncSession, username: str
) -> UserAccount | None:
    """Resolve only the independent login account — never nickname or tag."""
    return (
        await db.execute(
            select(UserAccount).where(
                UserAccount.username == normalize_account(username)
            )
        )
    ).scalar_one_or_none()


async def display_id(db: AsyncSession, account: UserAccount) -> str | None:
    """Return ``nickname#tag`` or None for a grandfathered untagged account."""
    handle = (
        await db.execute(
            select(AccountHandle).where(AccountHandle.account_id == account.id)
        )
    ).scalar_one_or_none()
    return handle.full_username if handle else None


async def audit_identity(db: AsyncSession, account: UserAccount) -> str:
    """Prefer the public display ID in audit logs, falling back to login account."""
    return await display_id(db, account) or account.username
