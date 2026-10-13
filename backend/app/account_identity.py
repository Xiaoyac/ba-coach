"""Independent, stable login identities for authentication and auditing."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserAccount


def normalize_account(username: str) -> str:
    """Login accounts are ASCII and case-insensitive."""
    return username.strip().casefold()


async def find_account_by_username(
    db: AsyncSession, username: str
) -> UserAccount | None:
    """Resolve only the independent login account — never a nickname."""
    return (
        await db.execute(
            select(UserAccount).where(
                UserAccount.username == normalize_account(username)
            )
        )
    ).scalar_one_or_none()


async def audit_identity(db: AsyncSession, account: UserAccount) -> str:
    """Use the unique login account; nicknames can change or be shared."""
    return account.username
